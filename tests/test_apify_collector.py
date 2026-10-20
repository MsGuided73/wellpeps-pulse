"""Live Reddit collection through Apify (Phase 9): mapping, client, schedule, budget.
No network: Apify is an httpx.MockTransport."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from harvey import collect
from harvey.collectors import get_collector
from harvey.collectors.apify import (
    REDDIT_ACTOR, ApifyClient, ApifyError, ApifyRedditCollector, reddit_item_to_mention,
)
from harvey.config import PulseConfig
from harvey.ingest import run_collectors
from harvey.models import Platform
from harvey.state import StateManager

NOW = datetime(2026, 10, 9, 12, 0)
POST = {
    "id": "t3_abc", "url": "https://www.reddit.com/r/Semaglutide/comments/abc/which_provider/",
    "username": "someone", "title": "Which provider includes follow&#39;up?",
    "communityName": "r/Semaglutide", "parsedCommunityName": "Semaglutide",
    "body": "Comparing   telehealth &amp; pharmacies", "createdAt": "2026-10-09T09:20:01.000Z",
    "dataType": "post", "upVotes": 4, "email": "never@stored.example",
}


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


def _fake_apify(items, status="SUCCEEDED", cost=0.048, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer apify_test"
        if seen is not None:
            seen.append(request)
        if request.method == "POST" and request.url.path.endswith(f"/acts/{REDDIT_ACTOR}/runs"):
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING"}})
        if request.url.path.endswith("/actor-runs/run1"):
            return httpx.Response(200, json={"data": {"id": "run1", "status": status, "usageTotalUsd": cost,
                                                      "defaultDatasetId": "ds1"}})
        if request.url.path.endswith("/datasets/ds1/items"):
            return httpx.Response(200, json=items)
        return httpx.Response(404, json={"error": "nope"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _collector(items, **kw):
    seen = kw.pop("seen", None)
    client = ApifyClient("apify_test", http=_fake_apify(items, seen=seen, **kw))
    return ApifyRedditCollector(searches=["GLP-1 telehealth provider"], max_items=10, max_charge_usd=0.2,
                                client=client)


# --- mapping -----------------------------------------------------------------------------------------


def test_a_reddit_post_maps_to_a_minimal_mention():
    m = reddit_item_to_mention(POST)
    assert m.platform is Platform.REDDIT and m.external_id == "t3_abc"
    assert m.url == POST["url"] and m.author_handle == "u/someone"
    assert m.title == "Which provider includes follow'up?" and m.text == "Comparing telehealth & pharmacies"
    assert m.posted_at == datetime(2026, 10, 9, 9, 20, 1)
    assert m.engagement == {"community": "Semaglutide", "upVotes": 4}
    assert "never@stored.example" not in m.model_dump_json()          # nothing beyond the allow-list


def test_non_posts_and_foreign_urls_are_skipped():
    assert reddit_item_to_mention({**POST, "dataType": "community"}) is None
    assert reddit_item_to_mention({**POST, "url": "https://evil.example/x"}) is None
    assert reddit_item_to_mention({**POST, "username": "[deleted]"}).author_handle == ""


def test_a_comment_keeps_its_parent():
    m = reddit_item_to_mention({**POST, "id": "t1_c", "dataType": "comment", "parentId": "t3_abc"})
    assert m.parent_external_id == "t3_abc"


# --- client + collector ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collector_runs_the_actor_with_caps_and_reports_cost():
    seen = []
    c = _collector([POST, POST, {**POST, "dataType": "user"}], seen=seen)
    out = [m async for m in c.collect(None)]
    assert [m.url for m in out] == [POST["url"]]                      # duplicate + non-post skipped
    assert c.skipped == 2 and c.cost_usd == pytest.approx(0.048)
    start = seen[0]
    assert start.url.params["maxItems"] == "10" and start.url.params["maxTotalChargeUsd"] == "0.20"
    body = json.loads(start.content)
    assert body["searches"] == ["GLP-1 telehealth provider"] and body["sort"] == "relevance"
    assert body["includeNSFW"] is False and body["skipComments"] is True


def test_actor_input_never_looks_back_further_than_the_lookback():
    c = ApifyRedditCollector(searches=["x"], lookback_hours=48, client=ApifyClient("t"))
    old = datetime(2020, 1, 1)
    limit = datetime.fromisoformat(c.actor_input(old)["postDateLimit"])
    assert limit > datetime.now() - timedelta(hours=49)


def test_missing_token_is_a_clear_error():
    with pytest.raises(ApifyError, match="APIFY_TOKEN is not set"):
        ApifyClient("")


@pytest.mark.asyncio
async def test_ingest_stores_mentions_and_the_run_cost(state):
    report = await run_collectors(state, [_collector([POST])])
    assert report.created == 1 and "cost $0.048" in report.lines()[0]
    runs = await state.get_runs()
    assert runs[0]["stage"] == "apify_reddit" and runs[0]["cost_usd"] == pytest.approx(0.048)
    again = await run_collectors(state, [_collector([POST])])
    assert again.created == 0 and again.duplicates == 1                # permalink dedupe


# --- schedule and budget -----------------------------------------------------------------------------


def _config(**reddit) -> PulseConfig:
    base = {"enabled": True, "searches": ["WellPeps"], "interval_minutes": 720, "monthly_budget_usd": 1.0}
    return PulseConfig(collectors={"apify_reddit": {**base, **reddit}})


def _builder(items=(POST,)):
    def build(name, **kwargs):
        assert name == "apify_reddit" and kwargs["searches"] == ["WellPeps"]
        return _collector(list(items))
    return build


@pytest.mark.asyncio
async def test_disabled_collector_never_runs_on_schedule(state):
    assert await collect.run_due_collectors(state, _config(enabled=False), now=NOW, build=_builder()) == []


@pytest.mark.asyncio
async def test_due_collector_runs_then_waits_for_its_interval(state):
    first = await collect.run_due_collectors(state, _config(), build=_builder())
    assert len(first) == 1 and first[0].created == 1
    assert await collect.run_due_collectors(state, _config(), build=_builder()) == []     # not due yet


@pytest.mark.asyncio
async def test_monthly_budget_stops_scheduled_and_manual_runs(state):
    run_id = await state.start_run(stage="apify_reddit", provider="collector")
    await state.finish_run(run_id, cost_usd=1.25)
    cfg = _config(interval_minutes=30)
    later = datetime.utcnow() + timedelta(hours=2)
    assert await collect.run_due_collectors(state, cfg, now=later, build=_builder()) == []
    assert await collect.run_collector(state, cfg, "apify_reddit", scheduled=False, build=_builder()) is None


@pytest.mark.asyncio
async def test_manual_run_ignores_enabled_and_interval_but_not_budget(state):
    report = await collect.run_collector(state, _config(enabled=False), "apify_reddit", scheduled=False,
                                         build=_builder())
    assert report is not None and report.created == 1


def test_repo_config_keeps_live_collection_off_until_legal_sign_off():
    from harvey.config import load_config

    reddit = load_config().collectors.apify_reddit
    assert reddit.enabled is False and reddit.searches and reddit.max_charge_usd <= 0.5


def test_collector_is_registered():
    assert isinstance(get_collector("apify_reddit", searches=["x"], token="t"), ApifyRedditCollector)


@pytest.mark.asyncio
async def test_a_slow_run_is_aborted_but_its_paid_results_are_kept():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/runs"):
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING"}})
        if request.url.path.endswith("/abort"):
            return httpx.Response(200, json={"data": {"id": "run1", "status": "ABORTING"}})
        if request.url.path.endswith("/actor-runs/run1"):
            aborted = any(p.endswith("/abort") for _, p in calls)
            return httpx.Response(200, json={"data": {"id": "run1", "status": "ABORTED" if aborted else "RUNNING",
                                                      "usageTotalUsd": 0.1, "defaultDatasetId": "ds1"}})
        return httpx.Response(200, json=[POST])

    client = ApifyClient("apify_test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                         wait_seconds=60)
    c = ApifyRedditCollector(searches=["x"], client=client)
    out = [m async for m in c.collect(None)]
    assert [m.url for m in out] == [POST["url"]] and c.cost_usd == pytest.approx(0.1)
    assert ("POST", "/v2/actor-runs/run1/abort") in calls


# --- relevance: sort + keyword gate (R-17) ---------------------------------------------------------------


def _post(n, title, body="", community="misc"):
    return {**POST, "id": f"t3_{n}", "url": f"https://www.reddit.com/r/{community}/comments/{n}/x/",
            "title": title, "body": body, "parsedCommunityName": community}


def _gated(items, **kw):
    client = ApifyClient("apify_test", http=_fake_apify(items))
    return ApifyRedditCollector(searches=["x"], max_items=10, client=client, **kw)


def test_actor_input_sorts_by_relevance_by_default_and_honours_config():
    assert ApifyRedditCollector(searches=["x"], client=ApifyClient("t")).actor_input(None)["sort"] == "relevance"
    c = ApifyRedditCollector(searches=["x"], sort="top", client=ApifyClient("t"))
    assert c.actor_input(None)["sort"] == "top"


def test_unknown_sort_is_rejected_in_config_and_collector():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PulseConfig(collectors={"apify_reddit": {"sort": "controversial"}})
    with pytest.raises(ValueError, match="sort must be one of"):
        ApifyRedditCollector(searches=["x"], sort="controversial", client=ApifyClient("t"))
    for ok in ("relevance", "new", "top", "hot", "comments"):
        assert PulseConfig(collectors={"apify_reddit": {"sort": ok}}).collectors.apify_reddit.sort == ok


def test_config_defaults_and_repo_config_use_relevance_and_the_gate():
    from harvey.config import load_config

    default = PulseConfig().collectors.apify_reddit
    assert default.sort == "relevance" and default.require_keyword_match is True
    assert "GLP-1" in default.extra_terms and "peptides" in default.extra_terms
    repo = load_config().collectors.apify_reddit
    assert repo.sort == "relevance" and repo.require_keyword_match is True
    assert repo.extra_terms == default.extra_terms
    assert '"WellPeps"' in repo.searches and len(repo.searches) == 8
    assert repo.max_items == 30 and repo.max_charge_usd == 0.25 and repo.monthly_budget_usd == 4.0


def test_collect_passes_sort_gate_and_extra_terms_to_the_collector():
    kwargs = collect.collector_settings(_config(sort="hot", require_keyword_match=False,
                                                extra_terms=["foo"]))["apify_reddit"][1]
    assert kwargs["sort"] == "hot" and kwargs["require_keyword_match"] is False
    assert kwargs["extra_terms"] == ["foo"]


@pytest.mark.asyncio
async def test_gate_drops_off_topic_posts_and_counts_them():
    on = [
        _post(1, "Switching to compounded tirzepatide, any tips?"),
        _post(2, "Has anyone used WellPeps?"),
        _post(3, "Ozempic made me nauseous"),
        _post(4, "Best GLP-1 provider for price"),
    ]
    off = [
        _post(5, "Hiring warehouse staff in Atlanta", "great pay"),
        _post(6, "Can't find my stick figure drawings"),
        _post(7, "Nada left in the tank today"),
    ]
    c = _gated(on + off)
    out = [m async for m in c.collect(None)]
    assert sorted(m.external_id for m in out) == ["t3_1", "t3_2", "t3_3", "t3_4"]
    assert c.off_topic == 3 and c.skipped == 3


@pytest.mark.asyncio
async def test_gate_matches_whole_words_only():
    from harvey import knowledge

    assert not knowledge.on_topic("I could not find nada")                    # not NAD, not fin
    assert knowledge.on_topic("NAD+ shots anyone?")
    c = _gated([_post(1, "where do I find it"), _post(2, "my fin broke"), _post(3, "telehealthy living")],
               extra_terms=["fin", "telehealth"])
    out = [m async for m in c.collect(None)]
    assert [m.external_id for m in out] == ["t3_2"]                           # "find"/"telehealthy" are not terms
    assert c.off_topic == 2


@pytest.mark.asyncio
async def test_gate_reads_the_body_and_extra_terms_case_insensitively():
    c = _gated([_post(1, "Question", "my GLP1 plan"), _post(2, "Question", "Weight Loss Medication costs"),
                _post(3, "Question", "just a question")])
    out = [m async for m in c.collect(None)]
    assert [m.external_id for m in out] == ["t3_1", "t3_2"] and c.off_topic == 1


@pytest.mark.asyncio
async def test_gate_off_keeps_everything():
    c = _gated([_post(1, "Hiring warehouse staff"), _post(2, "Ozempic")], require_keyword_match=False)
    out = [m async for m in c.collect(None)]
    assert len(out) == 2 and c.off_topic == 0 and c.skipped == 0


@pytest.mark.asyncio
async def test_ingest_report_shows_the_off_topic_count(state):
    c = _gated([_post(1, "compounded tirzepatide"), _post(2, "Hiring warehouse staff"),
                _post(3, "stick figure art")])
    report = await run_collectors(state, [c])
    assert report.created == 1 and report.skipped == 2 and report.off_topic == 2
    assert "1 created, 0 duplicates, 2 skipped (2 off-topic), 0 errors" in report.lines()[0]
