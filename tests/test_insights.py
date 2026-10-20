"""Pain points & emerging ICPs (harvey/insights.py): community attribution,
roll-up counts / ordering / examples, segment tagging with new-vs-previous,
privacy of the model payload."""

import json
from datetime import datetime, timedelta

import pytest

from harvey import insights, knowledge
from harvey.models.knowledge import InsightsFile
from tests.pulse_helpers import add_triaged, fresh_state

END = datetime(2026, 10, 19, 12, 0)
START = END - timedelta(days=7)
IN_WINDOW = START + timedelta(days=2)
IN_PREVIOUS = START - timedelta(days=3)
HANDLE = "secret_user_42"


def _row(url="https://www.reddit.com/r/test/comments/abc/x/", **kw) -> dict:
    return {"id": 1, "url": url, "title": "", "text": "", "engagement_json": "{}", "at": "2026-10-18T10:00:00",
            "subject_type": "", "competitor": "", "drug": "", "category": "question", "sentiment": "neutral",
            "sentiment_score": 0.0, "phrases_json": "[]", "protocol_json": "{}", **kw}


# ── Community → competitor attribution ──


def test_ivim_community_is_attributed_by_url():
    row = _row(url="https://www.reddit.com/r/IVIMHEALTH/comments/1x1i8mn/cheapest_glp1/")

    assert insights.attribute_competitor(row) == ("Ivím Health", "community")


def test_community_from_engagement_json_matches_an_alias():
    row = _row(url="https://example.com/post/1", engagement_json='{"community": "Noom"}')

    assert insights.attribute_competitor(row) == ("Noom Med", "community")


@pytest.mark.parametrize("community", ["Ivím Health", "IVIM HEALTH", "ivim_health", "Ivim-Health"])
def test_matching_ignores_case_spaces_punctuation_and_accents(community):
    row = _row(url="https://example.com/p", engagement_json=json.dumps({"community": community}))

    assert insights.attribute_competitor(row)[0] == "Ivím Health"


def test_yourmd_community_is_attributed():
    row = _row(url="https://www.reddit.com/r/yourmdonline/comments/1/weekly_pricing/")

    assert insights.attribute_competitor(row) == ("YourMD Online", "community")


def test_unrelated_community_is_not_attributed():
    row = _row(url="https://www.reddit.com/r/tressless/comments/1/x/")

    assert insights.attribute_competitor(row) == ("", "")


def test_triage_competitor_wins_over_the_community():
    row = _row(url="https://www.reddit.com/r/IVIMHEALTH/comments/1/x/", competitor="Henry Meds")

    assert insights.attribute_competitor(row) == ("Henry Meds", "triage")


@pytest.mark.parametrize("community", ["found", "Eden", "ro", "hers", "roman"])
def test_ambiguous_or_short_names_are_not_attributed(community):
    row = _row(url="https://example.com/p", engagement_json=json.dumps({"community": community}))

    assert insights.attribute_competitor(row) == ("", "")


@pytest.mark.asyncio
async def test_stored_triage_row_is_not_rewritten(tmp_path):
    state = await fresh_state(tmp_path)
    mid = await add_triaged(state, "c1", text="x", posted_at=IN_WINDOW,
                            url="https://www.reddit.com/r/IVIMHEALTH/comments/c1/x/")

    await insights.compute_insights(state, START, END)

    assert (await state.get_triage(mid)).competitor == ""


# ── Roll-up over seeded mentions ──


