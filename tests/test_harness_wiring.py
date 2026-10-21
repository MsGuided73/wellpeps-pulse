"""Context harness wiring (docs/HARNESS-HANDOFF.md §2 and §4): drafter, reviewer, the
drafting pipeline and what is recorded. Temp DB; no `claude` calls (scripted brains)."""

import json
import re

import pytest

from harvey import harness, knowledge_docs, review
from harvey.agents import prompting
from harvey.agents import reviewer as reviewer_module
from harvey.agents.drafter import Drafter, build_prompt, candidate_claims, programs_for
from harvey.agents.reviewer import Reviewer
from harvey.config import HarnessConfig, PulseConfig
from harvey.drafting import draft_batch
from harvey.models import AuditEventType, Category, Mention, MentionStatus, Platform, Triage, Urgency
from tests.test_knowledge_docs import add_active, env, run  # noqa: F401  (env is a fixture)

ON = HarnessConfig(enabled=True)
NONCE = "0123456789abcdef"
SECRET_PHRASE = "zebra-lantern guidance phrase"   # appears only in library text
PARA = ("This paragraph explains what the community team does in this part of the work and why it matters for "
        "the person reading the reply, in plain words and without any numbers, so a reviewer can follow it.")
VOICE = (f"Community Agent Voice\nThis document describes how the WellPeps community team should sound in public "
         f"communities, on every platform. {PARA}\n\nVoice principles\nAnswer the actual question in a calm, plain "
         f"tone, in the person's own terms, before anything else. {SECRET_PHRASE}. {PARA}\n\nAVOID LIST\nMarketing "
         f"language, scripted empathy and stock closings. {PARA}")
HAIR = f"# Hair restoration questions\nHair loss posts often ask about topical finasteride. {PARA}"
WEIGHT = f"# Weight loss questions\nWeight loss posts often ask about GLP-1 programs. WEIGHT-ONLY NOTE. {PARA}"
CLEAN = ("Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any provider are "
         "whether you are actually reviewed by a licensed clinician, what follow-up is included, how dose "
         "adjustments are handled, and which pharmacy dispenses the medication.")
CLEAN_IDS = ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"]


class Brain:
    """Like harvey.brain.Brain: a state, and JSON answers per agent."""

    def __init__(self, state, *, verdict="pass", reasons=()):
        self.state = state
        self.prompts: dict[str, list[str]] = {}
        self.verdict, self.reasons = verdict, list(reasons)

    def model_for(self, agent, task):
        return "fake-sonnet"

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.prompts.setdefault(agent, []).append(prompt)
        if agent == "reviewer":
            return {"verdict": self.verdict, "reasons": self.reasons}
        return {"reply": CLEAN, "claim_ids": CLEAN_IDS, "rationale": "r", "needs_human_reason": None}


def library(state):
    add_active(state, "Community Agent Voice.md", VOICE, authority="must_follow")
    add_active(state, "Hair FAQ.md", HAIR, kind="facts_faq")
    add_active(state, "Weight FAQ.md", WEIGHT, kind="facts_faq", programs=["Weight Loss"])


def mention(text="Has anyone tried topical finasteride for hair loss? Worth it?"):
    return Mention(id=1, platform=Platform.REDDIT, text=text, url="https://www.reddit.com/r/test/comments/w1/")


def triage(category=Category.QUESTION):
    return Triage(mention_id=1, category=category, reply_appropriate=True, subject_type="category",
                  drug="finasteride")


def fixed_nonce(monkeypatch):
    monkeypatch.setattr(prompting, "new_nonce", lambda: NONCE)


# --- Drafter ---------------------------------------------------------------------------------------------


def test_harness_off_drafter_prompt_is_byte_identical_to_today(env, monkeypatch):
    fixed_nonce(monkeypatch)
    library(env)
    plain, off = Brain(env), Brain(env)
    m, t = mention(), triage()

    run(Drafter(plain).draft(m, t))
    proposal = run(Drafter(off, harness=HarnessConfig(enabled=False)).draft(m, t))

    post = f"{m.title or ''} {m.text}".strip()
    programs = programs_for(t.product, t.drug, post)
    claims = candidate_claims(t.product, drug=t.drug, category=t.category, programs=programs)
    expected = build_prompt(m, t, claims) + "\n\n" + run(knowledge_docs.reference_block(env, programs))
    assert plain.prompts["drafter"][0] == off.prompts["drafter"][0] == expected
    assert proposal.context_sections == ()


