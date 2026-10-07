"""Escalation: SLA rows, Slack paging (link + category only), sweeps,
acknowledgement, and the triage hook. Frozen clock via ``now=``; Slack goes
through httpx.MockTransport."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from harvey.agents.triager import FALLBACK_REASON, Triager, triage_batch
from harvey.config import PulseConfig
from harvey.escalation import (
    UNASSIGNED,
    ack,
    build_page,
    escalate,
    escalation_kind,
    owner_for,
    reason_code,
    sweep,
)
from harvey.models import (
    AuditEventType,
    Category,
    Mention,
    MentionStatus,
    Platform,
    Triage,
    Urgency,
)
from harvey.notify import SlackNotifier
from harvey.state import StateManager
from tests.test_triager import FakeBrain, _answer

NOW = datetime(2026, 9, 27, 12, 0)
WEBHOOK = "https://hooks.slack.example.invalid/services/T1/B1/TOKEN"


class SlackSpy:
    """A real SlackNotifier over a MockTransport; records every payload."""

    def __init__(self, status: int = 200):
        self.status = status
        self.payloads: list[dict] = []
        self.notifier = SlackNotifier(WEBHOOK, transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        return httpx.Response(self.status)

    @property
    def texts(self) -> list[str]:
        return [p["text"] for p in self.payloads]


def _config(**escalation) -> PulseConfig:
    base = {
        "clinical_owner": "Dr. Clinical Owner",
        "backup_owner": "Backup Person",
        "sla_minutes": 15,
        "owners": {"legal": "Legal Lead", "privacy": "", "billing_fraud": "", "viral_negative": ""},
    }
    base.update(escalation)
    return PulseConfig(escalation=base, notify={"dashboard_url": "http://127.0.0.1:5555"})


def _triage(mention_id: int, category=Category.ADVERSE_EVENT, urgency=Urgency.URGENT, **kw) -> Triage:
    fields = dict(
        mention_id=mention_id, category=category, urgency=urgency,
        urgency_reason="override:emergency room", subject_type="wellpeps",
    )
    fields.update(kw)
    return Triage(**fields)


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


async def _mention(state, n=1, text="ER visit after my dose", handle="u/someone", **kw) -> Mention:
    mention = Mention(
        platform=kw.pop("platform", Platform.REDDIT),
        external_id=f"t3_esc{n}",
        url=f"https://www.reddit.com/r/test/comments/esc{n}/",
        author_handle=handle,
        text=text,
        **kw,
    )
    mid, _ = await state.upsert_mention(mention)
    return await state.get_mention(mid)


async def _escalated(state, spy, config=None, **triage_kw):
    mention = await _mention(state)
    triage = _triage(mention.id, **triage_kw)
    await state.save_triage(triage)
    esc = await escalate(state, spy.notifier, mention, triage, config or _config(), now=NOW)
    return mention, triage, esc


# --- Kind / owner / reason code ------------------------------------------------


@pytest.mark.parametrize("category,urgency,subject_type,expected", [
    (Category.ADVERSE_EVENT, Urgency.URGENT, "product", "adverse_event"),
    (Category.LEGAL_REGULATORY, Urgency.URGENT, "wellpeps", "legal"),
    (Category.PRIVACY, Urgency.HIGH, "wellpeps", "privacy"),
    (Category.BILLING_FRAUD, Urgency.URGENT, "wellpeps", "billing_fraud"),
    (Category.COMPLAINT, Urgency.URGENT, "wellpeps", "viral_negative"),
    (Category.COMPLAINT, Urgency.HIGH, "wellpeps", None),
    (Category.COMPLAINT, Urgency.URGENT, "competitor", None),
    (Category.PRAISE, Urgency.URGENT, "wellpeps", None),
    (Category.QUESTION, Urgency.NORMAL, "wellpeps", None),
])
def test_escalation_kind(category, urgency, subject_type, expected):
    triage = _triage(1, category=category, urgency=urgency, subject_type=subject_type)

    assert escalation_kind(triage) == expected


def test_irrelevant_mentions_never_escalate():
    assert escalation_kind(_triage(1, relevant=False)) is None


def test_owner_for_uses_clinical_owner_for_adverse_events_and_map_otherwise():
    config = _config()

    assert owner_for("adverse_event", config) == "Dr. Clinical Owner"
    assert owner_for("legal", config) == "Legal Lead"
    assert owner_for("privacy", config) == ""


def test_reason_code_never_passes_model_free_text():
    assert reason_code(_triage(1, urgency_reason="override:\\bER\\b")) == "keyword_override"
    assert reason_code(_triage(1, urgency_reason=FALLBACK_REASON)) == "triage_failed"
    free = reason_code(_triage(1, urgency_reason="the author says she vomited all night"))
    assert free == "model_urgent"


def test_unknown_owner_kind_is_a_config_error():
    with pytest.raises(ValueError):
        PulseConfig(escalation={"owners": {"weather": "x"}})


# --- escalate ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_creates_row_with_sla_and_pages(state):
    spy = SlackSpy()

    mention, _, esc = await _escalated(state, spy)

    assert esc.id is not None
    assert esc.kind == "adverse_event"
    assert esc.owner == "Dr. Clinical Owner"
    assert esc.sla_due_at == NOW + timedelta(minutes=15)
    assert esc.notified_at == NOW
    assert esc.breached is False
    assert len(spy.payloads) == 1
    assert "adverse_event" in spy.texts[0] and mention.url in spy.texts[0]
    events = [e for e in await state.list_audit(mention.id) if e.event is AuditEventType.ESCALATED]
    assert len(events) == 1
    assert events[0].verdict["kind"] == "adverse_event"
    assert events[0].verdict["paged"] is True


@pytest.mark.asyncio
async def test_sla_minutes_come_from_config(state):
    _, _, esc = await _escalated(state, SlackSpy(), config=_config(sla_minutes=30))

    assert esc.sla_due_at == NOW + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_escalate_with_slack_down_still_creates_row_not_paged(state):
    spy = SlackSpy(status=500)

    _, _, esc = await _escalated(state, spy)

    assert esc.id is not None
    assert esc.notified_at is None
    stored = (await state.list_open_escalations())[0]
    assert stored.notified_at is None


@pytest.mark.asyncio
async def test_escalate_without_webhook_creates_row(state):
    mention = await _mention(state)
    triage = _triage(mention.id)

    esc = await escalate(state, SlackNotifier(None), mention, triage, _config(), now=NOW)

    assert esc.id is not None and esc.notified_at is None


@pytest.mark.asyncio
async def test_escalate_is_idempotent_while_open(state):
    spy = SlackSpy()
    mention, triage, first = await _escalated(state, spy)

    again = await escalate(state, spy.notifier, mention, triage, _config(), now=NOW + timedelta(minutes=1))

    assert again.id == first.id
    assert len(await state.list_open_escalations()) == 1
    assert len(spy.payloads) == 1
    escalated = [e for e in await state.list_audit(mention.id) if e.event is AuditEventType.ESCALATED]
    assert len(escalated) == 1


@pytest.mark.asyncio
async def test_escalate_after_ack_opens_a_new_escalation(state):
    spy = SlackSpy()
    mention, triage, first = await _escalated(state, spy)
    await ack(state, first.id, "nurse@wellpeps.example", now=NOW + timedelta(minutes=2))

    second = await escalate(state, spy.notifier, mention, triage, _config(), now=NOW + timedelta(hours=1))

    assert second.id != first.id
    assert len(spy.payloads) == 2


@pytest.mark.asyncio
async def test_escalate_rejects_non_escalation_triage(state):
    mention = await _mention(state)

    with pytest.raises(ValueError):
        await escalate(state, SlackSpy().notifier, mention,
                       _triage(mention.id, category=Category.PRAISE, urgency=Urgency.LOW), _config(), now=NOW)


@pytest.mark.asyncio
async def test_unassigned_owner_is_called_out(state):
    spy = SlackSpy()

    _, _, esc = await _escalated(state, spy, category=Category.PRIVACY)

    assert esc.owner == ""
    assert UNASSIGNED in spy.texts[0]
    assert "set an owner in harvey.yaml" in spy.texts[0]


# --- PHI rule ----------------------------------------------------------------------------


MARKER = "ZQX-PHI-MARKER-4417"
HANDLE = "u/phi_handle_8812"


@pytest.mark.asyncio
async def test_slack_payload_never_carries_text_handle_or_phrases(state):
    spy = SlackSpy()
    mention = await _mention(
        state, text=f"I was hospitalized {MARKER} after my third dose", handle=HANDLE,
        title=f"Title {MARKER}",
    )
    triage = _triage(
        mention.id, urgency_reason=f"author says {MARKER}", phrases=[f"hospitalized {MARKER}"],
        subject=f"subject {MARKER}",
    )
    await state.save_triage(triage)

    esc = await escalate(state, spy.notifier, mention, triage, _config(), now=NOW)
    await sweep(state, spy.notifier, _config(), now=NOW + timedelta(minutes=20))

    assert len(spy.payloads) == 2  # initial page + breach re-page
    raw = json.dumps(spy.payloads)
    assert MARKER not in raw
    assert HANDLE not in raw and "phi_handle" not in raw
    assert mention.url in raw  # the permalink is the one thing that goes out
    text, blocks = build_page(mention, esc, triage, _config())
    assert MARKER not in text + json.dumps(blocks)
    assert "phi_handle" not in text + json.dumps(blocks)


def test_build_page_contains_only_allowed_fields():
    mention = Mention(id=5, platform=Platform.FACEBOOK, url="https://www.facebook.com/x/posts/1",
                      text="secret words", author_handle="someone")
    esc_due = NOW + timedelta(minutes=15)
    from harvey.models import Escalation
    esc = Escalation(id=9, mention_id=5, kind="legal", owner="Legal Lead", sla_due_at=esc_due)

    text, blocks = build_page(mention, esc, _triage(5, category=Category.LEGAL_REGULATORY), _config())

    assert "legal" in text
    assert "facebook" in text
    assert "keyword_override" in text
    assert "https://www.facebook.com/x/posts/1" in text
    assert "http://127.0.0.1:5555" in text
    assert "Legal Lead" in text
    assert "2026-09-27 12:15" in text
    assert "secret" not in text and "someone" not in text
    assert blocks and blocks[0]["type"] == "section"


# --- sweep --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_before_due_does_nothing(state):
    spy = SlackSpy()
    await _escalated(state, spy)

    report = await sweep(state, spy.notifier, _config(), now=NOW + timedelta(minutes=14))

    assert report.breached == 0 and report.paged == 0
    assert len(spy.payloads) == 1
    assert report.open == 1


@pytest.mark.asyncio
async def test_sweep_after_due_marks_breached_and_repages_with_backup(state):
    spy = SlackSpy()
    mention, _, esc = await _escalated(state, spy)

    report = await sweep(state, spy.notifier, _config(), now=NOW + timedelta(minutes=16))

    assert report.breached == 1
    stored = (await state.list_open_escalations())[0]
    assert stored.breached is True
    assert len(spy.payloads) == 2
    assert spy.texts[1].startswith("SLA BREACHED")
    assert "Backup Person" in spy.texts[1]
    assert "Dr. Clinical Owner" in spy.texts[1]
    events = [e for e in await state.list_audit(mention.id) if e.event is AuditEventType.ESCALATED]
    assert [e.verdict.get("action") for e in events] == ["opened", "sla_breached"]


@pytest.mark.asyncio
async def test_breached_escalation_is_not_repaged_every_sweep(state):
    spy = SlackSpy()
    await _escalated(state, spy)

    await sweep(state, spy.notifier, _config(), now=NOW + timedelta(minutes=16))
    report = await sweep(state, spy.notifier, _config(), now=NOW + timedelta(minutes=21))

    assert report.breached == 0 and report.paged == 0
    assert len(spy.payloads) == 2


@pytest.mark.asyncio
async def test_sweep_retries_unpaged_escalations(state):
    down = SlackSpy(status=500)
    mention, _, esc = await _escalated(state, down)
    assert esc.notified_at is None
    up = SlackSpy()

    report = await sweep(state, up.notifier, _config(), now=NOW + timedelta(minutes=5))

    assert report.paged == 1
    assert len(up.payloads) == 1
    assert not up.texts[0].startswith("SLA BREACHED")
    stored = (await state.list_open_escalations())[0]
    assert stored.notified_at == NOW + timedelta(minutes=5)
    again = await sweep(state, up.notifier, _config(), now=NOW + timedelta(minutes=6))
    assert again.paged == 0 and len(up.payloads) == 1


@pytest.mark.asyncio
async def test_sweep_with_slack_still_down_counts_failure(state):
    down = SlackSpy(status=500)
    await _escalated(state, down)

    report = await sweep(state, down.notifier, _config(), now=NOW + timedelta(minutes=5))

    assert report.paged == 0 and report.failed == 1
    assert (await state.list_open_escalations())[0].notified_at is None


@pytest.mark.asyncio
async def test_unpaged_and_breached_pages_only_once_per_sweep(state):
    down = SlackSpy(status=500)
    await _escalated(state, down)
    up = SlackSpy()

    report = await sweep(state, up.notifier, _config(), now=NOW + timedelta(minutes=30))

    assert len(up.payloads) == 1
    assert up.texts[0].startswith("SLA BREACHED")
    assert report.breached == 1 and report.paged == 1
    stored = (await state.list_open_escalations())[0]
    assert stored.breached is True and stored.notified_at is not None


@pytest.mark.asyncio
async def test_sweep_ignores_acked_escalations(state):
    spy = SlackSpy()
    _, _, esc = await _escalated(state, spy)
    await ack(state, esc.id, "nurse@wellpeps.example", now=NOW + timedelta(minutes=3))

    report = await sweep(state, spy.notifier, _config(), now=NOW + timedelta(hours=2))

    assert report.open == 0 and report.breached == 0
    assert len(spy.payloads) == 1


# --- ack ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ack_sets_fields_and_audits_once(state):
    spy = SlackSpy()
    mention, _, esc = await _escalated(state, spy)
    at = NOW + timedelta(minutes=4)

    first = await ack(state, esc.id, "nurse@wellpeps.example", now=at)
    second = await ack(state, esc.id, "someone-else", now=at + timedelta(minutes=1))

    assert (first, second) == (True, False)
    stored = await state.get_escalation(esc.id)
    assert stored.acked_at == at and stored.acked_by == "nurse@wellpeps.example"
    acked = [e for e in await state.list_audit(mention.id) if e.event is AuditEventType.ACKED]
    assert len(acked) == 1 and acked[0].actor == "nurse@wellpeps.example"


@pytest.mark.asyncio
async def test_ack_unknown_id_is_false(state):
    assert await ack(state, 999, "x", now=NOW) is False


@pytest.mark.asyncio
async def test_ack_requires_a_name(state):
    _, _, esc = await _escalated(state, SlackSpy())

    with pytest.raises(ValueError):
        await ack(state, esc.id, "  ", now=NOW)


# --- Triage hook ---------------------------------------------------------------------------


async def _seed(state, text, n):
    mid, _ = await state.upsert_mention(Mention(
        platform=Platform.INSTAGRAM, external_id=f"ig{n}",
        url=f"https://www.instagram.com/p/esc{n}/", text=text,
        collected_at=NOW - timedelta(minutes=60 - n),
    ))
    return mid


@pytest.mark.asyncio
async def test_triage_batch_escalates_through_the_hook(state):
    spy = SlackSpy()
    config = _config()
    adverse = await _seed(state, "emergency room last night after my dose", 1)
    viral = await _seed(state, "WellPeps ghosted me, do not sign up", 2)
    praise = await _seed(state, "My provider checks in often", 3)
    brain = FakeBrain({
        "emergency room": [_answer(category="praise", urgency="low")],
        "ghosted me": [_answer(category="complaint", urgency="urgent", sentiment=-0.9,
                               sentiment_label="negative", reply_appropriate=False)],
        "provider checks in": [_answer()],
    })

    async def hook(mention, triage):
        return await escalate(state, spy.notifier, mention, triage, config, now=NOW)

    report = await triage_batch(state, Triager(brain), escalate=hook)

    assert report.escalated == 1
    assert report.paged == 2
    kinds = sorted(e.kind for e in await state.list_open_escalations())
    assert kinds == ["adverse_event", "viral_negative"]
    # Paged, and kept triaged for the guide's approved boundary reply (rules of engagement).
    assert (await state.get_mention(adverse)).status is MentionStatus.TRIAGED
    assert (await state.get_mention(viral)).status is MentionStatus.TRIAGED
    assert (await state.get_mention(praise)).status is MentionStatus.TRIAGED
    assert len(spy.payloads) == 2
    events = [e.event for e in await state.list_audit(adverse)]
    assert events == [AuditEventType.TRIAGED, AuditEventType.ESCALATED]


@pytest.mark.asyncio
async def test_failed_escalation_leaves_mention_new_for_retry(state):
    mid = await _seed(state, "emergency room last night after my dose", 1)
    brain = FakeBrain({"emergency room": [_answer(category="adverse_event", urgency="urgent")]})

    async def broken(mention, triage):
        raise RuntimeError("db down")

    report = await triage_batch(state, Triager(brain), escalate=broken)

    assert report.errors == 1
    assert (await state.get_mention(mid)).status is MentionStatus.NEW


# --- CLI ------------------------------------------------------------------------------------


def test_cli_has_escalations_and_ack_commands():
    from harvey.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["ack", "7", "--by", "Nurse Jo"])

    assert args.id == 7 and args.by == "Nurse Jo"
    assert parser.parse_args(["escalations"]).command == "escalations"


@pytest.mark.asyncio
async def test_cli_escalation_lines_show_sla_status(state):
    from harvey.cli import escalation_lines

    down = SlackSpy(status=500)
    _, _, esc = await _escalated(state, down)

    due_soon = await escalation_lines(state, now=NOW + timedelta(minutes=5))
    overdue = await escalation_lines(state, now=NOW + timedelta(minutes=20))

    joined = "\n".join(due_soon)
    assert f"#{esc.id}" in joined and "adverse_event" in joined
    assert "due in 10m" in joined
    assert "not paged" in joined
    assert "OVERDUE" in "\n".join(overdue)
    assert "ER visit" not in joined  # no post text in the CLI list either


@pytest.mark.asyncio
async def test_cli_escalation_lines_empty(state):
    from harvey.cli import escalation_lines

    assert escalation_lines and await escalation_lines(state, now=NOW) == ["No open escalations."]


def test_triage_failed_code_matches_triager_fallback():
    from harvey.escalation import TRIAGE_FAILED

    assert TRIAGE_FAILED == FALLBACK_REASON
