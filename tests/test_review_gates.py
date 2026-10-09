"""Topic gate, WellPeps-only paging, the safety watch, and the review gates
(user decisions 2026-10-09, docs/REVISIONS-LOG.md R-13 / R-14; the safety
watch is pending WellPeps clinical confirmation).

- A post outside WellPeps' market is dropped and never trips the keyword override.
- Only WellPeps' own incidents page an owner. A serious health report about
  anyone else goes on the safety watch (escalated, never paged, not counted
  as an open escalation, skipped by the sweep); the rest is trends only.
- gate:not_ours: no boundary / clinical reply to a post not about WellPeps.
- gate:not_worth_it: no draft unless a reply could plausibly benefit WellPeps.

Fake brain, temp DB, Slack over httpx.MockTransport; no network, no Claude.
"""

from datetime import timedelta

import pytest
import pytest_asyncio

from harvey import engagement
from harvey.agents.triager import Triager, apply_safety_net, triage_batch
from harvey.escalation import SAFETY_WATCH, escalate, escalation_kind, sweep
from harvey.models import Category, Mention, MentionStatus, Platform, Triage, Urgency
from harvey.state import StateManager
from tests.test_escalation import NOW, SlackSpy, _config
from tests.test_triager import FakeBrain, _answer

OFF_TOPIC_LAWSUIT = "Our church group is discussing the lawsuit over the parking lot"
WELLPEPS_ER = "I ended up in the ER after my WellPeps semaglutide shot"
# Provider not named (the usual case): outside the competitor protocol's scope.
OTHER_ER = "I ended up in the ER after my compounded semaglutide shot"
OTHER_MILD = "My GLP-1 makes me a bit nauseous some mornings"
OTHER_BILLING = "Hims charged me without asking for my weight loss refill, I am filing a chargeback"
OTHER_LAWSUIT = "Heard there is a lawsuit against Ro over their GLP-1 ads"
GENERAL_EDU = "What do GLP-1 medications actually do in the body?"
SHOPPER = "How much does a telehealth weight loss program usually cost per month?"


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


async def _seed(state, text: str, n: int) -> int:
    mid, created = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id=f"t3_gate{n}",
        url=f"https://www.reddit.com/r/test/comments/gate{n}/", text=text,
        collected_at=NOW - timedelta(minutes=60 - n),
    ))
    assert created
    return mid


def _brain() -> FakeBrain:
    neg = {"sentiment": -0.8, "sentiment_label": "negative", "reply_appropriate": False}
    other = {"subject_type": "category", "subject": "GLP-1"}
    return FakeBrain({
        "church group": [_answer(category="legal_regulatory", urgency="urgent", subject_type="none", **neg)],
        "WellPeps semaglutide": [_answer(category="adverse_event", urgency="urgent", **neg)],
        "compounded semaglutide": [_answer(category="adverse_event", urgency="urgent", **other, **neg)],
        "nauseous": [_answer(category="adverse_event", subtype="symptom_report", urgency="normal",
                             **other, **neg)],
        "Hims charged": [_answer(category="billing_fraud", urgency="urgent", subject_type="competitor",
                                 subject="Hims & Hers", competitor="Hims & Hers", **neg)],
        "lawsuit against Ro": [_answer(category="legal_regulatory", urgency="high", subject_type="competitor",
                                       subject="Ro", competitor="Ro", **neg)],
        "actually do in the body": [_answer(category="question", subtype="general_education",
                                            subject_type="category", subject="GLP-1", urgency="normal",
                                            sentiment=0.0, sentiment_label="neutral")],
        "usually cost": [_answer(category="question", subtype="pricing_question", subject_type="category",
                                 subject="telehealth weight loss", urgency="normal",
                                 sentiment=0.0, sentiment_label="neutral")],
    })


class _Hook:
    """The production escalate hook (harvey.escalation.escalate), recording calls."""

    def __init__(self, state, spy, config=None):
        self.state, self.spy, self.config = state, spy, config or _config()
        self.calls: list[str] = []

    async def __call__(self, mention, triage):
        self.calls.append(escalation_kind(triage))
        return await escalate(self.state, self.spy.notifier, mention, triage, self.config, now=NOW)


async def _run(state, *texts):
    spy = SlackSpy()
    hook = _Hook(state, spy)
    ids = [await _seed(state, text, n) for n, text in enumerate(texts, start=1)]
    report = await triage_batch(state, Triager(_brain()), escalate=hook)
    assert report.errors == 0
    return ids, spy, hook, report


# --- Topic gate ---------------------------------------------------------------------------


def test_off_topic_post_is_dropped_and_the_keyword_override_never_fires():
    triage = Triage(mention_id=1, category=Category.LEGAL_REGULATORY, urgency=Urgency.NORMAL,
                    urgency_reason="model reason", reply_appropriate=True)

    out = apply_safety_net(triage, OFF_TOPIC_LAWSUIT)

    assert out.relevant is False and out.reply_appropriate is False
    assert out.urgency_reason.startswith("off_topic")
    assert "override:" not in out.urgency_reason
    assert out.urgency is Urgency.NORMAL
    assert escalation_kind(out) is None