def test_harness_on_drafter_gets_relevant_sections_instead_of_the_first_6000_chars(env):
    library(env)
    brain = Brain(env)

    proposal = run(Drafter(brain, harness=ON).draft(mention(), triage()))

    prompt = brain.prompts["drafter"][0]
    assert "[must_follow: voice_style] Community Agent Voice > Voice principles\n" in prompt
    assert "Hair FAQ > Hair restoration questions" in prompt
    assert "WEIGHT-ONLY NOTE" not in prompt
    assert "[must_follow: voice_style] Community Agent Voice.md\n" not in prompt   # not today's whole-doc entry
    assert "facts and claims still only from the approved claims list" in prompt
    assert prompt.index("END_UNTRUSTED_MENTION") < prompt.index("BEGIN_REFERENCE_DOCUMENTS")
    assert proposal.context_sections and proposal.context_sections[0].endswith(":community-agent-voice")
    assert [r["section_id"] for r in proposal.context_refs] == list(proposal.context_sections)
    assert all(set(r) == {"section_id", "doc_id", "doc_name", "heading"} for r in proposal.context_refs)


def test_drafter_block_respects_the_drafter_budget(env):
    library(env)
    brain = Brain(env)
    tight = HarnessConfig(enabled=True, budgets={"drafter": 1500})

    run(Drafter(brain, harness=tight).draft(mention(), triage()))

    prompt = brain.prompts["drafter"][0]
    block = prompt[prompt.rindex("## WellPeps reference documents"):]
    assert "BEGIN_REFERENCE_DOCUMENTS" in block and len(block) <= 1500


def test_a_harness_error_falls_back_to_todays_block(env, monkeypatch):
    library(env)

    async def boom(state):
        raise RuntimeError("harness broke")

    monkeypatch.setattr(harness, "load_sections", boom)
    brain = Brain(env)

    proposal = run(Drafter(brain, harness=ON).draft(mention(), triage()))

    assert "[must_follow: voice_style] Community Agent Voice.md\n" in brain.prompts["drafter"][0]
    assert proposal.reply and proposal.context_sections == ()


def test_harness_on_with_an_empty_library_adds_nothing(env):
    brain = Brain(env)

    proposal = run(Drafter(brain, harness=ON).draft(mention(), triage()))

    assert "REFERENCE_DOCUMENTS" not in brain.prompts["drafter"][0]
    assert proposal.context_sections == ()


# --- Reviewer --------------------------------------------------------------------------------------------


def review_args():
    return (CLEAN, "reddit", mention(), [])


def test_reviewer_prompt_is_unchanged_without_sections(env, monkeypatch):
    fixed_nonce(monkeypatch)
    library(env)
    plain, on = Brain(env), Brain(env)

    run(Reviewer(plain).review(*review_args()))
    run(Reviewer(on, harness=ON).review(*review_args(), context_sections=()))

    expected = reviewer_module.build_prompt(*review_args())
    assert plain.prompts["reviewer"][0] == on.prompts["reviewer"][0] == expected
    assert "REFERENCE_DOCUMENTS" not in expected


def test_reviewer_gets_exactly_the_drafters_sections(env):
    library(env)
    brain = Brain(env)
    proposal = run(Drafter(brain, harness=ON).draft(mention(), triage()))

    run(Reviewer(brain, harness=ON).review(*review_args(), context_sections=proposal.context_sections))

    prompt = brain.prompts["reviewer"][0]
    assert "## Reference sections the drafter used (guidance, not facts)" in prompt
    sections = harness.by_ids(run(harness.load_sections(env)), proposal.context_sections)
    titles = re.findall(r"^\[(?:must_follow|reference): \w+\] (.+)$", prompt, re.M)
    assert titles == [s.title for s in sections]
    nonce = re.search(r"BEGIN_REFERENCE_DOCUMENTS (\w+)", prompt).group(1)
    assert prompt.index(f"END_REFERENCE_DOCUMENTS {nonce}") < prompt.index("## Verdict")


def test_reviewer_ignores_sections_while_the_harness_is_off(env):
    library(env)
    brain = Brain(env)
    ids = harness.select(run(harness.load_sections(env)), harness.TaskContext(agent="drafter"), 8000).section_ids

    run(Reviewer(brain).review(*review_args(), context_sections=ids))

    assert ids and "REFERENCE_DOCUMENTS" not in brain.prompts["reviewer"][0]


