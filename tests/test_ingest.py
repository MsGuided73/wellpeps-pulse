"""Ingest: run collectors, dedupe into mentions, record runs/sources/audit."""

import json
import sys

import pytest
import pytest_asyncio

from harvey.collectors import Collector
from harvey.collectors.fixture import FixtureCollector
from harvey.ingest import IngestReport, run_collectors
from harvey.models import AuditEventType, Mention, Platform
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


class _Boom(Collector):
    name = "boom"
    platform_default = Platform.OTHER
    cost_note = "test only"

    async def collect(self, since=None):
        yield Mention(platform=Platform.WEB, url="https://example.invalid/boom/1", text="first")
        raise RuntimeError("upstream exploded")


class _Static(Collector):
    name = "static"
    platform_default = Platform.WEB
    cost_note = "test only"

    def __init__(self, mentions):
        self._mentions = mentions

    async def collect(self, since=None):
        for m in self._mentions:
            yield m


async def _count_mentions(state) -> int:
    return (await state.get_state_summary())["total"]


@pytest.mark.asyncio
async def test_ingest_sample_fixture_creates_mentions(state):
    report = await run_collectors(state, [FixtureCollector()])

    assert isinstance(report, IngestReport)
    [result] = report.results
    assert result.name == "fixture"
    assert result.created >= 21
    assert result.duplicates >= 1  # the utm-tagged copy
    assert result.skipped == 1  # the non-http url
    assert result.errors == 0
    assert await _count_mentions(state) == result.created
    assert report.created == result.created


@pytest.mark.asyncio
async def test_rerunning_ingest_adds_zero_rows(state):
    first = await run_collectors(state, [FixtureCollector()])
    total = await _count_mentions(state)

    second = await run_collectors(state, [FixtureCollector()])

    assert second.created == 0
    assert second.duplicates == first.created + first.duplicates
    assert await _count_mentions(state) == total


@pytest.mark.asyncio
async def test_utm_tagged_duplicate_is_deduped(state, tmp_path):
    fixture_dir = tmp_path / "fx"
    fixture_dir.mkdir()
    rows = [
        {"platform": "instagram", "external_id": "", "url": "https://www.instagram.com/p/AbC123/",
         "author_handle": "@fx_a", "text": "WellPeps shipping took forever"},
        {"platform": "instagram", "external_id": "", "url": "https://www.instagram.com/p/AbC123/?utm_source=ig&igshid=zz",
         "author_handle": "@fx_a", "text": "WellPeps shipping took forever"},
    ]
    (fixture_dir / "a.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    report = await run_collectors(state, [FixtureCollector(directory=fixture_dir)])

    assert (report.created, report.duplicates) == (1, 1)


@pytest.mark.asyncio
async def test_ingest_records_a_run_per_collector(state):
    report = await run_collectors(state, [FixtureCollector()])

    runs = await state.get_runs()
    [run] = [r for r in runs if r["stage"] == "fixture"]
    assert run["status"] == "completed"
    assert run["records"] == report.created
    assert run["ended_at"] is not None
    assert report.results[0].run_id == run["id"]


@pytest.mark.asyncio
async def test_ingest_tags_mentions_with_source_and_run(state):
    report = await run_collectors(state, [FixtureCollector()])

    [mention] = await state.list_mentions(limit=1)
    source = await state.get_source_by_collector("fixture")
    assert source is not None
    assert mention.source_id == source["id"]
    assert mention.run_id == report.results[0].run_id
    assert source["last_run_at"] is not None


@pytest.mark.asyncio
async def test_source_row_is_reused_across_runs(state):
    await run_collectors(state, [FixtureCollector()])
    first = await state.get_source_by_collector("fixture")

    await run_collectors(state, [FixtureCollector()])

    assert (await state.get_source_by_collector("fixture"))["id"] == first["id"]
    assert len(await state.list_sources()) == 1


@pytest.mark.asyncio
async def test_failing_collector_does_not_stop_others(state):
    good = _Static([Mention(platform=Platform.WEB, url="https://example.invalid/ok/1", text="fine")])

    report = await run_collectors(state, [_Boom(), good])

    boom, static = report.results
    assert boom.error and "upstream exploded" in boom.error
    assert boom.created == 1  # what it yielded before failing is kept
    assert static.created == 1 and static.error == ""
    runs = {r["stage"]: r for r in await state.get_runs()}
    assert runs["boom"]["status"] == "failed"
    assert "upstream exploded" in runs["boom"]["error"]
    assert runs["static"]["status"] == "completed"


@pytest.mark.asyncio
async def test_per_mention_store_error_is_counted_not_fatal(state, monkeypatch):
    mentions = [
        Mention(platform=Platform.WEB, url="https://example.invalid/a", text="a"),
        Mention(platform=Platform.WEB, url="https://example.invalid/b", text="b"),
    ]
    real_upsert = state.upsert_mention

    async def flaky(mention):
        if mention.url.endswith("/a"):
            raise RuntimeError("disk full")
        return await real_upsert(mention)

    monkeypatch.setattr(state, "upsert_mention", flaky)

    report = await run_collectors(state, [_Static(mentions)])

    assert (report.created, report.errors) == (1, 1)


@pytest.mark.asyncio
async def test_collected_audit_written_once_per_new_mention(state):
    await run_collectors(state, [FixtureCollector()])
    await run_collectors(state, [FixtureCollector()])

    for mention in await state.list_mentions(limit=500):
        events = await state.list_audit(mention.id)
        collected = [e for e in events if e.event is AuditEventType.COLLECTED]
        assert len(collected) == 1
        assert collected[0].actor == "collector:fixture"
        assert collected[0].permalink == mention.url


# --- CLI ------------------------------------------------------------------


def test_cli_ingest_fixture_prints_report(tmp_path, monkeypatch, capsys):
    import harvey.state
    from harvey import cli

    monkeypatch.setattr(harvey.state, "DB_PATH", tmp_path / "cli.db")
    monkeypatch.setattr(sys, "argv", ["pulse", "ingest", "--fixture"])

    cli.main()

    out = capsys.readouterr().out
    assert "fixture" in out
    assert "created" in out.lower()
    assert (tmp_path / "cli.db").exists()


def test_cli_ingest_fixture_accepts_directory(tmp_path, monkeypatch, capsys):
    import harvey.state
    from harvey import cli

    fixture_dir = tmp_path / "fx"
    fixture_dir.mkdir()
    (fixture_dir / "one.jsonl").write_text(json.dumps(
        {"platform": "reddit", "external_id": "t3_x", "url": "https://www.reddit.com/r/x/comments/x/",
         "author_handle": "u/fx", "text": "hello"}
    ), encoding="utf-8")
    monkeypatch.setattr(harvey.state, "DB_PATH", tmp_path / "cli.db")
    monkeypatch.setattr(sys, "argv", ["pulse", "ingest", "--fixture", str(fixture_dir)])

    cli.main()

    assert "1 created" in capsys.readouterr().out


def test_cli_ingest_without_source_exits_nonzero(monkeypatch, capsys):
    from harvey import cli

    monkeypatch.setattr(sys, "argv", ["pulse", "ingest"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
