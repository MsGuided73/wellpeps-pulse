"""Pulse trends (Phase 8): tokenizer, velocity ranking, share of voice,
sentiment shift, mixes, complaint themes, and the language bank."""

import math
from datetime import timedelta

import pytest

from harvey import trends
from harvey.models import MentionStatus
from tests.pulse_helpers import WINDOW_END, WINDOW_START, add_triaged, fresh_state


# --- Tokenizer ------------------------------------------------------------------------


def test_tokenize_keeps_hyphenated_drug_names_and_drops_noise():
    text = "Loving BPC-157 & GLP-1!! https://x.com/a?b=1 @someone u/redditor r/Peptides 🙂 It's GREAT, 100%"

    tokens = trends.tokenize(text)

    assert "bpc-157" in tokens
    assert "glp-1" in tokens
    assert "loving" in tokens and "great" in tokens
    for noise in ("https", "x.com", "someone", "redditor", "peptides", "100", "🙂", "it's", "it"):
        assert noise not in tokens


def test_tokenize_is_lowercase_and_strips_punctuation():
    assert trends.tokenize("Shipping-DELAY... again?!") == ["shipping-delay", "again"]


def test_stopwords_never_become_terms():
    assert trends.extract_terms("the and is of to a it this") == set()


def test_extract_terms_builds_unigrams_bigrams_and_trigrams():
    terms = trends.extract_terms("Compounded tirz shortage again, the shipping delay is bad")

    assert {"compounded", "tirz", "shortage"} <= terms
    assert "compounded tirz" in terms
    assert "compounded tirz shortage" in terms
    assert "shipping delay" in terms
    # n-grams never span a stopword
    assert "the shipping" not in terms
    assert "delay is" not in terms


def test_platform_noise_is_a_stopword():
    assert trends.extract_terms("edit: deleted post lol reddit comment") == set()


# --- Ranking (pure) ------------------------------------------------------------------------


def _docs(term_counts: dict[str, int], start_id: int = 1) -> list[tuple[int, set[str]]]:
    docs, i = [], start_id
    for term, n in term_counts.items():
        for _ in range(n):
            docs.append((i, {term}))
            i += 1
    return docs


def test_rank_terms_puts_the_spike_first_and_flags_new_terms():
    window = _docs({"price hike": 6, "refill": 5, "oral wegovy": 3, "rare": 2})
    baseline = [d for _, d in _docs({"price hike": 1, "refill": 20})]

    ranked = trends.rank_terms(window, baseline, window_days=7, baseline_days=28, min_count=3)

    names = [t.term for t in ranked]
    assert names[0] in ("price hike", "oral wegovy")
    assert names.index("price hike") < names.index("refill")
    assert "rare" not in names  # below min_count
    by = {t.term: t for t in ranked}
    assert by["oral wegovy"].is_new and not by["price hike"].is_new
    assert by["refill"].velocity == pytest.approx(1.0, abs=0.01)
    expected = (6 / 7 + trends.ALPHA) / (1 / 28 + trends.ALPHA)
    assert by["price hike"].velocity == pytest.approx(expected, rel=1e-6)
    assert by["price hike"].score == pytest.approx(expected * math.log(1 + 6), rel=1e-6)
    assert len(by["price hike"].example_ids) <= 3


def test_rank_terms_respects_top_n():
    window = _docs({f"term{i:02d}x": 3 for i in range(10)})

    assert len(trends.rank_terms(window, [], window_days=7, baseline_days=28, top_n=4)) == 4


def test_rank_terms_drops_a_subterm_that_only_rides_its_phrase():
    window = [(i, {"shipping", "delay", "shipping delay"}) for i in range(1, 5)]

    names = [t.term for t in trends.rank_terms(window, [], window_days=7, baseline_days=28)]

    assert names == ["shipping delay"]


