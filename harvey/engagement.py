"""WellPeps' rules of engagement, applied (docs/RULES-OF-ENGAGEMENT.md).

Everything here is deterministic; no model calls.

- ``situation_for`` / ``situation_of``: which situation of
  config/engagement_guide.yaml a triage result is (first match wins), and so
  which template, approved response, escalation and approval gate apply.
- ``reply_mode`` / ``draftable_where``: whether Pulse drafts at all. The SQL
  that lists draftable mentions is generated from the same situations, so the
  heartbeat, the draft batch and ``route_status`` always agree.
- ``approved_reply``: the verbatim approved response (with the persona's
  disclosure first) for ``boundary_only`` situations.
- ``reply_context`` / ``stop_decision``: community rules
  (config/communities.yaml), repeated links and the stop rules, from the
  reply history in the database.
- ``engagement_info`` / ``mix_report``: what the review desk and the
  Analytics "Education vs promotion" card show. The 80/20 share is a
  planning metric only (Competitor/Switching Protocol §1; Operations Manual
  §2.1): it never gates or flags a single reply.
- The competitor / switching protocol (harvey/protocol.py) decides first for
  mentions in its scope: its classification and route are part of the
  situation match (``decisions`` / ``routes`` in engagement_guide.yaml).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from harvey import communities, knowledge, promotion, protocol
from harvey.compliance import ReplyContext, has_disclosure
from harvey.models import Category, Mention, Triage
from harvey.models.knowledge import Situation

# Safety-screen / triage markers that block any reply, boundary replies included.
SAFETY_BLOCK_MARKERS = ("safety_screen:minor", "safety_screen:self_harm", "safety_screen:failed",
                        "triage_failed")
# Review gates (first live pull, user decision 2026-10-09; docs/REVISIONS-LOG.md
# R-13, R-14). Appended to the triage's urgency_reason at triage time so the
# Python and SQL views of "what gets drafted" stay identical.
GATE_NOT_OURS = "gate:not_ours"            # a boundary / clinical reply to a post not about WellPeps
GATE_NOT_WORTH_IT = "gate:not_worth_it"    # a reply could not plausibly benefit WellPeps
NO_REPLY_MARKERS = (*SAFETY_BLOCK_MARKERS, GATE_NOT_OURS, GATE_NOT_WORTH_IT)
# Questions where a WellPeps answer can lead somewhere (choosing, comparing,
# how it works, asking about WellPeps).
BENEFIT_SUBTYPES = frozenset({
    "process_question", "pricing_question", "has_anyone_used_wellpeps", "clinic_recommendation",
    "competitor_comparison", "competitor_comparison_wellpeps", "misinformation_about_wellpeps",
})
BENEFIT_DECISIONS = frozenset({"appropriate_alternative", "educational_only"})
DRAFTING_MODES = frozenset({"draft", "boundary_only", "stop"})
APPROVED_RESPONSE_MODEL = "approved-response"
RECENT_REPLY_LIMIT = 2000
SKIPPED_EVENT = "skipped"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _value(value) -> str:
    return getattr(value, "value", value) or ""


def subject_of(subject_type: str) -> str:
    return "wellpeps" if (subject_type or "") == "wellpeps" else "other"


def situation_for(category, subtype: str = "", subject_type: str = "", decision: str = "",
                  route: str = "") -> Situation:
    """The first situation in config/engagement_guide.yaml that matches.
    ``decision`` / ``route``: the competitor / switching protocol's
    classification and escalation route ("" outside its scope)."""
    cat, sub, subj = _value(category), (subtype or ""), subject_of(subject_type)
    dec, rte = (decision or ""), (route or "")
    for sit in knowledge.engagement_guide().situations:
        m = sit.match
        if "*" not in m.categories and cat not in m.categories:
            continue
        if "*" not in m.subtypes and sub not in m.subtypes:
            continue
        if m.subject != "any" and m.subject != subj:
            continue
        if "*" not in m.decisions and dec not in m.decisions:
            continue
        if "*" not in m.routes and rte not in m.routes:
            continue
        return sit
    raise RuntimeError("engagement_guide.yaml has no catch-all situation")  # the loader prevents this


def situation_of(triage: Triage | None) -> Situation:
    if triage is None:
        return situation_for("other")
    return situation_for(triage.category, triage.subtype, triage.subject_type,
                         triage.protocol_decision, triage.protocol_route)


def safety_blocked(triage: Triage | None) -> bool:
    reason = (triage.urgency_reason if triage else "") or ""
    return any(marker in reason for marker in SAFETY_BLOCK_MARKERS)


def reply_mode(triage: Triage) -> str:
    """draft | boundary_only | stop | no_reply for this triage (safety screen
    and review gates included)."""
    if safety_blocked(triage) or gated(triage):
        return "no_reply"
    return situation_of(triage).reply


def gated(triage: Triage | None) -> str:
    """The review gate that stopped this triage ("" = none)."""
    reason = (triage.urgency_reason if triage else "") or ""
    return next((g for g in (GATE_NOT_OURS, GATE_NOT_WORTH_IT) if g in reason), "")


def worth_it(triage: Triage) -> bool:
    """Gate 3: a reply could plausibly benefit WellPeps."""
    category = _value(triage.category)
    return (triage.subject_type in ("wellpeps", "competitor")
            or (triage.protocol_decision or "") in BENEFIT_DECISIONS
            or category == "purchase_intent"
            or (triage.subtype or "") in BENEFIT_SUBTYPES)


def review_gate(triage: Triage) -> str:
    """Which review gate applies to a fresh triage ("" = it may be drafted).
    Order: safety and permission (situations, protocol) first, then gate 2b
    (clinical / boundary replies only for posts about WellPeps), then gate 3
    (benefit). Benefit is never a reason to engage; it only filters."""
    if not triage.relevant or safety_blocked(triage):
        return ""
    mode = situation_of(triage).reply
    if mode in ("boundary_only", "stop") and triage.subject_type != "wellpeps":
        return GATE_NOT_OURS
    if mode == "draft" and not worth_it(triage):
        return GATE_NOT_WORTH_IT
    return ""


def with_review_gate(triage: Triage) -> Triage:
    """The triage with its review gate recorded (a new copy; unchanged when none).

    The gate goes after the existing reason: its leading marker (override:,
    safety_screen:, severe_category...) is what ``escalation.reason_code`` and
    ``explain.urgency_explanation`` read. The reason is trimmed so the gate
    always fits in the 300-character column."""
    gate = review_gate(triage)
    if not gate:
        return triage
    suffix = f"; {gate}"
    reason = (triage.urgency_reason or "").strip()
    reason = f"{reason[:300 - len(suffix)]}{suffix}" if reason else gate
    return triage.model_copy(update={"urgency_reason": reason})


def drafts_reply(triage: Triage) -> bool:
    """Pulse will draft for this mention (given a triaged status)."""
    if not triage.relevant:
        return False
    mode = reply_mode(triage)
    if mode == "draft":
        return bool(triage.reply_appropriate)
    return mode in ("boundary_only", "stop")


def _in_sql(column: str, values: list[str]) -> tuple[str, list]:
    """``column IN (...)`` for a match list; "" when the list is a wildcard."""
    if "*" in values:
        return "", []
    return f"COALESCE({column}, '') IN ({', '.join('?' for _ in values)})", list(values)


def _match_sql(sit: Situation) -> tuple[str, list]:
    """A SQL condition (over ``triage t``) equal to ``situation_for`` matching."""
    m = sit.match
    parts, params = [], []
    for column, values in (("t.category", m.categories), ("t.subtype", m.subtypes),
                           ("t.protocol_decision", m.decisions), ("t.protocol_route", m.routes)):
        cond, args = _in_sql(column, values)
        if cond:
            parts.append(cond)
            params += args
    if m.subject == "wellpeps":
        parts.append("COALESCE(t.subject_type, '') = 'wellpeps'")
    elif m.subject == "other":
        parts.append("COALESCE(t.subject_type, '') <> 'wellpeps'")
    return (" AND ".join(parts) or "1 = 1"), params


def _mode_case_sql() -> tuple[str, list]:
    """``CASE WHEN <situation 1> THEN '<reply>' ... END``: the situation list,
    first match wins, in SQL. Generated from config, so the heartbeat, the
    draft batch and ``reply_mode`` always agree."""
    whens, params = [], []
    for sit in knowledge.engagement_guide().situations:
        cond, args = _match_sql(sit)
        whens.append(f"WHEN {cond} THEN '{sit.reply}'")
        params += args
    return f"(CASE {' '.join(whens)} ELSE 'no_reply' END)", params


def draftable_where() -> tuple[str, tuple]:
    """(WHERE clause over ``mentions m JOIN triage t``, params) for mentions
    the draft batch should take: triaged, relevant, not safety-blocked, not
    skipped before, and a situation that drafts (model drafts also need
    triage's reply_appropriate)."""
    case, case_params = _mode_case_sql()
    blocks = " AND ".join("instr(COALESCE(t.urgency_reason, ''), ?) = 0" for _ in NO_REPLY_MARKERS)
    sql = (f"m.status = 'triaged' AND t.relevant = TRUE AND {blocks} "
           f"AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.mention_id = m.id AND a.event = '{SKIPPED_EVENT}') "
           f"AND (({case} = 'draft' AND t.reply_appropriate = TRUE) "
           f"OR {case} IN ('boundary_only', 'stop'))")
    return sql, (*NO_REPLY_MARKERS, *case_params, *case_params)


# --- Persona and approved replies ------------------------------------------------------------


def disclosure_sentence(claim_id: str | None = None) -> str:
    """The opening sentence for the configured persona ("I work with WellPeps."
    or "Hi, I'm <name> - I work with WellPeps.")."""
    by_id = knowledge.claims_by_id()
    default = knowledge.compliance_rules().disclosure.default_form or "I work with WellPeps."
    text = by_id[claim_id].text if claim_id and claim_id in by_id else default
    persona = knowledge.engagement_guide().engagement
    if persona.persona == "identified_employee":
        return f"Hi, I'm {persona.display_name.strip()} — {text}"
    return text


def approved_response_id(situation: Situation, triage: Triage | None) -> str:
    program = knowledge.program_for(triage.product, triage.drug) if triage else ""
    return situation.approved_response_by_program.get(program) or situation.approved_response or ""


def approved_reply(situation: Situation, triage: Triage | None) -> tuple[str, list[str]]:
    """(text, claim ids) of the verbatim approved response, disclosure first."""
    response = knowledge.claims_by_id()[approved_response_id(situation, triage)]
    persona = knowledge.engagement_guide().engagement
    if has_disclosure(response.text):
        prefix = f"Hi, I'm {persona.display_name.strip()} — " if persona.persona == "identified_employee" else ""
        return prefix + response.text, [response.id]
    opening = disclosure_sentence(situation.disclosure_claim)
    return f"{opening} {response.text}", [situation.disclosure_claim, response.id]


def acknowledged_reply(situation: Situation, triage: Triage | None,
                       acknowledgement: str) -> tuple[str, list[str]] | None:
    """The approved reply with ONE acknowledgement sentence between the
    disclosure and the verbatim approved response ([AMG] §1: adapt ordinary
    conversational wording, preserve meaning). None when the approved response
    carries its own disclosure (nothing may be inserted into it)."""
    text, ids = approved_reply(situation, triage)
    clause = (acknowledgement or "").strip()
    opening = disclosure_sentence(situation.disclosure_claim)
    if not clause or len(ids) != 2 or not text.startswith(f"{opening} "):
        return None
    return f"{opening} {clause} {text[len(opening) + 1:]}", ids


def close_reply() -> tuple[str, list[str]]:
    """The guide's graceful close (§22), disclosure first."""
    claim = knowledge.claims_by_id()["CLM-AMG-22-CLOSE"]
    return f"{disclosure_sentence()} {claim.text}", ["CLM-AMG-04-WORK-WITH", claim.id]


# --- Reply history -------------------------------------------------------------------------------


APPROVED_STATUSES = frozenset({"approved", "posted"})
PENDING_STATUSES = frozenset({"drafted", "in_review"})


@dataclass(frozen=True)
class ReplyRecord:
    mention_id: int
    platform: str
    status: str
    author: str
    text: str
    claim_ids: tuple[str, ...]
    link_id: str
    approved_at: datetime | None
    community: str
    thread: str

    @property
    def promotional(self) -> bool:
        return promotion.is_promotional(self.text, self.claim_ids)

    @property
    def approved(self) -> bool:
        return self.status in APPROVED_STATUSES


_RECENT_SQL = (
    "SELECT m.id AS mention_id, m.platform, m.url, m.status, m.author_handle, d.text, d.claim_ids_json, "
    "d.link_json, (SELECT MAX(a.at) FROM audit_log a WHERE a.mention_id = m.id AND a.event = 'approved') "
    "AS approved_at FROM mentions m JOIN drafts d ON d.mention_id = m.id "
    "AND d.version = (SELECT MAX(d2.version) FROM drafts d2 WHERE d2.mention_id = m.id) "
    "WHERE m.status IN ('approved', 'posted', 'drafted', 'in_review') ORDER BY m.id DESC LIMIT ?"
)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T")).replace(tzinfo=None)
    except ValueError:
        return None


def _json(raw, default):
    try:
        data = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


async def recent_replies(state, limit: int = RECENT_REPLY_LIMIT) -> list[ReplyRecord]:
    """WellPeps' replies (latest draft each), newest mention first: approved /
    posted ones (the history) and ones still waiting for review (``approved``
    is False; used only to avoid a second reply in the same thread)."""
    async with state.connect() as db:
        async with db.execute(_RECENT_SQL, (int(limit),)) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
    records = []
    for row in rows:
        link = _json(row["link_json"], {})
        records.append(ReplyRecord(
            mention_id=int(row["mention_id"]), platform=row["platform"] or "", status=row["status"] or "",
            author=(row["author_handle"] or "").strip().lower(), text=row["text"] or "",
            claim_ids=tuple(str(c) for c in _json(row["claim_ids_json"], [])),
            link_id=str(link.get("id") or ""), approved_at=_parse_ts(row["approved_at"]),
            community=communities.community_id(row["url"]), thread=communities.thread_key(row["url"]),
        ))
    return records


def _newest_first(records: list[ReplyRecord]) -> list[ReplyRecord]:
    return sorted(records, key=lambda r: (r.approved_at or datetime.min, r.mention_id), reverse=True)


@dataclass(frozen=True)
class Mix:
    community: str
    replies: int
    promotional: int
    window: int
    limit: float

    @property
    def share(self) -> float:
        return round(self.promotional / self.replies, 3) if self.replies else 0.0

    @property
    def over(self) -> bool:
        return self.replies > 0 and self.share > self.limit

    def as_dict(self) -> dict:
        return {"community": self.community, "replies": self.replies, "promotional": self.promotional,
                "education": self.replies - self.promotional, "share": self.share, "limit": self.limit,
                "window": self.window, "over": self.over}


def community_mix(records: list[ReplyRecord], community: str, exclude: int | None = None) -> Mix:
    """Promotional share of the last ``window_replies`` WellPeps replies in a community."""
    rules = knowledge.engagement_guide().eighty_twenty
    same = [r for r in records if community and r.community == community and r.mention_id != exclude
            and r.approved]
    last = _newest_first(same)[:rules.window_replies]
    return Mix(community=community, replies=len(last), promotional=sum(r.promotional for r in last),
               window=rules.window_replies, limit=rules.max_promotional_share)


def repeated_links(records: list[ReplyRecord], community: str, exclude: int | None = None,
                   now: datetime | None = None) -> frozenset[str]:
    """Registry links already used ``max_repeats`` times in the community within the window."""
    if not community:
        return frozenset()
    rules = knowledge.engagement_guide().link_rules
    cutoff = (now or _utcnow()) - timedelta(days=rules.repeat_window_days)
    counts: dict[str, int] = {}
    for r in records:
        if r.community == community and r.mention_id != exclude and r.link_id and r.approved \
                and r.approved_at is not None and r.approved_at >= cutoff:
            counts[r.link_id] = counts.get(r.link_id, 0) + 1
    return frozenset(lid for lid, n in counts.items() if n >= rules.max_repeats)


def context_from(mention: Mention, records: list[ReplyRecord], now: datetime | None = None) -> ReplyContext:
    status = communities.status_for_url(mention.url)
    return ReplyContext(
        community_id=status.id, participation=status.participation,
        permission_obtained=status.permission_obtained, links_allowed=status.links_allowed,
        repeated_link_ids=repeated_links(records, status.id, exclude=mention.id, now=now),
    )


async def reply_context(state, mention: Mention, now: datetime | None = None) -> ReplyContext:
    """Community rules + history for a reply on ``mention``."""
    return context_from(mention, await recent_replies(state), now=now)


# --- Stop rules (Guide §22) --------------------------------------------------------------------------


@dataclass(frozen=True)
class StopDecision:
    # "close" (draft the graceful close), "no_reply" (empty draft, needs a
    # human) or "skip" (nothing at all: another reply is already pending here)
    action: str
    reason: str


def _boundary_claims() -> set[str]:
    """Approved responses of the individual-advice situations (the boundary replies)."""
    guide = knowledge.engagement_guide()
    advice = set(guide.stop_rules.individual_advice_subtypes) | {"personal_medical_info", "records_dm_request"}
    ids: set[str] = set()
    for sit in guide.situations:
        if sit.reply == "boundary_only" and advice & set(sit.match.subtypes):
            ids.update(c for c in (sit.approved_response, *sit.approved_response_by_program.values()) if c)
    return ids


def stop_decision_from(mention: Mention, triage: Triage | None, records: list[ReplyRecord]) -> StopDecision | None:
    situation = situation_of(triage)
    if situation.reply == "stop":
        return StopDecision("no_reply", f"stop responding: {situation.label.lower()} (Guide §22)")
    thread = communities.thread_key(mention.url)
    if not thread:
        return None
    rules = knowledge.engagement_guide().stop_rules
    in_thread = [r for r in records if r.thread == thread and r.mention_id != mention.id]
    if any(r.status in PENDING_STATUSES for r in in_thread):
        return StopDecision("skip", "another WellPeps reply in this thread is already waiting for review: "
                                    "one representative per thread, no duplicate replies "
                                    "(Competitor/Switching Protocol §9; Pre-LegitScript strategy Channel 3)")
    earlier = [r for r in in_thread if r.approved]
    if len(earlier) >= rules.max_wellpeps_replies_per_thread:
        return StopDecision("no_reply", f"stop responding: WellPeps already replied {len(earlier)} times in this "
                                        "thread (Guide §22; no pile-ons)")
    author = (mention.author_handle or "").strip().lower()
    subtype = triage.subtype if triage else ""
    boundary = _boundary_claims()
    repeated = author and any(r.author == author and boundary & set(r.claim_ids) for r in earlier)
    if repeated and subtype == "personal_medical_info":
        return StopDecision("no_reply", "stop responding: the person keeps posting private medical information "
                                        "after a privacy reply (Guide §22)")
    if repeated and subtype in rules.individual_advice_subtypes:
        return StopDecision("close", "the person repeats an individual medical request after a boundary reply: "
                                     "graceful close only (Guide §22)")
    return None


async def stop_decision(state, mention: Mention, triage: Triage | None) -> StopDecision | None:
    return stop_decision_from(mention, triage, await recent_replies(state))


# --- Drafter guidance ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftGuidance:
    situation_id: str
    label: str
    template: str = ""
    template_structure: str = ""
    notes: str = ""
    disclosure: str = "I work with WellPeps."
    disclosure_claim: str = "CLM-AMG-04-WORK-WITH"
    preferred_claims: tuple[str, ...] = ()
    allow_link: bool = True
    link_note: str = ""
    education_only: bool = False
    promotion_note: str = ""
    repeated_link_ids: tuple[str, ...] = field(default_factory=tuple)
    # Competitor / switching protocol (harvey/protocol.py), "" outside its scope.
    protocol_label: str = ""
    brand_mode: str = ""
    brand_limits: str = ""
    need_focus: str = ""
    # Smart Patient's Guide (harvey/guides.py; binding user instruction 2026-10-07):
    # link | name | forbidden | none, and the picked guide + chapter(s).
    guide_mode: str = "none"
    guide_claim_id: str = ""
    guide_title: str = ""
    guide_short: str = ""
    guide_chapters: tuple[str, ...] = ()
    guide_all_chapters: str = ""
    guide_specific: bool = True
    guide_why: str = ""

    @property
    def guide_required(self) -> bool:
        return self.guide_mode in ("link", "name") and bool(self.guide_claim_id)

    def guide_requirement(self):
        """The harvey.guides.GuideRequirement this guidance was built from."""
        from harvey import guides

        if not self.guide_required:
            mode = self.guide_mode if self.guide_mode in ("forbidden", "omit") else "none"
            return guides.GuideRequirement(mode, None, self.guide_why)
        guide = guides.by_claim_id(self.guide_claim_id)
        return guides.GuideRequirement(self.guide_mode, guides.GuidePick(guide, self.guide_chapters,
                                                                         self.guide_specific), self.guide_why)


def _guide_fields(requirement) -> dict:
    from harvey import guides

    pick = requirement.pick
    if pick is None:
        return {"guide_mode": requirement.mode, "guide_why": requirement.why}
    return {"guide_mode": requirement.mode, "guide_claim_id": pick.guide.claim_id, "guide_title": pick.guide.title,
            "guide_short": pick.guide.short, "guide_chapters": tuple(pick.chapters),
            "guide_all_chapters": guides.chapter_line(pick.guide), "guide_specific": pick.specific,
            "guide_why": requirement.why}


def guidance_for(situation: Situation, context: ReplyContext | None = None, triage: Triage | None = None,
                 mention: Mention | None = None) -> DraftGuidance:
    """``mention``: the post (its words pick the guide chapter); None = no text."""
    from harvey import guides

    context = context or ReplyContext()
    template = next((t for t in knowledge.engagement_guide().templates if t.id == situation.template), None)
    record = (triage.protocol if triage is not None else None) or {}
    link_reason = ("this situation never carries a link" if situation.link_policy == "none"
                   else context.link_forbidden)
    promo_reason = context.promotion_forbidden or (
        "Competitor/Switching Protocol EDUCATIONAL ONLY: general useful answer, no promotion"
        if situation.education_only else "")
    return DraftGuidance(
        situation_id=situation.id, label=situation.label,
        template=situation.template or "", template_structure=template.structure if template else "",
        notes=situation.notes, disclosure=disclosure_sentence(situation.disclosure_claim),
        disclosure_claim=situation.disclosure_claim, preferred_claims=tuple(situation.preferred_claims),
        allow_link=not link_reason, link_note=link_reason,
        education_only=bool(promo_reason), promotion_note=promo_reason,
        repeated_link_ids=tuple(sorted(context.repeated_link_ids)),
        protocol_label=str(record.get("label") or ""), brand_mode=str(record.get("brand_mode") or ""),
        brand_limits=str(record.get("brand_limits") or ""), need_focus=str(record.get("need_focus") or ""),
        **_guide_fields(guides.requirement_for(situation, context, triage, mention)),
    )


# --- Review desk + analytics ---------------------------------------------------------------------------


def situation_summary(situation: Situation) -> dict:
    template = next((t for t in knowledge.engagement_guide().templates if t.id == situation.template), None)
    return {"id": situation.id, "label": situation.label, "template": situation.template or "",
            "template_name": template.name if template else "", "reply": situation.reply,
            "escalate": situation.escalate or "", "clinical_approval": situation.clinical_approval}


def requires_clinical_approval(triage: Triage | None) -> bool:
    """Adverse-event (and emergency) replies: only role clinical or admin may approve."""
    if triage is None:
        return False
    return situation_of(triage).clinical_approval or _value(triage.category) == Category.ADVERSE_EVENT.value


async def engagement_info(state, mention: Mention, triage: Triage | None, draft=None) -> dict:
    """What the review desk shows about how the rules of engagement apply."""
    records = await recent_replies(state)
    status = communities.status_for_url(mention.url)
    situation = situation_of(triage)
    mix = community_mix(records, status.id, exclude=mention.id)
    text = getattr(draft, "text", "") or ""
    claim_ids = list(getattr(draft, "claim_ids", []) or [])
    elements = promotion.promotional_elements(text, claim_ids) if text else []
    persona = knowledge.engagement_guide().engagement
    return {
        "situation": situation_summary(situation),
        "community": status.as_dict(),
        "mix": mix.as_dict() if status.id else None,     # 80/20: a planning metric, never a gate
        "protocol": protocol_view(triage),
        "draft_kind": ("promotion" if elements else "education") if text else None,
        "promotional_elements": elements,
        "clinical_approval_required": requires_clinical_approval(triage),
        "finalize": [{"claim_id": cid, "missing": missing}
                     for cid, missing in knowledge.unresolved_finalize(claim_ids)],
        "persona": {"persona": persona.persona, "display_name": persona.display_name},
        "repeated_link_ids": sorted(repeated_links(records, status.id, exclude=mention.id)),
        "guide": guide_view(situation, context_from(mention, records), triage, mention, text, claim_ids),
    }


def guide_view(situation: Situation, context: ReplyContext, triage: Triage | None, mention: Mention,
               text: str = "", claim_ids=()) -> dict | None:
    """The review desk's guide chip: which guide / chapter this reply should
    point to, and whether the draft does (None when no guide applies)."""
    from harvey import guides

    if triage is None:
        return None
    requirement = guides.requirement_for(situation, context, triage, mention)
    if requirement.mode == "none":
        return None
    view = requirement.as_dict()
    view["referenced"] = bool(text) and guides.references_guide(text, claim_ids)
    view["satisfied"] = bool(text) and guides.satisfied(text, requirement)
    view["drafted"] = bool(text)
    return view


def protocol_view(triage: Triage | None) -> dict | None:
    """The competitor / switching protocol record for the review desk (None
    outside its scope). Rule-based text only; never post text."""
    if triage is None or not triage.protocol_decision:
        return None
    record = dict(triage.protocol or {})
    record.update({"decision": triage.protocol_decision, "route": triage.protocol_route,
                   "score": triage.opportunity_score,
                   "label": protocol.LABELS.get(triage.protocol_decision, triage.protocol_decision)})
    return record


def _bump(table: dict, key: str, kind: str) -> None:
    entry = table.setdefault(key, {"education": 0, "promotion": 0, "total": 0})
    entry[kind] += 1
    entry["total"] += 1


def _rows(table: dict, label: str) -> list[dict]:
    limit = knowledge.engagement_guide().eighty_twenty.max_promotional_share
    out = []
    for key, v in sorted(table.items(), key=lambda kv: -kv[1]["total"]):
        share = round(v["promotion"] / v["total"], 3) if v["total"] else 0.0
        out.append({label: key, **v, "promotional_share": share, "over": share > limit})
    return out


async def mix_report(state, days: int | None = None, now: datetime | None = None) -> dict:
    """Education vs promotion of approved + posted replies whose latest
    approval falls in the last ``days`` days: overall, per platform, per
    community. Our own replies only (no mention text), so no privacy floor."""
    rules = knowledge.engagement_guide().eighty_twenty
    days = days or rules.analytics_days
    cutoff = (now or _utcnow()) - timedelta(days=days)
    overall = {"education": 0, "promotion": 0, "total": 0}
    by_platform: dict[str, dict] = {}
    by_community: dict[str, dict] = {}
    for r in await recent_replies(state):
        if not r.approved or r.approved_at is None or r.approved_at < cutoff:
            continue
        kind = "promotion" if r.promotional else "education"
        overall[kind] += 1
        overall["total"] += 1
        _bump(by_platform, r.platform, kind)
        _bump(by_community, r.community or communities.NONE, kind)
    share = round(overall["promotion"] / overall["total"], 3) if overall["total"] else 0.0
    return {
        "days": days, "limit": rules.max_promotional_share, "window_replies": rules.window_replies,
        **overall, "promotional_share": share, "over": share > rules.max_promotional_share,
        "by_platform": _rows(by_platform, "platform"),
        "by_community": _rows(by_community, "community"),
    }