async def _seed(state):
    """Window: price_clarity x3 (2 complaint-like, 2 competitors), care_process x2, one-off need.
    Previous window: price_clarity x1."""
    shared = dict(subject_type="competitor", category="complaint")
    await add_triaged(state, "w1", text="Hims price jumped after month one", posted_at=IN_WINDOW, author=HANDLE,
                      competitor="Hims & Hers", sentiment="negative", sentiment_score=-0.7,
                      intents=["alternatives_requested"], unmet_need="price_clarity",
                      phrases=["the price doubled after the first month", f"@{HANDLE} said so", "ok"], **shared)
    await add_triaged(state, "w2", text="Henry renewal charge surprise", posted_at=IN_WINDOW + timedelta(hours=1),
                      competitor="Henry Meds", sentiment="negative", sentiment_score=-0.4,
                      intents=["venting_only"], unmet_need="price_clarity",
                      phrases=["the price doubled after the first month", "renewal charged me twice"], **shared)
    await add_triaged(state, "w3", text="what does it cost in total", posted_at=IN_WINDOW + timedelta(hours=2),
                      category="question", sentiment="neutral", unmet_need="price_clarity",
                      intents=["comparison_request"], phrases=["x" * 130, "see https://spam.example/x"])
    await add_triaged(state, "w4", text="does a clinician review me", posted_at=IN_WINDOW + timedelta(hours=3),
                      category="question", unmet_need="care_process", phrases=["does a real clinician review my intake"])
    await add_triaged(state, "w5", text="who reviews labs", posted_at=IN_WINDOW + timedelta(hours=4),
                      category="question", unmet_need="care_process", phrases=["who reviews my lab work first"])
    await add_triaged(state, "w6", text="a one-off supply gripe", posted_at=IN_WINDOW + timedelta(hours=5),
                      category="question", unmet_need="medication_availability", phrases=["out of stock again today"])
    await add_triaged(state, "p1", text="old price complaint", posted_at=IN_PREVIOUS, category="complaint",
                      sentiment="negative", sentiment_score=-0.5, unmet_need="price_clarity",
                      phrases=["old price complaint phrase here"])


