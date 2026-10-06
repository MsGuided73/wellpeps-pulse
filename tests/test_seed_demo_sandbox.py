"""scripts/seed_demo.py --sandbox: every demo mention lives in the DEMO sandbox,
and a DEMO config copy lets the approve -> post loop run in demos."""

import asyncio
import importlib.util
from pathlib import Path

import pytest
import yaml

from harvey import knowledge
from harvey.models import MentionStatus
from harvey.sandbox import demo_config, urls as sandbox_urls
from harvey.sandbox.store import SandboxStore
from harvey.state import StateManager
from harvey.urls import normalize_url

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"
SCENARIO = ("anyone tried compounded tirzepatide via telehealth? Trying to figure out which providers "
            "include the follow-up visits in the price.")
BASE = "http://127.0.0.1:5555"


def _load():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo")
    paths = {"db": str(tmp / "demo.db"), "sandbox": tmp / "sandbox.db", "config": tmp / "demo-config"}
    asyncio.run(_load().seed(paths["db"], sandbox_db=paths["sandbox"], config_dir=paths["config"],
                             base_url=BASE))
    knowledge.reload()
    return paths


def _mentions(db: str) -> list:
    async def go():
        state = StateManager(db)
        async with state.connect() as conn:
            async with conn.execute("SELECT id, platform, external_id, url, url_norm, text, title, status "
                                    "FROM mentions") as cur:
                return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def test_every_demo_mention_is_a_post_or_comment_in_the_sandbox(seeded):
    store = SandboxStore(seeded["sandbox"])
    rows = _mentions(seeded["db"])
    assert len(rows) > 200
    for row in rows:
        assert row["url"].startswith(BASE + "/sandbox/"), row["url"]
        ref = sandbox_urls.parse(row["url"])
        assert ref is not None, row["url"]
        thread = store.thread(ref.thread_id)
        assert thread is not None and thread["kind"] == ref.kind
        if ref.kind == "forum":
            assert thread["community"] == ref.community
        if ref.comment_id is None:
            assert thread["body"] == row["text"]
        else:
            comment = store.comment(ref.comment_id)
            assert comment["thread_id"] == thread["id"] and comment["body"] == row["text"]
            assert row["url"].endswith(f"#c{ref.comment_id}")
        assert len(store.comments(thread["id"])) >= 2, "every thread has fictional context"


def test_url_norm_dedupe_still_holds(seeded):
    rows = _mentions(seeded["db"])
    norms = [r["url_norm"] for r in rows]
    assert len(norms) == len(set(norms))
    ig = [r for r in rows if r["external_id"] == "ig-c-17900000000000001"]
    assert len(ig) == 1  # the fixture's tracking-param duplicate collapsed into it
    assert "#" not in ig[0]["url_norm"] and normalize_url(ig[0]["url"]) == ig[0]["url_norm"]
    assert not any("javascript:" in r["url"] for r in rows)


def test_scenario_thread_exists_with_community_replies(seeded):
    store = SandboxStore(seeded["sandbox"])
    threads = [t for t in store.threads("tirzepatide_talk", limit=500) if t["body"] == SCENARIO]
    assert len(threads) == 1
    replies = store.comments(threads[0]["id"])
    assert 3 <= len(replies) <= 8
    assert any(c["parent_id"] for c in replies), "some replies are nested"
    assert not any(c["is_brand"] for c in replies)
    scenario = next(r for r in _mentions(seeded["db"]) if r["text"] == SCENARIO)
    assert sandbox_urls.parse(scenario["url"]).thread_id == threads[0]["id"]
    assert scenario["status"] == MentionStatus.IN_REVIEW.value


def test_sandbox_spans_the_demo_sites_and_communities(seeded):
    store = SandboxStore(seeded["sandbox"])
    communities = {c["name"]: c for c in store.communities()}
    for name in ("tirzepatide_talk", "glp1_journey", "hairloss_help", "telehealth_reviews", "menshealth_q"):
        assert name in communities
    kinds = {c["kind"] for c in communities.values()}
    assert kinds == {"forum", "photo", "review"}
    photo_threads = [t for c in communities.values() if c["kind"] == "photo"
                     for t in store.threads(c["name"], limit=500)]
    assert any(t["author"] == "slimscript_rx" for t in photo_threads), "a fictional competitor promo post"


def test_demo_config_marks_claims_approved_for_demos_only(seeded):
    directory = seeded["config"]
    claims = yaml.safe_load((directory / "claims.yaml").read_text(encoding="utf-8"))["claims"]
    assert claims and all(c["approved_by"] == demo_config.DEMO_APPROVER for c in claims)
    links = yaml.safe_load((directory / "links.yaml").read_text(encoding="utf-8"))["links"]
    assert all(link["live"] is True for link in links)
    assert demo_config.is_demo_config(directory)
    real = yaml.safe_load((knowledge.PROJECT_ROOT / "config" / "claims.yaml").read_text(encoding="utf-8"))
    assert any(c["approved_by"] == "PENDING" for c in real["claims"]), "the real config is untouched"


def test_demo_config_refuses_the_real_config_dir():
    with pytest.raises(ValueError):
        demo_config.write_demo_config(knowledge.PROJECT_ROOT / "config")


def test_sandbox_flag_parsing(monkeypatch):
    seed_demo = _load()
    monkeypatch.setenv("PULSE_DEMO_SANDBOX", "")
    assert seed_demo._wants_sandbox(["--sandbox"]) is True
    assert seed_demo._wants_sandbox([]) is False
    monkeypatch.setenv("PULSE_DEMO_SANDBOX", "true")
    assert seed_demo._wants_sandbox([]) is True


def test_sandbox_seed_refuses_the_real_database_paths(tmp_path):
    seed_demo = _load()
    with pytest.raises(SystemExit):
        seed_demo._check_sandbox_target(knowledge.PROJECT_ROOT / "data" / "pulse.db", str(tmp_path / "demo.db"))
    with pytest.raises(SystemExit):
        seed_demo._check_sandbox_target(tmp_path / "demo.db", str(tmp_path / "demo.db"))


def test_sandbox_seed_needs_a_database_without_mentions(seeded, tmp_path):
    # Existing mentions would keep their old permalinks (dedupe), so refuse.
    with pytest.raises(SystemExit):
        asyncio.run(_load().seed(seeded["db"], sandbox_db=tmp_path / "sb.db", config_dir=tmp_path / "cfg"))
    assert not (tmp_path / "sb.db").exists()
