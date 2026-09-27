"""Collector framework (registry) and the JSONL fixture collector."""

import json
from datetime import datetime

import pytest

from harvey.collectors import REGISTRY, Collector, get_collector, register
from harvey.collectors.fixture import DEFAULT_FIXTURE_DIR, FixtureCollector
from harvey.models import Mention, Platform
from harvey.paths import PROJECT_ROOT


def _write_jsonl(directory, rows, name="posts.jsonl"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _post(**overrides) -> dict:
    row = {
        "platform": "reddit",
        "external_id": "t3_one",
        "url": "https://www.reddit.com/r/Semaglutide/comments/one/",
        "author_handle": "u/fixture_one",
        "text": "Has anyone tried WellPeps?",
    }
    row.update(overrides)
    return row


async def _collect(collector, since=None) -> list[Mention]:
    return [m async for m in collector.collect(since)]


# --- Registry ---------------------------------------------------------------


def test_fixture_collector_is_registered():
    assert REGISTRY["fixture"] is FixtureCollector


def test_get_collector_builds_instance_with_config(tmp_path):
    collector = get_collector("fixture", directory=str(tmp_path))

    assert isinstance(collector, FixtureCollector)
    assert collector.directory == tmp_path


def test_get_collector_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown collector"):
        get_collector("no-such-collector")


def test_register_rejects_duplicate_name():
    with pytest.raises(ValueError, match="already registered"):

        @register
        class _Dupe(FixtureCollector):
            name = "fixture"


def test_register_rejects_blank_name():
    with pytest.raises(ValueError, match="name"):

        @register
        class _Blank(Collector):
            name = ""
            platform_default = Platform.OTHER
            cost_note = "free"

            async def collect(self, since=None):
                yield  # pragma: no cover


def test_collectors_declare_platform_and_cost_note():
    for cls in REGISTRY.values():
        assert cls.name
        assert isinstance(cls.platform_default, Platform)
        assert cls.cost_note.strip()


# --- Fixture mapping --------------------------------------------------------


def test_default_fixture_dir_is_tests_fixtures_mentions():
    assert DEFAULT_FIXTURE_DIR == PROJECT_ROOT / "tests" / "fixtures" / "mentions"


@pytest.mark.asyncio
async def test_fixture_maps_raw_post_to_mention(tmp_path):
    _write_jsonl(tmp_path, [_post(
        title="WellPeps?",
        posted_at="2026-09-20T14:03:00Z",
        parent_external_id="t3_parent",
        engagement={"upvotes": 12, "comments": 3},
    )])

    [mention] = await _collect(FixtureCollector(directory=tmp_path))

    assert mention.platform is Platform.REDDIT
    assert mention.external_id == "t3_one"
    assert mention.url == "https://www.reddit.com/r/Semaglutide/comments/one/"
    assert mention.author_handle == "u/fixture_one"
    assert mention.text == "Has anyone tried WellPeps?"
    assert mention.title == "WellPeps?"
    assert mention.parent_external_id == "t3_parent"
    assert mention.engagement == {"upvotes": 12, "comments": 3}
    # Stored as naive UTC, like every other timestamp.
    assert mention.posted_at == datetime(2026, 9, 20, 14, 3)


@pytest.mark.asyncio
async def test_fixture_drops_every_pii_field(tmp_path):
    _write_jsonl(tmp_path, [_post(
        author_name="Jess Fixture",
        author_email="jess.fixture@example.invalid",
        avatar_url="https://cdn.example.invalid/avatar.png",
        profile_url="https://www.reddit.com/user/fixture_one",
        location="Springfield",
    )])

    [mention] = await _collect(FixtureCollector(directory=tmp_path))

    dumped = mention.model_dump_json()
    for leaked in ("Jess Fixture", "example.invalid", "avatar", "/user/fixture_one", "Springfield"):
        assert leaked not in dumped
    assert mention.author_handle == "u/fixture_one"


@pytest.mark.asyncio
async def test_fixture_skips_non_http_url_without_crashing(tmp_path, caplog):
    _write_jsonl(tmp_path, [
        _post(external_id="bad", url="javascript:alert(1)"),
        _post(external_id="bad2", url="ftp://example.invalid/post"),
        _post(external_id="good"),
    ])
    collector = FixtureCollector(directory=tmp_path)

    mentions = await _collect(collector)

    assert [m.external_id for m in mentions] == ["good"]
    assert collector.skipped == 2
    assert "skipping" in caplog.text.lower()


@pytest.mark.asyncio
async def test_fixture_skips_malformed_lines(tmp_path):
    _write_jsonl(tmp_path, [
        "{not json",
        "",
        json.dumps(["a", "list"]),
        json.dumps({"platform": "reddit", "text": "no url at all"}),
        _post(external_id="good"),
    ])
    collector = FixtureCollector(directory=tmp_path)

    mentions = await _collect(collector)

    assert [m.external_id for m in mentions] == ["good"]
    assert collector.skipped == 3


@pytest.mark.asyncio
async def test_fixture_unknown_platform_maps_to_other(tmp_path):
    _write_jsonl(tmp_path, [_post(platform="myspace")])

    [mention] = await _collect(FixtureCollector(directory=tmp_path))

    assert mention.platform is Platform.OTHER


@pytest.mark.asyncio
async def test_fixture_since_filters_older_posts(tmp_path):
    _write_jsonl(tmp_path, [
        _post(external_id="old", url="https://x.com/a/status/1", posted_at="2026-09-01T00:00:00Z"),
        _post(external_id="new", url="https://x.com/a/status/2", posted_at="2026-09-25T00:00:00Z"),
        _post(external_id="undated", url="https://x.com/a/status/3"),
    ])

    mentions = await _collect(FixtureCollector(directory=tmp_path), since=datetime(2026, 9, 10))

    assert sorted(m.external_id for m in mentions) == ["new", "undated"]


@pytest.mark.asyncio
async def test_fixture_missing_directory_yields_nothing(tmp_path):
    assert await _collect(FixtureCollector(directory=tmp_path / "nope")) == []


# --- The shipped sample fixture ----------------------------------------------


@pytest.mark.asyncio
async def test_sample_fixture_loads_with_expected_mix():
    collector = FixtureCollector()

    mentions = await _collect(collector)

    assert len(mentions) >= 22
    assert collector.skipped == 1  # the one non-http url
    platforms = {m.platform for m in mentions}
    assert {
        Platform.REDDIT, Platform.INSTAGRAM, Platform.FACEBOOK,
        Platform.TIKTOK, Platform.TRUSTPILOT,
    } <= platforms
    assert all(m.url.startswith(("http://", "https://")) for m in mentions)


def test_sample_fixture_contains_pii_fields_to_prove_they_are_dropped():
    raw = (PROJECT_ROOT / "tests" / "fixtures" / "mentions" / "sample.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]

    assert any("author_email" in r or "author_name" in r for r in rows)
    assert any("avatar_url" in r for r in rows)
