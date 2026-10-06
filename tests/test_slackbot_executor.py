"""#pulse-query executor: QuerySpec -> aggregates via the existing deterministic
code. Privacy floor (< 2 suppressed, sentiment needs 3), no mention text,
handles, URLs or ids in the result, and dashboard deep links."""

import json
from datetime import datetime

import pytest
import pytest_asyncio

from harvey import pulse_store
from harvey.config import PulseConfig
from harvey.slackbot import executor
from harvey.slackbot.planner import QuerySpec
from tests.pulse_helpers import fresh_state
from tests.slackbot_helpers import NOW, seed_week

CONFIG = PulseConfig()


@pytest_asyncio.fixture
async def state(tmp_path):
    state = await fresh_state(tmp_path)
    await seed_week(state)
    return state


async def _run(state, **spec):
    return await executor.execute(state, QuerySpec(**spec), CONFIG, now=NOW)


def _no_identifiers(payload: dict):
    dumped = json.dumps(payload)
    for leak in ("waiting on my order", "a@b.example", "reddit.com", "contact me", '"id"', "mention_id"):
        assert leak not in dumped, leak


@pytest.mark.asyncio
async def test_volume_counts_and_category_mix(state):
    result = await _run(state, intent="volume", days=7)
    data = result.data
    assert data["mentions"] == 10 and data["previous_period_mentions"] == 2
    cats = {c["category"]: c["mentions"] for c in data["by_category"]}
    assert cats["complaint"] == 7 and cats["praise"] == 2
    assert "question" not in cats  # one question: below the floor (folded into other)
    assert result.link == "#analytics?days=7"
    _no_identifiers(result.payload())


@pytest.mark.asyncio
async def test_share_of_voice_suppresses_brands_below_two(state):
    result = await _run(state, intent="share_of_voice", days=7)
    brands = {b["brand"]: b for b in result.data["brands"]}
    assert brands["Hims & Hers"]["mentions"] == 4
    assert brands["WellPeps"]["share_pct"] == 50.0
    assert "Ro" not in brands  # 1 mention
    _no_identifiers(result.payload())


@pytest.mark.asyncio
async def test_sentiment_needs_three_scored_mentions(state):
    result = await _run(state, intent="sentiment", days=7, competitors=["hims & hers"])
    brands = {b["brand"]: b for b in result.data["brands"]}
    assert brands["WellPeps"]["mean"] is not None and brands["WellPeps"]["scored_mentions"] == 5
    assert brands["Hims & Hers"]["scored_mentions"] == 4
    assert result.filters["competitors"] == ["Hims & Hers"]  # canonicalised
    assert "competitor=Hims%20%26%20Hers" in result.link


@pytest.mark.asyncio
async def test_sentiment_mean_is_none_below_three(tmp_path):
    from tests.pulse_helpers import add_triaged

    state = await fresh_state(tmp_path)
    for i in range(2):
        await add_triaged(state, f"wp{i}", text="ok", subject_type="wellpeps", category="praise",
                          sentiment_score=0.9, posted_at=datetime(2026, 9, 27, 12 + i))
    result = await executor.execute(state, QuerySpec(intent="sentiment"), CONFIG, now=NOW)
    wellpeps = result.data["brands"][0]
    assert wellpeps["mean"] is None and wellpeps["scored_mentions"] is None


@pytest.mark.asyncio
async def test_emerging_terms_are_aggregates_seen_at_least_twice(state):
    result = await _run(state, intent="emerging_terms", days=7)
    terms = result.data["terms"]
    assert terms and all(t["mentions"] >= 2 for t in terms)
    for t in terms:
        assert "@" not in t["term"] and "http" not in t["term"]
    _no_identifiers(result.payload())


@pytest.mark.asyncio
async def test_complaints_heatmap_and_term_count(state):
    result = await _run(state, intent="complaints", days=7)
    assert result.data["complaint_mentions"] == 7
    clusters = {(c["brand"], c["theme"]): c["mentions"] for c in result.data["top_clusters"]}
    assert clusters[("Hims & Hers", "Price & billing")] == 4
    assert all(n >= 2 for n in clusters.values())

    by_term = await _run(state, intent="complaints", days=7, term="shipping")
    assert by_term.data["mentions"] == 3
    assert by_term.link == "#feed?q=shipping&category=complaint"