@pytest.mark.asyncio
async def test_pain_points_counts_ordering_and_floor(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed(state)

    result = await insights.compute_insights(state, START, END)

    assert result["mentions"] == 6 and result["previous_mentions"] == 1
    ids = [p["id"] for p in result["pain_points"]]
    assert ids == ["price_clarity", "care_process"]       # medication_availability (1) is below the floor
    price = result["pain_points"][0]
    assert (price["count"], price["complaint_count"], price["score"]) == (3, 2, 5)
    assert (price["prev_count"], price["delta"], price["status"]) == (1, 2, "growing")
    assert price["label"] == "Price and cancellation clarity"
    assert {c["name"] for c in price["competitors"]} == {"Hims & Hers", "Henry Meds"}
    assert result["suppressed"]["pain_points"] == 1
    assert result["pain_points"][1]["status"] == "new"


@pytest.mark.asyncio
async def test_min_count_override_shows_singletons(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed(state)

    result = await insights.compute_insights(state, START, END, min_count=1)

    assert "medication_availability" in [p["id"] for p in result["pain_points"]]
    assert result["suppressed"]["pain_points"] == 0


@pytest.mark.asyncio
async def test_examples_are_short_verbatim_unique_and_linked(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed(state)

    result = await insights.compute_insights(state, START, END)

    examples = result["pain_points"][0]["examples"]
    assert len(examples) <= 2
    assert len({e["mention_id"] for e in examples}) == len(examples)
    for e in examples:
        assert 1 <= len(e["quote"]) <= 120 and len(e["quote"].split()) >= 3
        assert "http" not in e["quote"] and "@" not in e["quote"]
        assert e["url"].startswith("https://www.reddit.com/r/test/comments/")
    quotes = [e["quote"] for e in examples]
    assert "the price doubled after the first month" in quotes
    assert "x" * 130 not in quotes and "ok" not in quotes


@pytest.mark.asyncio
async def test_no_author_handle_anywhere_in_the_rollup(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed(state)

    result = await insights.compute_insights(state, START, END, min_count=1)

    assert HANDLE not in json.dumps(result)


@pytest.mark.asyncio
async def test_competitor_complaints_include_community_attribution(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed(state)
    for i, score in enumerate((-0.3, -0.9)):
        await add_triaged(state, f"iv{i}", text="my first charge was far above the ad price",
                          posted_at=IN_WINDOW + timedelta(days=1, hours=i), category="purchase_intent",
                          sentiment="negative", sentiment_score=score, unmet_need="price_clarity",
                          phrases=[f"my first charge was nowhere near the {199 + i} advertised"],
                          url=f"https://www.reddit.com/r/IVIMHEALTH/comments/iv{i}/cheapest/")

    result = await insights.compute_insights(state, START, END)

    by_name = {c["competitor"]: c for c in result["competitor_complaints"]}
    ivim = by_name["Ivím Health"]
    assert ivim["count"] == 2 and ivim["sources"] == {"community": 2}
    assert ivim["worst_sentiment"] == -0.9
    assert ivim["main_pain"]["id"] == "price_clarity"
    assert ivim["example"]["mention_id"] and ivim["example"]["url"].endswith("/cheapest/")
    assert result["community_attributed"] == 2
    assert [c["competitor"] for c in result["competitor_complaints"]][0] in {"Ivím Health"}
    assert by_name["Henry Meds"]["count"] == 1     # public posts about companies: a single complaint is listed
    assert result["suppressed"]["competitor_complaints"] == 0


async def _seed_competitor(state, key, **kw):
    base = dict(text="", posted_at=IN_WINDOW, category="question", sentiment="neutral", sentiment_score=0.0,
                competitor="Henry Meds", unmet_need="price_clarity", intents=["comparison_request"], phrases=[])
    return await add_triaged(state, key, **{**base, **kw})


@pytest.mark.asyncio
async def test_neutral_comparison_naming_a_competitor_is_not_a_complaint(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_competitor(state, "c1", text="got quotes from Henry Meds and others, which is cheapest overall?",
                           sentiment="negative", sentiment_score=-0.25)

    result = await insights.compute_insights(state, START, END)

    assert result["competitor_complaints"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", [
    dict(category="complaint"),
    dict(intents=["comparison_request", "alternatives_requested"]),
    dict(sentiment_score=-0.5),
    dict(text="they charged me three months up front without saying so"),
    dict(phrases=["my prescription was never sent"]),
])
async def test_one_real_signal_makes_a_competitor_complaint(tmp_path, signal):
    state = await fresh_state(tmp_path)
    await _seed_competitor(state, "c1", **signal)

    result = await insights.compute_insights(state, START, END)

    assert [c["competitor"] for c in result["competitor_complaints"]] == ["Henry Meds"]


@pytest.mark.asyncio
async def test_complaint_signal_without_a_competitor_is_not_a_competitor_complaint(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_competitor(state, "c1", competitor="", category="complaint", sentiment_score=-0.8)

    result = await insights.compute_insights(state, START, END)

    assert result["competitor_complaints"] == []


def test_competitor_complaint_rule_defaults_from_config():
    rule = knowledge.insights().competitor_complaint
    assert rule.min_count == 1 and rule.sentiment_max == -0.4
    assert "alternatives_requested" in rule.intents and "never sent" in rule.keywords


# ── Segments ──


async def _seed_segment(state, key, text, when, **kw):
    return await add_triaged(state, key, text=text, posted_at=when, **kw)


@pytest.mark.asyncio
async def test_segment_tagging_and_new_vs_previous(tmp_path):
    state = await fresh_state(tmp_path)
    ins = "My insurance stopped covering Wegovy last month, so I am paying cash for GLP-1 now"
    for i in range(3):
        await _seed_segment(state, f"ins{i}", ins, IN_WINDOW + timedelta(hours=i), category="question",
                            phrases=["my insurance stopped covering Wegovy last month"])
    # steady: 2 now, 2 before
    for i in range(2):
        await _seed_segment(state, f"cmp{i}", "which is cheapest", IN_WINDOW + timedelta(hours=i),
                            intents=["comparison_request"], unmet_need="price_clarity")
        await _seed_segment(state, f"cmpp{i}", "which is cheapest", IN_PREVIOUS + timedelta(hours=i),
                            intents=["comparison_request"], unmet_need="price_clarity")
    # growing: 3 now, 1 before
    for i in range(3):
        await _seed_segment(state, f"saf{i}", "is the pharmacy legit", IN_WINDOW + timedelta(hours=i),
                            unmet_need="care_process")
    await _seed_segment(state, "safp", "is the pharmacy legit", IN_PREVIOUS, unmet_need="care_process")
    # declining: 1 now (below floor, hidden), 4 before
    await _seed_segment(state, "unh0", "switching away", IN_WINDOW, competitor="Hims & Hers",
                        intents=["alternatives_requested"], sentiment="negative", sentiment_score=-0.6)

    result = await insights.compute_insights(state, START, END)

    segs = {s["id"]: s for s in result["segments"]}
    assert segs["insurance_dropout"]["count"] == 3 and segs["insurance_dropout"]["status"] == "new"
    assert segs["insurance_dropout"]["emerging"] is True
    assert segs["price_comparer"]["status"] == "steady" and segs["price_comparer"]["emerging"] is False
    assert segs["safety_first_seeker"]["status"] == "growing"
    assert segs["safety_first_seeker"]["prev_count"] == 1
    assert "unhappy_with_provider" not in segs and result["suppressed"]["segments"] == 1
    assert [s["emerging"] for s in result["segments"]] == [True, True, False]   # emerging first
    assert segs["insurance_dropout"]["examples"][0]["quote"].startswith("my insurance stopped")


@pytest.mark.asyncio
async def test_segment_clauses_are_anded_and_a_declining_segment_is_reported(tmp_path):
    state = await fresh_state(tmp_path)
    # coverage talk with no GLP-1 term: the third clause fails
    for i in range(2):
        await _seed_segment(state, f"nog{i}", "my insurance stopped covering my allergy shots", IN_WINDOW)
    for i in range(2):
        await _seed_segment(state, f"cur{i}", "switching from them", IN_WINDOW + timedelta(hours=i),
                            competitor="Ro", intents=["venting_only"], sentiment="negative", sentiment_score=-0.8)
    for i in range(5):
        await _seed_segment(state, f"old{i}", "switching from them", IN_PREVIOUS + timedelta(hours=i),
                            competitor="Ro", intents=["venting_only"], sentiment="negative", sentiment_score=-0.8)

    result = await insights.compute_insights(state, START, END)

    segs = {s["id"]: s for s in result["segments"]}
    assert "insurance_dropout" not in segs
    assert segs["unhappy_with_provider"]["status"] == "declining"
    assert segs["unhappy_with_provider"]["competitors"] == [{"name": "Ro", "count": 2}]


def test_apostrophes_do_not_break_keywords():
    cfg = knowledge.insights()
    row = _row(text="my insurance won't cover Zepbond anymore, I lost coverage", id=7)
    item = insights.tag_segments([insights.to_item(row, cfg, {})], cfg)[0]

    assert "insurance_dropout" in item.segments


# ── Privacy of what the model sees ──


@pytest.mark.asyncio
async def test_model_payload_has_counts_and_labels_only(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed(state)
    result = await insights.compute_insights(state, START, END)

    payload = json.dumps(insights.model_payload(result))

    assert "the price doubled" not in payload and "http" not in payload and "mention_id" not in payload
    assert "Price and cancellation clarity" in payload


def test_clean_note_limits_sentences_and_checks_numbers():
    from harvey.briefs import numbers_in

    allowed = {3.0, 2.0}
    assert insights.clean_note("Price talk is up (3). Watch it. Third sentence here.", allowed, numbers_in) == \
        "Price talk is up (3). Watch it."
    assert insights.clean_note("Price talk is up 99%.", allowed, numbers_in) == ""
    assert insights.clean_note("See https://x.example for more.", allowed, numbers_in) == ""
    assert insights.clean_note("", allowed, numbers_in) == ""


def test_markdown_section_lists_quotes_and_links():
    rollup = {"min_count": 2, "pain_points": [{"label": "Price", "count": 3, "complaint_count": 2, "prev_count": 1,
                                                "status": "growing", "competitors": [{"name": "Ro", "count": 1}],
                                                "examples": [{"quote": "the price doubled", "mention_id": 4,
                                                              "url": "https://r/x", "competitor": "Ro"}]}],
              "competitor_complaints": [], "segments": []}

    text = insights.render_markdown(rollup, "A short note.")

    assert text.startswith("### Pain points & emerging ICPs")
    assert "\"the price doubled\" [Ro] (https://r/x)" in text and "A short note." in text
    assert insights.render_markdown({"pain_points": [], "competitor_complaints": [], "segments": []}) == ""


# ── Config ──


def test_shipped_config_loads_with_four_segments():
    cfg = knowledge.insights()

    assert isinstance(cfg, InsightsFile)
    assert [s.id for s in cfg.segments] == ["insurance_dropout", "price_comparer", "safety_first_seeker",
                                            "unhappy_with_provider"]
    assert cfg.min_count == 2 and cfg.max_quote_chars == 120


def test_unknown_intent_in_insights_yaml_is_rejected(tmp_path, monkeypatch):
    import shutil

    import yaml

    for path in knowledge.config_dir().glob("*.yaml"):
        shutil.copy(path, tmp_path / path.name)
    data = yaml.safe_load((tmp_path / "insights.yaml").read_text(encoding="utf-8"))
    data["segments"][0]["all_of"][0]["intents"] = ["not_an_intent"]
    (tmp_path / "insights.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()
    try:
        with pytest.raises(knowledge.KnowledgeError, match="unknown intents"):
            knowledge.insights()
    finally:
        monkeypatch.delenv("PULSE_CONFIG_DIR")
        knowledge.reload()


def test_a_config_dir_without_insights_yaml_falls_back_to_the_bundled_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()
    try:
        assert knowledge.insights().segments
    finally:
        monkeypatch.delenv("PULSE_CONFIG_DIR")
        knowledge.reload()


# ── Questions we couldn't answer (Why and When to Engage: "collect recurring skipped questions") ──

GAP = "relevant question; gate:not_worth_it:knowledge_gap"
HOLD = "direct question; gate:hold_unverified_fact"


async def _seed_gaps(state):
    """Window: sermorelin no-answer x2, approval-time HOLD x2, one-off no-answer, a price_only skip x2.
    Previous window: one sermorelin no-answer."""
    await add_triaged(state, "g1", text="does sermorelin help sleep", posted_at=IN_WINDOW, author=HANDLE,
                      drug="sermorelin", useful_contribution=0, urgency_reason=GAP,
                      phrases=["does sermorelin actually help with deep sleep", f"@{HANDLE} asked"])
    await add_triaged(state, "g2", text="sermorelin and recovery", posted_at=IN_WINDOW + timedelta(hours=1),
                      drug="sermorelin", useful_contribution=0, urgency_reason=GAP,
                      phrases=["is sermorelin worth it for recovery"])
    await add_triaged(state, "g3", text="wellpeps approval time", posted_at=IN_WINDOW + timedelta(hours=2),
                      subject_type="wellpeps", unmet_need="care_process", intents=["wellpeps_question"],
                      urgency_reason=HOLD, phrases=["how long does wellpeps approval take"])
    await add_triaged(state, "g4", text="wellpeps review speed", posted_at=IN_WINDOW + timedelta(hours=3),
                      subject_type="wellpeps", unmet_need="care_process", intents=["wellpeps_question"],
                      urgency_reason=HOLD, phrases=["how fast does the provider review happen"])
    await add_triaged(state, "g5", text="a one-off nad question", posted_at=IN_WINDOW + timedelta(hours=4),
                      drug="nad_plus", useful_contribution=0, urgency_reason=GAP,
                      phrases=["can nad shots replace coffee"])
    for key in ("s1", "s2"):
        await add_triaged(state, key, text="who beats 99", posted_at=IN_WINDOW + timedelta(hours=5),
                          intents=["price_only"], urgency_reason="price; gate:not_worth_it:price_only",
                          phrases=["who can beat ninety nine"])
    await add_triaged(state, "gp", text="older sermorelin question", posted_at=IN_PREVIOUS, drug="sermorelin",
                      useful_contribution=0, urgency_reason=GAP, phrases=["older sermorelin sleep question"])


def test_gap_kind_reads_the_recorded_gate():
    assert insights.gap_kind(GAP) == "no_answer"
    assert insights.gap_kind(HOLD) == "unapproved_fact"
    assert insights.gap_kind("price; gate:not_worth_it:price_only") == ""
    assert insights.gap_kind("") == "" and insights.gap_kind(None) == ""


def test_gap_markers_match_the_engagement_gates():
    from harvey import engagement

    assert insights.GAP_NO_ANSWER_MARK == f"{engagement.GATE_NOT_WORTH_IT}:{engagement.SKIP_KNOWLEDGE_GAP}"
    assert insights.GAP_UNAPPROVED_MARK == engagement.GATE_HOLD_FACT


@pytest.mark.asyncio
async def test_recurring_unanswered_questions_are_grouped_by_topic_with_a_floor(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_gaps(state)

    result = await insights.compute_insights(state, START, END)

    gaps = {g["id"]: g for g in result["knowledge_gaps"]}
    assert set(gaps) == {"no_answer:drug:sermorelin", "unapproved_fact:care_process"}
    serm = gaps["no_answer:drug:sermorelin"]
    assert (serm["count"], serm["prev_count"], serm["status"], serm["kind"]) == (2, 1, "growing", "no_answer")
    assert serm["label"] == "Questions about sermorelin"
    hold = gaps["unapproved_fact:care_process"]
    assert hold["kind_label"] == "WellPeps fact not approved yet" and hold["status"] == "new"
    assert result["suppressed"]["knowledge_gaps"] == 1          # the one-off NAD+ question
    assert [g["count"] for g in result["knowledge_gaps"]] == sorted(
        (g["count"] for g in result["knowledge_gaps"]), reverse=True)


@pytest.mark.asyncio
async def test_unanswered_question_examples_are_quotable_and_anonymous(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_gaps(state)

    result = await insights.compute_insights(state, START, END, min_count=1)

    blob = json.dumps(result["knowledge_gaps"])
    assert HANDLE not in blob and "who can beat ninety nine" not in blob
    for gap in result["knowledge_gaps"]:
        for e in gap["examples"]:
            assert len(e["quote"]) <= 120 and "@" not in e["quote"] and "http" not in e["quote"]


@pytest.mark.asyncio
async def test_unanswered_questions_never_reach_the_model(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_gaps(state)
    result = await insights.compute_insights(state, START, END)

    payload = json.dumps(insights.model_payload(result))

    assert "knowledge_gaps" not in payload and "sermorelin" not in payload


@pytest.mark.asyncio
async def test_only_unanswered_questions_still_counts_as_content(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_gaps(state)
    result = await insights.compute_insights(state, START, END)
    only_gaps = {**result, "pain_points": [], "competitor_complaints": [], "segments": []}

    assert insights.has_content(only_gaps)
    lines = insights.slack_lines(only_gaps)
    assert len(lines) == 1 and lines[0].startswith("Questions we couldn't answer: ")
    assert "Questions about sermorelin (2)" in lines[0]
    assert all("http" not in line and "deep sleep" not in line for line in lines)
    text = insights.render_markdown(only_gaps)
    assert "**Questions we couldn't answer**" in text and "does sermorelin actually help" in text