# --- compute_trends over the DB ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_trends_ranks_spike_above_steady_term(tmp_path):
    state = await fresh_state(tmp_path)
    n = 0
    for day in range(1, 28):  # steady "refill" through the baseline
        n += 1
        await add_triaged(state, f"b{n}", text="waiting on my refill again",
                          posted_at=WINDOW_START - timedelta(days=day, hours=1))
    for i in range(6):
        n += 1
        await add_triaged(state, f"w{n}", text="price hike on my plan this month",
                          posted_at=WINDOW_START + timedelta(days=1, hours=i))
    for i in range(2):
        n += 1
        await add_triaged(state, f"w{n}", text="waiting on my refill again",
                          posted_at=WINDOW_START + timedelta(days=2, hours=i))

    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END, baseline_days=28, min_count=2)

    names = [t.term for t in report.terms]
    assert names[0] == "price hike"
    assert report.terms[0].is_new
    assert "refill" in names and names.index("refill") > 0
    assert report.mentions == 8


@pytest.mark.asyncio
async def test_compute_trends_ignores_dropped_irrelevant_and_untriaged(tmp_path):
    state = await fresh_state(tmp_path)
    at = WINDOW_START + timedelta(days=1)
    for i in range(3):
        await add_triaged(state, f"d{i}", text="dropped spam term", posted_at=at, status=MentionStatus.DROPPED)
        await add_triaged(state, f"i{i}", text="irrelevant spam term", posted_at=at, relevant=False)
        await add_triaged(state, f"n{i}", text="untriaged spam term", posted_at=at, status=MentionStatus.NEW)
        await add_triaged(state, f"e{i}", text="escalated. shipping delay", posted_at=at,
                          status=MentionStatus.ESCALATED)

    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END, min_count=3)

    names = {t.term for t in report.terms}
    assert "spam" not in names and "dropped" not in names and "untriaged" not in names
    assert "shipping delay" in names
    assert report.mentions == 3


@pytest.mark.asyncio
async def test_share_of_voice_sums_to_one_and_reports_deltas(tmp_path):
    state = await fresh_state(tmp_path)
    prev = WINDOW_START - timedelta(days=3)
    cur = WINDOW_START + timedelta(days=3)
    rows = [("c1", cur, "Hims & Hers", "competitor"), ("c2", cur, "Hims & Hers", "competitor"),
            ("c3", cur, "Hims & Hers", "competitor"), ("c4", cur, "", "wellpeps"),
            ("c5", cur, "", "category"),  # no subject: not in share of voice
            ("p1", prev, "Hims & Hers", "competitor"), ("p2", prev, "", "wellpeps")]
    for key, at, comp, subject_type in rows:
        await add_triaged(state, key, text="some post", posted_at=at, competitor=comp,
                          subject_type=subject_type)

    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END)

    sov = {r["subject"]: r for r in report.share_of_voice}
    assert set(sov) == {"Hims & Hers", "WellPeps"}
    assert sum(r["share"] for r in report.share_of_voice) == pytest.approx(1.0)
    assert sov["Hims & Hers"]["count"] == 3 and sov["Hims & Hers"]["share"] == pytest.approx(0.75)
    assert sov["Hims & Hers"]["prev_share"] == pytest.approx(0.5)
    assert sov["Hims & Hers"]["delta"] == pytest.approx(0.25)
    assert sov["WellPeps"]["delta"] == pytest.approx(-0.25)


def test_share_of_voice_is_empty_without_subjects():
    assert trends.share_of_voice([], []) == []