@pytest.mark.asyncio
async def test_off_topic_lawsuit_post_is_dropped_not_escalated(state):
    (mid,), spy, hook, report = await _run(state, OFF_TOPIC_LAWSUIT)

    assert (await state.get_mention(mid)).status is MentionStatus.DROPPED
    assert report.dropped == 1 and report.escalated == 0
    assert hook.calls == [] and spy.payloads == []
    assert await state.list_open_escalations() == []


def test_naming_wellpeps_makes_wellpeps_the_subject():
    triage = Triage(mention_id=1, category=Category.ADVERSE_EVENT, subject_type="product")

    assert apply_safety_net(triage, WELLPEPS_ER).subject_type == "wellpeps"
    assert apply_safety_net(triage, OTHER_ER).subject_type == "product"


# --- Escalation routing -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wellpeps_adverse_event_pages_the_clinical_owner(state):
    (mid,), spy, hook, report = await _run(state, WELLPEPS_ER)

    assert hook.calls == ["adverse_event"]
    esc = await state.get_open_escalation(mid)
    assert esc.kind == "adverse_event" and esc.owner == "Dr. Clinical Owner"
    assert esc.notified_at == NOW and len(spy.payloads) == 1
    assert report.escalated == 1 and report.paged == 1
    assert (await state.get_state_summary())["open_escalations"] == 1


@pytest.mark.asyncio
async def test_serious_adverse_event_about_another_provider_goes_on_the_safety_watch(state):
    (mid,), spy, hook, report = await _run(state, OTHER_ER)

    triage = await state.get_triage(mid)
    assert triage.subject_type == "category" and triage.urgency_reason.startswith("override:")
    assert hook.calls == [SAFETY_WATCH]
    assert (await state.get_mention(mid)).status is MentionStatus.ESCALATED
    esc = await state.get_open_escalation(mid)
    assert esc.kind == SAFETY_WATCH and esc.notified_at is None
    assert esc.sla_due_at == NOW + timedelta(hours=24)       # a daily review window, not the paging SLA
    assert spy.payloads == []                                # never paged
    assert report.escalated == 1 and report.paged == 0
    assert (await state.get_state_summary())["open_escalations"] == 0
    assert engagement.gated(triage) == engagement.GATE_NOT_OURS
    assert await state.list_draftable_mentions() == []

    # The sweep never pages or re-pages it, even long after the review window.
    swept = await sweep(state, spy.notifier, _config(), now=NOW + timedelta(days=3))
    assert (swept.paged, swept.breached, swept.failed, swept.errors) == (0, 0, 0, 0)
    assert spy.payloads == []
    assert (await state.get_open_escalation(mid)).breached is False


@pytest.mark.asyncio
async def test_mild_adverse_event_about_another_provider_is_not_escalated_or_drafted(state):
    (mid,), spy, hook, report = await _run(state, OTHER_MILD)

    triage = await state.get_triage(mid)
    assert escalation_kind(triage) is None
    assert hook.calls == [] and spy.payloads == []
    assert await state.get_open_escalation(mid) is None
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED     # kept for trends
    assert engagement.gated(triage) == engagement.GATE_NOT_OURS
    assert engagement.reply_mode(triage) == "no_reply"
    assert engagement.drafts_reply(triage) is False
    assert await state.list_draftable_mentions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [OTHER_BILLING, OTHER_LAWSUIT], ids=["billing_keyword", "legal_keyword"])
async def test_legal_or_billing_post_about_another_company_is_not_escalated(state, text):
    (mid,), spy, hook, _ = await _run(state, text)

    triage = await state.get_triage(mid)
    assert triage.relevant and triage.category in (Category.BILLING_FRAUD, Category.LEGAL_REGULATORY)
    assert triage.subject_type != "wellpeps"
    assert escalation_kind(triage) is None
    assert hook.calls == [] and spy.payloads == []
    assert await state.list_open_escalations() == []
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED
    assert await state.list_draftable_mentions() == []


@pytest.mark.parametrize("category,subtype", [
    (Category.LEGAL_REGULATORY, ""), (Category.PRIVACY, ""), (Category.BILLING_FRAUD, ""),
    (Category.QUESTION, "legal_threat"), (Category.QUESTION, "regulatory_contact"),
])
def test_severe_kinds_page_only_for_wellpeps(category, subtype):
    for subject in ("competitor", "category", "product", ""):
        triage = Triage(mention_id=1, category=category, subtype=subtype, subject_type=subject,
                        urgency=Urgency.URGENT, urgency_reason="override:x")
        assert escalation_kind(triage) is None, subject
    assert escalation_kind(Triage(mention_id=1, category=category, subtype=subtype,
                                  subject_type="wellpeps")) is not None