def test_a_reviewer_harness_error_means_no_block_not_a_failed_review(env, monkeypatch):
    library(env)

    async def boom(state):
        raise RuntimeError("harness broke")

    monkeypatch.setattr(harness, "load_sections", boom)
    brain = Brain(env)

    result = run(Reviewer(brain, harness=ON).review(*review_args(), context_sections=("16:1:voice",)))

    assert result.verdict.value == "pass"
    assert "REFERENCE_DOCUMENTS" not in brain.prompts["reviewer"][0]


# --- Pipeline and records --------------------------------------------------------------------------------


class SpyReviewer:
    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple] = []

    async def review(self, reply, platform, mention, claims, guidance=None, **kwargs):
        self.calls.append(tuple(kwargs.get("context_sections", ())))
        return await self.inner.review(reply, platform, mention, claims, guidance=guidance, **kwargs)


async def _triaged(state, text="Love how calm the hair loss threads are here, thanks WellPeps") -> int:
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="hw1", text=text,
                                                url="https://www.reddit.com/r/t/comments/hw1/"))
    await state.save_triage(Triage(mention_id=mid, category=Category.PRAISE, reply_appropriate=True,
                                   urgency=Urgency.NORMAL, subject_type="wellpeps"))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    return mid


def test_pipeline_records_the_sections_and_hands_the_reviewer_the_same_ids(env):
    library(env)
    mid = run(_triaged(env))
    brain = Brain(env)
    spy = SpyReviewer(Reviewer(brain, harness=ON))

    report = run(draft_batch(env, Drafter(brain, harness=ON), spy))

    assert report.drafted == 1 and report.errors == 0
    draft = run(env.get_latest_draft(mid))
    ids = tuple(r["section_id"] for r in draft.context)
    assert ids and spy.calls == [ids]
    drafted = [e for e in run(env.list_audit(mid)) if e.event is AuditEventType.DRAFTED]
    assert drafted[-1].verdict["context_sections"] == list(ids)
    detail = run(review.detail(env, mid, PulseConfig()))
    assert detail["latest_draft"]["context"] == draft.context


def test_no_document_text_reaches_any_audit_row(env):
    library(env)
    mid = run(_triaged(env))
    brain = Brain(env)
    run(draft_batch(env, Drafter(brain, harness=ON), Reviewer(brain, harness=ON)))

    assert SECRET_PHRASE in brain.prompts["drafter"][0]          # it was used ...
    async def all_rows():
        async with env.connect() as db:
            async with db.execute("SELECT * FROM audit_log") as cursor:
                return [dict(r) for r in await cursor.fetchall()]
    rows = run(all_rows())
    assert rows and not any(SECRET_PHRASE in json.dumps(r, default=str) for r in rows)   # ... never recorded
    draft = run(env.get_latest_draft(mid))
    assert SECRET_PHRASE not in json.dumps(draft.context)


def test_harness_off_pipeline_records_no_context_and_no_extra_audit_keys(env):
    library(env)
    mid = run(_triaged(env))
    brain = Brain(env)
    spy = SpyReviewer(Reviewer(brain))

    run(draft_batch(env, Drafter(brain), spy))

    assert spy.calls == [()]
    assert run(env.get_latest_draft(mid)).context == []
    drafted = [e for e in run(env.list_audit(mid)) if e.event is AuditEventType.DRAFTED]
    assert "context_sections" not in drafted[-1].verdict


def test_the_heartbeat_hands_the_harness_config_to_the_drafter_and_reviewer():
    from pathlib import Path

    from harvey import main

    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "drafter = Drafter(brain, acknowledgements=True)\n    drafter.harness = config.harness" in src
    assert "Reviewer(brain, harness=config.harness)" in src


def test_a_human_edit_keeps_what_the_draft_relied_on(env):
    library(env)
    mid = run(_triaged(env))
    brain = Brain(env)
    run(draft_batch(env, Drafter(brain, harness=ON), Reviewer(brain, harness=ON)))
    before = run(env.get_latest_draft(mid)).context

    run(review.edit(env, mid, CLEAN + " Thanks for the kind words.", CLEAN_IDS, "reviewer@pulse.test",
                    PulseConfig()))

    after = run(env.get_latest_draft(mid))
    assert after.model.startswith("human:") and before and after.context == before