@pytest.mark.asyncio
async def test_sentiment_shift_per_subject_and_drug(tmp_path):
    state = await fresh_state(tmp_path)
    prev = WINDOW_START - timedelta(days=2)
    cur = WINDOW_START + timedelta(days=2)
    await add_triaged(state, "a", text="x", posted_at=cur, subject_type="wellpeps", sentiment_score=0.5,
                      drug="semaglutide")
    await add_triaged(state, "b", text="y", posted_at=cur, subject_type="wellpeps", sentiment_score=0.5)
    await add_triaged(state, "c", text="z", posted_at=prev, subject_type="wellpeps", sentiment_score=-0.5,
                      drug="semaglutide")
    await add_triaged(state, "d", text="w", posted_at=cur, competitor="Ro", subject_type="competitor",
                      sentiment_score=-0.8)

    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END)

    by = {(r["kind"], r["subject"]): r for r in report.sentiment}
    wp = by[("wellpeps", "WellPeps")]
    assert wp["mean"] == pytest.approx(0.5) and wp["prev_mean"] == pytest.approx(-0.5)
    assert wp["delta"] == pytest.approx(1.0) and wp["n"] == 2
    assert by[("competitor", "Ro")]["prev_mean"] is None and by[("competitor", "Ro")]["delta"] is None
    assert by[("drug", "semaglutide")]["delta"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_category_and_drug_mix_with_deltas(tmp_path):
    state = await fresh_state(tmp_path)
    prev = WINDOW_START - timedelta(days=1)
    cur = WINDOW_START + timedelta(days=1)
    for key, at, category, drug in [("a", cur, "complaint", "tirzepatide"), ("b", cur, "complaint", "tirzepatide"),
                                    ("c", cur, "question", ""), ("d", prev, "complaint", "semaglutide")]:
        await add_triaged(state, key, text="t", posted_at=at, category=category, drug=drug)

    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END)

    cats = {r["category"]: r for r in report.category_mix}
    assert cats["complaint"] == {"category": "complaint", "count": 2, "prev_count": 1, "delta": 1}
    assert cats["question"]["delta"] == 1
    drugs = {r["drug"]: r for r in report.drug_mix}
    assert drugs["tirzepatide"]["count"] == 2
    assert drugs["semaglutide"] == {"drug": "semaglutide", "count": 0, "prev_count": 1, "delta": -1}
    assert "" not in drugs


@pytest.mark.asyncio
async def test_complaint_themes_by_competitor_skip_the_brand_name(tmp_path):
    state = await fresh_state(tmp_path)
    cur = WINDOW_START + timedelta(days=1)
    for i in range(3):
        await add_triaged(state, f"h{i}", text=f"Hims shipping delay again, week {i}", posted_at=cur,
                          competitor="Hims & Hers", subject_type="competitor", category="complaint")
    await add_triaged(state, "q", text="Hims pricing question", posted_at=cur,
                      competitor="Hims & Hers", subject_type="competitor", category="question")

    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END)

    themes = {t["subject"]: t["terms"] for t in report.complaint_themes}
    top = [t["term"] for t in themes["Hims & Hers"]]
    assert top[0] == "shipping delay"
    assert "hims" not in top and "pricing" not in top


@pytest.mark.asyncio
async def test_report_to_dict_is_json_ready(tmp_path):
    import json

    state = await fresh_state(tmp_path)
    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END)

    data = json.loads(json.dumps(report.to_dict()))
    assert data["window_start"].startswith("2026-09-20")
    assert data["terms"] == [] and data["mentions"] == 0


# --- Language bank ---------------------------------------------------------------------------------


def test_phrase_norm_lowercases_and_collapses_whitespace():
    assert trends.phrase_norm("  Food   Noise\tis GONE ") == "food noise is gone"


@pytest.mark.asyncio
async def test_language_bank_is_idempotent_and_counts_per_scope(tmp_path):
    state = await fresh_state(tmp_path)
    at = WINDOW_START + timedelta(days=1)
    first = await add_triaged(state, "a", text="food noise is gone for real", posted_at=at,
                              drug="semaglutide", category="praise", sentiment="positive",
                              phrases=["food noise is gone"])
    await add_triaged(state, "b", text="My FOOD  NOISE is gone lol", posted_at=at + timedelta(hours=5),
                      drug="semaglutide", phrases=["FOOD  NOISE is gone", "food noise is gone"])
    await add_triaged(state, "c", text="food noise is gone on tirz", posted_at=at,
                      drug="tirzepatide", phrases=["food noise is gone"])
    await add_triaged(state, "d", text="dropped food noise is gone", posted_at=at,
                      status=MentionStatus.DROPPED, drug="semaglutide", phrases=["food noise is gone"])

    assert await trends.bank_language(state) == 3
    assert await trends.bank_language(state) == 0  # idempotent per mention

    from harvey import pulse_store

    page = await pulse_store.search_language_bank(state)
    rows = {(r["phrase_norm"], r["scope"]): r for r in page["items"]}
    assert set(rows) == {("food noise is gone", "semaglutide"), ("food noise is gone", "tirzepatide")}
    sema = rows[("food noise is gone", "semaglutide")]
    assert sema["count"] == 2  # once per mention, not per duplicate phrase
    assert sema["example_mention_id"] == first
    assert sema["category"] == "praise" and sema["sentiment"] == "positive"
    assert sema["first_seen"] < sema["last_seen"]
