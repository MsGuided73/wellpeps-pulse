"""scripts/seed_demo.py --claude: hand-written demo posts (and the briefs) go
through the real pipeline with a real brain; synthetic history keeps the fake.
A fake "real" brain is injected: no claude CLI calls here."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from harvey.batching import run_by_thread
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager

SCRIPT = PROJECT_ROOT / "scripts" / "seed_demo.py"
FAIL_POST = "Is WellPeps legit? Thinking about signing up for their weight loss program but want to hear from real members first."
COMPLAINT_POST = "Do NOT sign up for WellPeps. Customer service ghosted me for a month and my order never shipped."
ACK = "Thanks for flagging the slow replies you're describing."


def _load():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClaude:
    """Stands in for harvey.brain.Brain: records every call and the peak
    number of calls in flight; answers like the demo fake (plus an
    acknowledgement), and fails (None) for one post."""

    def __init__(self, seed_demo, fail_post: str = FAIL_POST):
        self.seed_demo, self.fail_post = seed_demo, fail_post
        self.fake = seed_demo.DemoBrain(guide_links=False)
        self.calls: list[tuple[str, str, str]] = []
        self.active = self.peak = 0

    def model_for(self, agent, task):
        return "claude-test-sonnet"

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.005)
            text = self.seed_demo._mention_text(prompt)
            self.calls.append((agent, task, text))
            if self.fail_post and self.fail_post in text:
                return None
            if agent == "drafter" and task == "acknowledge":
                return {"acknowledgement": ACK}
            return await self.fake.think_json(prompt, session_id=session_id, agent=agent, task=task)
        finally:
            self.active -= 1


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    seed_demo = _load()
    tmp = tmp_path_factory.mktemp("claude-demo")
    db, sheet = str(tmp / "demo.db"), tmp / "sheet.md"
    holder = {}

    def factory(state):
        holder["brain"] = FakeClaude(seed_demo)
        return holder["brain"]

    lines: list[str] = []
    asyncio.run(seed_demo.seed(db, real_brain=factory, review_sheet=sheet, out=lines.append))
    return {"module": seed_demo, "db": db, "sheet": sheet, "brain": holder["brain"], "out": lines}


def _rows(db: str, sql: str, params=()) -> list[dict]:
    async def go():
        async with StateManager(db).connect() as conn:
            async with conn.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def test_only_hand_written_posts_and_briefs_reach_the_real_brain(seeded):
    posts = seeded["module"].hand_written_posts()
    calls = seeded["brain"].calls
    assert len(posts) >= 20
    assert {agent for agent, _, _ in calls} >= {"triager", "drafter", "reviewer", "pulse"}
    for agent, _, text in calls:
        if agent == "pulse":
            assert text == ""                                    # aggregates only
        else:
            assert any(post in text for post in posts), text[:80]
    assert sum(agent == "pulse" for agent, _, _ in calls) == 2   # daily + weekly brief
    synthetic = [s for s in seeded["module"].STEADY]
    assert not any(s in text for _, _, text in calls for s in synthetic)


def test_at_most_three_real_calls_at_a_time(seeded):
    assert 1 <= seeded["brain"].peak <= seeded["module"].CLAUDE_CONCURRENCY == 3


def test_real_drafts_carry_the_real_model_and_synthetic_ones_the_fake(seeded):
    rows = _rows(seeded["db"], "SELECT m.text, m.external_id, d.model FROM drafts d JOIN mentions m ON m.id = d.mention_id")
    posts = set(seeded["module"].hand_written_posts())
    real = [r for r in rows if r["text"].strip() in posts]
    synthetic = [r for r in rows if r["external_id"].startswith("demo-")]
    assert real and synthetic
    assert {r["model"] for r in synthetic} <= {"demo-fake", "approved-response"}
    models = {r["model"] for r in real if r["text"].strip() != FAIL_POST}
    assert "claude-test-sonnet" in models
    assert not any(m == "demo-fake" for m in models)


def test_complaint_gets_the_real_acknowledgement(seeded):
    rows = _rows(seeded["db"], "SELECT d.text, d.model FROM drafts d JOIN mentions m ON m.id = d.mention_id "
                               "WHERE m.text LIKE ?", (COMPLAINT_POST[:40] + "%",))
    assert rows and ACK in rows[-1]["text"] and rows[-1]["model"] == "approved-response+ack:claude-test-sonnet"


def test_a_failed_claude_call_falls_back_to_the_fake_and_is_marked(seeded):
    rows = _rows(seeded["db"], "SELECT d.model, d.text FROM drafts d JOIN mentions m ON m.id = d.mention_id "
                               "WHERE m.text = ?", (FAIL_POST,))
    assert rows and all(r["model"] == seeded["module"].FALLBACK_MODEL for r in rows)
    assert rows[-1]["text"].startswith("I work with WellPeps.")
    out = "\n".join(seeded["out"])
    assert "FAILED -> demo fake" in out and "failed -> demo fake for" in out
    triage = _rows(seeded["db"], "SELECT t.subject FROM triage t JOIN mentions m ON m.id = t.mention_id "
                                 "WHERE m.text = ?", (FAIL_POST,))
    assert triage[0]["subject"] == seeded["module"].BANNER          # the fake's own DEMO marker


def test_review_sheet_lists_every_hand_written_post(seeded):
    sheet = Path(seeded["sheet"]).read_text(encoding="utf-8")
    assert sheet.startswith("# DEMO review sheet (real Claude)")
    for post in seeded["module"].hand_written_posts():
        assert " ".join(post.split())[:60] in sheet
    assert sheet.count("- Final reply:") >= 20 and "Reviewer verdict:" in sheet


def test_fake_drafter_uses_the_guide_disclosure():
    seed_demo = _load()
    assert seed_demo.DEMO_REPLY.startswith("I work with WellPeps.")
    assert "not neutral" not in seed_demo.DEMO_REPLY and "Disclosure:" not in seed_demo.DEMO_REPLY
    assert seed_demo.DEMO_CLAIMS[0] == "CLM-AMG-04-WORK-WITH"


def test_claude_flag_and_launcher_switch():
    seed_demo = _load()
    factory = seed_demo._real_brain_factory()
    from harvey.brain import Brain

    assert isinstance(factory(StateManager(":memory:")), Brain)       # built, never called
    ps1 = (PROJECT_ROOT / "scripts" / "run_demo.ps1").read_text(encoding="utf-8")
    assert "[switch]$Claude" in ps1 and "--claude" in ps1


# --- harvey/batching.py ----------------------------------------------------------------------


def test_run_by_thread_keeps_threads_in_order_and_caps_parallelism():
    seen: list[str] = []
    active = {"now": 0, "peak": 0}

    async def worker(item):
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.01)
        seen.append(item)
        active["now"] -= 1

    items = ["a1", "b1", "a2", "c1", "b2", "a3", "d1", "e1"]
    asyncio.run(run_by_thread(items, lambda s: s[0], worker, 2))
    assert sorted(seen) == sorted(items) and active["peak"] <= 2
    for thread in "ab":
        order = [s for s in seen if s[0] == thread]
        assert order == sorted(order)


def test_run_by_thread_stops_when_a_worker_says_so():
    done: list[int] = []

    async def worker(item):
        done.append(item)
        return item != 2

    asyncio.run(run_by_thread([1, 2, 3, 4, 5], lambda i: "same", worker, 3))
    assert done == [1, 2]