@pytest.mark.asyncio
async def test_search_count_floor_and_feed_link(state):
    result = await _run(state, intent="search_count", days=7, term="Shipping")
    assert result.data["mentions"] == 3 and result.data["previous_period_mentions"] == 2
    assert result.link == "#feed?q=Shipping"

    single = await _run(state, intent="search_count", days=7, term="texas")
    assert single.data["mentions"] is None  # one mention: not shown

    none = await _run(state, intent="search_count", days=7, term="zzzunseen")
    assert none.data["mentions"] == 0


@pytest.mark.asyncio
async def test_drug_momentum_hides_drugs_below_the_floor(state):
    result = await _run(state, intent="drug_momentum", days=7)
    drugs = {d["drug"]: d["mentions"] for d in result.data["drugs"]}
    assert drugs == {"semaglutide": 6, "tirzepatide": 3}
    assert "BPC-157" in result.data["below_floor"]


@pytest.mark.asyncio
async def test_escalations_sla(state):
    result = await _run(state, intent="escalations_sla", days=7)
    assert result.data["escalations"] == 0 and result.data["open_now"] == 0
    assert result.link == "#urgent"


@pytest.mark.asyncio
async def test_latest_brief_none_then_scrubbed_brief(state):
    empty = await _run(state, intent="latest_brief")
    assert empty.data == {"brief": None} and empty.link == "#pulse"

    brief_id = await pulse_store.save_brief(state, {
        "period": "daily", "window_start": datetime(2026, 9, 27, 4), "window_end": datetime(2026, 9, 28, 4),
        "status": "ok", "headline": "Shipping talk up; see https://x.example and @someone",
        "summary_md": "s", "action_cards": [{"title": "Refresh FAQ", "why": "w", "action": "a",
                                             "owner_hint": "support", "urgency": "watch"}],
        "watchlist": [], "data": {"language_bank": []}, "model": "m", "created_at": NOW}, [])
    result = await _run(state, intent="latest_brief")
    brief = result.data["brief"]
    assert "https" not in brief["headline"] and "@someone" not in brief["headline"]
    assert brief["top_actions"] == ["Refresh FAQ"]
    assert result.link == f"#pulse-brief-{brief_id}"


@pytest.mark.asyncio
async def test_unknown_filters_are_ignored_and_reported(state):
    result = await _run(state, intent="volume", competitors=["Nobody Inc", "WellPeps"],
                        drugs=["semaglutide", "tirzepatide"], categories=["complaint", "praise"])
    assert result.filters["competitors"] == []
    assert result.filters["drug"] == "semaglutide" and result.filters["category"] == "complaint"
    assert "competitor Nobody Inc" in result.ignored
    assert any("tirzepatide" in i for i in result.ignored)
    assert any("praise" in i for i in result.ignored)


@pytest.mark.asyncio
async def test_help_runs_nothing(state):
    result = await _run(state, intent="help")
    assert result.intent == "help" and result.data == {}


def test_full_link():
    assert executor.full_link("", "#analytics?days=7") == ""
    assert executor.full_link("https://pulse.example.com/", "#analytics?days=7") == \
        "https://pulse.example.com/#analytics?days=7"


def test_floor():
    assert [executor.floor(n) for n in (0, 1, 2, 9, None)] == [0, None, 2, 9, None]


def test_shaping_hides_counts_inferable_below_the_floor():
    vol = executor._volume({"total": 5, "prev_total": 1, "change_pct": 400.0, "series": []})
    assert vol["previous_period_mentions"] is None and vol["change_pct"] is None
    share = executor._share({"total": 5, "prev_total": 1, "series": [
        {"key": "WellPeps", "total": 5, "share": 1.0, "prev_share": 1.0, "delta_pts": 0.0}]})
    assert share["brands"][0]["previous_share_pct"] is None and share["brands"][0]["change_pts"] is None
    em = executor._emerging({"baseline_days": 28, "terms": [
        {"term": "shipping delay", "count": 3, "baseline_count": 1, "velocity": 4.0, "is_new": False}]})
    assert em["terms"][0]["baseline_mentions"] is None
