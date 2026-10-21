"""scripts/harness_compare.py: works on a copy, records nothing, subscription only.
Built and tested with a scripted brain; never run against Claude here."""

import asyncio
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from harvey import knowledge_docs
from harvey.models import Category, Mention, MentionStatus, Platform, Triage, Urgency
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager

SCRIPT = PROJECT_ROOT / "scripts" / "harness_compare.py"
PARA = ("This paragraph explains what the community team does in this part of the work and why it matters for "
        "the person reading the reply, in plain words and without any numbers, so a reviewer can follow it.")
VOICE = f"# Voice principles\nAnswer the actual question in a calm, plain tone. {PARA}"
CLEAN = ("Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any provider are "
         "whether you are actually reviewed by a licensed clinician, what follow-up is included, how dose "
         "adjustments are handled, and which pharmacy dispenses the medication.")


@pytest.fixture(scope="module")
def compare():
    spec = importlib.util.spec_from_file_location("harness_compare", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptedBrain:
    def __init__(self, state):
        self.state = state
        self.calls: list[str] = []

    def model_for(self, agent, task):
        return "fake-sonnet"

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.calls.append(agent)
        if agent == "reviewer":
            return {"verdict": "pass", "reasons": [{"rule_id": "VOICE", "explanation": "a little stiff"}]}
        return {"reply": CLEAN, "claim_ids": ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"],
                "rationale": "r", "needs_human_reason": None}


async def _seed(path: Path) -> int:
    state = StateManager(str(path))
    await state.init_db()
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="cmp1",
                                                text="Love how calm these hair loss threads are, thanks WellPeps",
                                                url="https://www.reddit.com/r/t/comments/cmp1/"))
    await state.save_triage(Triage(mention_id=mid, category=Category.PRAISE, reply_appropriate=True,
                                   urgency=Urgency.NORMAL, subject_type="wellpeps"))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    doc = await knowledge_docs.create_document(state, {
        "filename": "Community Agent Voice.md", "kind": "voice_style", "authority": "must_follow",
        "approved_by": "Compliance Lead", "programs": [],
        "content_base64": __import__("base64").b64encode(VOICE.encode()).decode()}, "admin@pulse.test")
    await knowledge_docs.decide(state, doc["id"], True, "admin@pulse.test")
    return mid


def _counts(path: Path) -> dict:
    with sqlite3.connect(path) as db:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("drafts", "audit_log", "mentions")}


def test_compares_off_and_on_on_a_copy_and_records_nothing(compare, tmp_path, capsys):
    source = tmp_path / "source.db"
    mid = asyncio.run(_seed(source))
    before = _counts(source)
    raw_before = source.read_bytes()
    copy = tmp_path / "copy" / "compare.db"
    brains = []

    code = compare.main([str(mid), "--source", str(source), "--copy-to", str(copy)],
                        brain_factory=lambda state: brains.append(ScriptedBrain(state)) or brains[-1])

    out = capsys.readouterr().out
    assert code == 0
    assert "--- harness OFF ---" in out and "--- harness ON ---" in out
    assert "sections (1): Community Agent Voice > Voice principles" in out
    assert "reviewer: pass" in out and "VOICE notes: a little stiff" in out
    assert source.read_bytes() == raw_before and _counts(source) == before      # source untouched
    assert _counts(copy)["drafts"] == 0 and _counts(copy)["audit_log"] == before["audit_log"]   # nothing recorded
    assert brains and str(brains[0].state.db_path) == str(copy)
    assert brains[0].calls.count("drafter") == 2 and brains[0].calls.count("reviewer") == 2


def test_unknown_or_untriaged_mentions_are_skipped(compare, tmp_path, capsys):
    source = tmp_path / "source.db"
    asyncio.run(_seed(source))

    code = compare.main(["999", "--source", str(source), "--copy-to", str(tmp_path / "c.db")],
                        brain_factory=ScriptedBrain)

    assert code == 0 and "Mention 999: skipped" in capsys.readouterr().out


def test_refuses_to_write_over_the_source_or_live_db(compare, tmp_path):
    source = tmp_path / "source.db"
    asyncio.run(_seed(source))

    with pytest.raises(compare.CompareError):
        compare.copy_database(source, source)
    with pytest.raises(compare.CompareError):
        compare.copy_database(source, compare.LIVE_DB)
    existing = tmp_path / "taken.db"
    existing.write_bytes(b"x")
    with pytest.raises(compare.CompareError):
        compare.copy_database(source, existing)


def test_the_default_copy_is_a_new_temp_file_outside_data(compare):
    path = compare.default_copy_path()

    assert not path.exists()
    assert (PROJECT_ROOT / "data") not in path.resolve().parents


def test_real_claude_needs_subscription_billing(compare, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PULSE_CLAUDE_BILLING", "api")

    code = compare.main(["1", "--source", str(tmp_path / "missing.db")])

    assert code == 2 and "PULSE_CLAUDE_BILLING=subscription" in capsys.readouterr().err