@pytest.mark.parametrize("update", [
    {"subtype": "emergency"}, {"subtype": "self_harm"}, {"urgency_reason": "override:\\bER\\b"},
    {"urgency_reason": "safety_screen:self_harm: x"}, {"urgency_reason": "safety_screen:minor: 15"},
    {"intents": ["possible_serious_harm"]},
])
def test_every_serious_signal_puts_a_non_wellpeps_report_on_the_watch(update):
    base = Triage(mention_id=1, category=Category.ADVERSE_EVENT, subject_type="competitor")
    assert escalation_kind(base) is None
    assert escalation_kind(base.model_copy(update=update)) == SAFETY_WATCH


# --- Review gates -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_general_education_not_about_wellpeps_or_a_competitor_is_not_worth_it(state):
    (mid,), *_ = await _run(state, GENERAL_EDU)

    triage = await state.get_triage(mid)
    assert triage.reply_appropriate is True
    assert engagement.gated(triage) == engagement.GATE_NOT_WORTH_IT
    assert engagement.reply_mode(triage) == "no_reply"
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED
    assert await state.list_draftable_mentions() == []


@pytest.mark.asyncio
async def test_pricing_question_from_a_shopper_has_no_gate_and_is_drafted(state):
    (mid,), *_ = await _run(state, SHOPPER)

    triage = await state.get_triage(mid)
    assert engagement.gated(triage) == ""
    assert engagement.reply_mode(triage) == "draft"
    assert [m.id for m in await state.list_draftable_mentions()] == [mid]


def test_gates_follow_subject_and_benefit():
    t = lambda **kw: Triage(mention_id=1, relevant=True, reply_appropriate=True, **kw)  # noqa: E731
    # Boundary reply about WellPeps: allowed; about anyone else: not ours.
    assert engagement.review_gate(t(category=Category.QUESTION, subtype="dose_question",
                                    subject_type="wellpeps")) == ""
    assert engagement.review_gate(t(category=Category.ADVERSE_EVENT,
                                    subject_type="competitor")) == engagement.GATE_NOT_OURS
    # A draft needs a plausible benefit.
    assert engagement.review_gate(t(category=Category.PRAISE, subject_type="category")) == \
        engagement.GATE_NOT_WORTH_IT
    assert engagement.review_gate(t(category=Category.PURCHASE_INTENT, subject_type="category")) == ""
    assert engagement.review_gate(t(category=Category.PRAISE, subject_type="competitor")) == ""
    # Irrelevant posts are never gated (they are dropped).
    assert engagement.review_gate(Triage(mention_id=1, relevant=False)) == ""
    # with_review_gate appends the gate, keeping the reason's leading marker.
    gated = engagement.with_review_gate(t(category=Category.PRAISE, subject_type="category",
                                          urgency_reason="model text"))
    assert gated.urgency_reason == "model text; gate:not_worth_it"
    # ... and the gate survives a reason that fills the column.
    long = engagement.with_review_gate(t(category=Category.PRAISE, subject_type="category",
                                         urgency_reason="x" * 300))
    assert len(long.urgency_reason) <= 300 and long.urgency_reason.endswith("gate:not_worth_it")
    assert engagement.gated(long) == engagement.GATE_NOT_WORTH_IT


def test_a_gate_keeps_the_page_reason_code_and_the_why():
    from harvey.escalation import reason_code
    from harvey.explain import urgency_explanation

    triage = Triage(mention_id=1, category=Category.ADVERSE_EVENT, subject_type="category",
                    urgency=Urgency.URGENT, urgency_reason=r"override:(?-i:\bE\.?R\b)")
    gated = engagement.with_review_gate(triage)

    assert engagement.gated(gated) == engagement.GATE_NOT_OURS
    assert reason_code(gated) == "keyword_override"
    assert urgency_explanation(OTHER_ER, gated)["source"] == "keyword"


# --- Manual escalation is a human decision, not gated -------------------------------------


@pytest.fixture
def desk(tmp_path, monkeypatch):
    from tests.dashboard_helpers import setup_app, teardown_app

    state, notifier = setup_app(tmp_path, monkeypatch)
    yield state, notifier
    teardown_app()


@pytest.mark.parametrize("kind,status", [("legal", MentionStatus.ESCALATED),
                                         ("adverse_event", MentionStatus.ESCALATED),
                                         (SAFETY_WATCH, MentionStatus.ESCALATED)])
def test_manual_escalation_works_on_a_post_not_about_wellpeps(desk, kind, status):
    from tests.dashboard_helpers import REVIEWER, add_mention, client_for, post, run

    state, notifier = desk
    mid = run(add_mention(state, f"man-{kind}", text="Is Ro legit for GLP-1s?", status=MentionStatus.TRIAGED,
                          triage={"category": "question", "urgency": "normal", "relevant": True,
                                  "subject_type": "competitor"}))
    client, csrf = client_for(REVIEWER)

    resp = post(client, csrf, f"/api/mentions/{mid}/escalate", {"kind": kind})

    assert resp.status_code == 200, resp.text
    esc = run(state.get_open_escalation(mid))
    assert esc.kind == kind
    assert run(state.get_mention(mid)).status is status
    paged = kind != SAFETY_WATCH
    assert (esc.notified_at is not None) is paged
    assert bool(notifier.sent) is paged
