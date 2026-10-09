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
    assert body["searches"] == ["GLP-1 telehealth provider"] and body["sort"] == "new"
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
