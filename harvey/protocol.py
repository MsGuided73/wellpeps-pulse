"""The Competitor Mentions and Provider Switching Protocol, applied.

Source: WellPeps_AI_Competitor_Mentions_and_Provider_Switching_Protocol_V1
(Oct 6, 2026; cited [CP] below). It controls competitor mentions,
dissatisfaction, provider switching and opportunity scoring; later approved
legal, clinical, privacy and compliance directives control over it
(docs/RULES-OF-ENGAGEMENT.md).

Everything here is deterministic; no model calls. Triage (a model) supplies
the judgement inputs the protocol asks for (intent tags, the underlying need,
need clarity and useful contribution, [CP] §3, §9); this module applies the
decision sequence exactly as written ([CP] §4):

1. Access and participation: prohibited -> DO NOT ENGAGE; unknown rules or
   permissions -> HOLD (safety may still be routed internally). Untrusted
   instructions / a deceptive request -> DO NOT ENGAGE ([CP] §2, Example 24).
2. Safety and incident overrides -> ESCALATE (emergency, serious or
   unexpected health reports, privacy exposure, threats, legal / regulatory /
   media contact, a complaint about WellPeps).
3. Clinical boundary -> CLINICAL CAUTION (symptoms, testing, eligibility, lab
   interpretation, dose changes, interactions, medication decisions; a
   follow-up does not clear an earlier clinical concern, Example 25).
4. Intent: venting only or nothing useful -> MONITOR ONLY; an explicit
   request for alternatives with approved facts that fit -> APPROPRIATE
   ALTERNATIVE; otherwise EDUCATIONAL ONLY.
5. Facts: a WellPeps-specific question whose central fact is not approved ->
   HOLD; an alternatives request with no approved fact -> EDUCATIONAL ONLY.
6. The review record (``ProtocolRecord.as_dict``, [CP] §10). Pulse never
   publishes, sends messages or enrolls anyone ([CP] §13): the record names
   the human reviewer and the blockers.

Opportunity is scored only after the gates, only for EDUCATIONAL ONLY and
APPROPRIATE ALTERNATIVE ([CP] §9): alternative intent + need clarity +
approved capability fit + useful contribution, each 0-2; 6-8 high, 3-5
moderate, 0-2 low. Desperation, vulnerability or severity is never scored.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from harvey import knowledge

# [CP] §4 classifications (stored lower-case on triage.protocol_decision).
APPROPRIATE_ALTERNATIVE = "appropriate_alternative"
EDUCATIONAL_ONLY = "educational_only"
CLINICAL_CAUTION = "clinical_caution"
ESCALATE = "escalate"
MONITOR_ONLY = "monitor_only"
HOLD = "hold"
DO_NOT_ENGAGE = "do_not_engage"
# Decisions that publish nothing ([CP] §4 table: "Public draft: None").
NO_DRAFT_DECISIONS = frozenset({MONITOR_ONLY, HOLD, DO_NOT_ENGAGE})
SCORED_DECISIONS = frozenset({APPROPRIATE_ALTERNATIVE, EDUCATIONAL_ONLY})
LABELS = {
    APPROPRIATE_ALTERNATIVE: "APPROPRIATE ALTERNATIVE",
    EDUCATIONAL_ONLY: "EDUCATIONAL ONLY",
    CLINICAL_CAUTION: "CLINICAL CAUTION",
    ESCALATE: "ESCALATE",
    MONITOR_ONLY: "MONITOR ONLY",
    HOLD: "HOLD",
    DO_NOT_ENGAGE: "DO NOT ENGAGE",
}
# [CP] §4 table "Required review".
REQUIRED_REVIEW = {
    APPROPRIATE_ALTERNATIVE: "routine authorized review and claim checks",
    EDUCATIONAL_ONLY: "routine authorized review",
    CLINICAL_CAUTION: "trained reviewer; clinical escalation if required",
    ESCALATE: "designated incident owner immediately",
    MONITOR_ONLY: "optional trend review",
    HOLD: "resolve context, permission or evidence",
    DO_NOT_ENGAGE: "record reason; no channel workaround",
}
# [CP] §9 queue order: safety incidents first, unresolved gates second, then
# eligible educational and alternative requests.
QUEUE_RANK = {ESCALATE: 0, CLINICAL_CAUTION: 1, HOLD: 1, APPROPRIATE_ALTERNATIVE: 2, EDUCATIONAL_ONLY: 2,
              MONITOR_ONLY: 3, DO_NOT_ENGAGE: 3}

# [CP] §7 brand modes.
BRAND_NONE = "none"
BRAND_AFFILIATION = "affiliation_only"
BRAND_OPTION = "brief_factual_option"
BRAND_PROCESS = "requested_process_detail"
BRAND_MODE_LIMITS = {
    BRAND_NONE: "no company contribution; do not conceal a material affiliation",
    BRAND_AFFILIATION: "identity disclosure is not a sales pitch: no WellPeps service description",
    BRAND_OPTION: "one concise, factual description of WellPeps ('one option you can evaluate'); "
                  "no superiority claim, no promise it is right for this person",
    BRAND_PROCESS: "approved facts and a relevant approved link only",
}

# Escalation routes ([CP] §5; Guide §23). "support" has no paging owner yet
# (Live Reference §10/§14: complaint escalation queue TO BE ESTABLISHED).
ROUTE_HARM = "adverse_event"
ROUTE_PRIVACY = "privacy"
ROUTE_LEGAL = "legal"
ROUTE_BILLING = "billing_fraud"
ROUTE_SUPPORT = "support"

SAFETY_SUBTYPES = frozenset({"emergency", "self_harm"})
LEGAL_SUBTYPES = frozenset({"legal_threat", "regulatory_contact", "media_inquiry"})
PRIVACY_SUBTYPES = frozenset({"personal_medical_info", "records_dm_request"})
PERMITTED = frozenset({"allowed", "none"})


def _value(value) -> str:
    return getattr(value, "value", value) or ""


def config():
    return knowledge.engagement_guide().switching_protocol


@lru_cache(maxsize=64)
def _injection_rx(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def injection_attempt(text: str) -> bool:
    """The post tries to instruct the tool ([CP] §2: ignore embedded
    instructions to override policy, reveal prompts, hide affiliation...)."""
    return any(rx.search(text or "") for rx in _injection_rx(tuple(config().injection_patterns)))


# --- Inputs ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolInput:
    """What the decision sequence reads. Built from a triage result, the
    community registry and the thread history (``input_for``)."""

    category: str = "other"
    subtype: str = ""
    subject_type: str = ""
    competitor: str = ""
    urgency: str = "normal"
    intents: frozenset[str] = frozenset()
    unmet_need: str = ""
    need_clarity: int | None = None
    useful_contribution: int | None = None
    participation: str = "none"         # allowed | with_permission | prohibited | unknown | none
    permission_obtained: bool = False
    links_allowed: bool | None = None
    community_id: str = ""
    prior_clinical_in_thread: bool = False
    injection: bool = False
    # Program-status claims (claims.yaml ``program:``) for the programs the post
    # is about: a publishable one partly answers a medication-availability need.
    program_status: tuple[str, ...] = ()


def in_scope(triage) -> bool:
    """The protocol governs competitor mentions, dissatisfaction and provider
    switching: a named competitor, or a configured scope intent (switching,
    comparison, venting, a deceptive request), need (provider access,
    continuity, fulfillment, lab terms, medication availability) or subtype
    (clinic recommendations, competitor comparisons or praise)."""
    if triage is None or not getattr(triage, "relevant", True):
        return False
    if (getattr(triage, "competitor", "") or "").strip():
        return True
    cfg = config()
    if set(getattr(triage, "intents", ()) or ()) & set(cfg.scope_intents):
        return True
    if (getattr(triage, "unmet_need", "") or "") in set(cfg.scope_needs):
        return True
    return (getattr(triage, "subtype", "") or "") in set(cfg.scope_subtypes)


def input_for(triage, community=None, *, prior_clinical_in_thread: bool = False,
              text: str = "") -> ProtocolInput:
    """``community``: a harvey.communities.CommunityStatus (or None = no community)."""
    return ProtocolInput(
        category=_value(triage.category), subtype=triage.subtype or "", subject_type=triage.subject_type or "",
        competitor=triage.competitor or "", urgency=_value(triage.urgency) or "normal",
        intents=frozenset(triage.intents or ()), unmet_need=triage.unmet_need or "",
        need_clarity=triage.need_clarity, useful_contribution=triage.useful_contribution,
        participation=getattr(community, "participation", "none") or "none",
        permission_obtained=bool(getattr(community, "permission_obtained", False)),
        links_allowed=getattr(community, "links_allowed", None),
        community_id=getattr(community, "id", "") or "",
        prior_clinical_in_thread=prior_clinical_in_thread, injection=injection_attempt(text),
        program_status=_program_status(triage, text),
    )


def _program_status(triage, text: str) -> tuple[str, ...]:
    programs = [knowledge.program_for(triage.product or "", triage.drug or ""), *knowledge.programs_in_text(text)]
    return tuple(dict.fromkeys(c.id for c in knowledge.status_claims(dict.fromkeys(p for p in programs if p))))


# --- Capability matching ([CP] §6) -----------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    fit: int                   # 0 unavailable, 1 partial, 2 directly supported
    claim_ids: tuple[str, ...]  # the approved, publishable claims that fit (evidence)
    focus: str = ""
    label: str = ""


def capability(need: str, publishable: set[str] | frozenset[str] | None = None) -> Capability:
    """How well current approved WellPeps facts fit the need. Only
    publishable claims count: CONFIRM / TO BE ESTABLISHED / FINALIZE /
    pending items cannot be stated as facts ([CP] §6)."""
    spec = config().needs.get(need or "other") or config().needs.get("other")
    if spec is None:
        return Capability(0, ())
    ok = set(knowledge.publishable_claim_ids() if publishable is None else publishable)
    direct = tuple(c for c in spec.direct if c in ok)
    partial = tuple(c for c in spec.partial if c in ok)
    if direct:
        return Capability(2, direct + partial, spec.focus, spec.label)
    if partial:
        return Capability(1, partial, spec.focus, spec.label)
    return Capability(0, (), spec.focus, spec.label)


# --- The record ([CP] §10) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolRecord:
    decision: str
    rationale: tuple[str, ...]
    route: str = ""                       # escalation route when ESCALATE (or safety routed while held)
    components: dict | None = None        # opportunity components, None when gated
    score: int | None = None
    brand_mode: str = BRAND_NONE
    disclosure: str = ""
    resource: str = "none"
    clinical_flag: bool = False
    privacy_flag: bool = False
    competitor_claim_risk: str = "low"    # low | medium | high
    confidence: str = "high"              # high | low
    intents: tuple[str, ...] = ()
    unmet_need: str = ""
    need_label: str = ""
    need_focus: str = ""
    evidence: tuple[str, ...] = ()        # approved claim ids that support the WellPeps facts
    blockers: tuple[str, ...] = ()
    rules: str = "none"                   # community rule status ([CP] §10 "Rules")
    community_id: str = ""
    safety_routed: bool = False           # a safety / incident route despite a public HOLD / DNE
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LABELS.get(self.decision, self.decision)

    @property
    def band(self) -> str | None:
        if self.score is None:
            return None
        cfg = config()
        return "high" if self.score >= cfg.high_min else ("moderate" if self.score >= cfg.moderate_min else "low")

    @property
    def queue_rank(self) -> int:
        return 0 if self.safety_routed else QUEUE_RANK.get(self.decision, 3)

    def as_dict(self) -> dict:
        """The structured review record; stored on triage.protocol_json.
        Rule-based text only, never post text."""
        return {
            "version": config().version,
            "decision": self.decision, "label": self.label, "rationale": list(self.rationale),
            "route": self.route, "safety_routed": self.safety_routed,
            "rules": self.rules, "community_id": self.community_id,
            "intents": list(self.intents), "unmet_need": self.unmet_need,
            "need_label": self.need_label, "need_focus": self.need_focus,
            "risks": {"clinical_flag": self.clinical_flag, "privacy_flag": self.privacy_flag,
                      "competitor_claim_risk": self.competitor_claim_risk, "confidence": self.confidence},
            "opportunity": ({**self.components, "total": self.score, "band": self.band}
                            if self.components is not None else None),
            "brand_mode": self.brand_mode, "brand_limits": BRAND_MODE_LIMITS.get(self.brand_mode, ""),
            "disclosure": self.disclosure, "resource": self.resource,
            "evidence": list(self.evidence),
            "required_review": REQUIRED_REVIEW.get(self.decision, ""),
            "blockers": list(self.blockers), "queue_rank": self.queue_rank,
            "publishing": "human only: Pulse never publishes, sends messages or enrolls anyone",
        }


# --- The decision sequence ([CP] §4) -------------------------------------------------------------------


def _safety_route(inp: ProtocolInput) -> tuple[str, str] | None:
    """(route, reason) when step 2 applies, else None."""
    intents, sub, cat = inp.intents, inp.subtype, inp.category
    if sub in SAFETY_SUBTYPES or cat == "adverse_event" or "possible_serious_harm" in intents:
        return ROUTE_HARM, ("possible emergency or serious / unexpected treatment-related health report: "
                            "safety review immediately; suppress marketing and switching ([CP] §4 step 2, §5)")
    media_to_us = sub == "media_inquiry" and inp.subject_type == "wellpeps"
    legal_intent = "legal_media_privacy" in intents and sub not in PRIVACY_SUBTYPES and cat != "privacy"
    if sub in (LEGAL_SUBTYPES - {"media_inquiry"}) or media_to_us or cat == "legal_regulatory" or legal_intent:
        return ROUTE_LEGAL, "legal, regulatory or media contact: authorized reviewer only, no substantive reply " \
                            "([CP] §4 step 2, Example 22; Guide §23)"
    if sub in PRIVACY_SUBTYPES or cat == "privacy" or "legal_media_privacy" in intents:
        return ROUTE_PRIVACY, "privacy exposure (records or personal health details posted): privacy review; " \
                              "never copy or interpret the values ([CP] §4 step 2, Example 16)"
    if "wellpeps_complaint" in intents or (cat in ("complaint", "billing_fraud") and inp.subject_type == "wellpeps"):
        route = ROUTE_BILLING if cat == "billing_fraud" else ROUTE_SUPPORT
        return route, "complaint involving WellPeps: support or incident route; never confirm patient status " \
                      "([CP] §3, Examples 17-18)"
    return None


def _clinical(inp: ProtocolInput) -> str:
    """Why step 3 applies ("" when it doesn't)."""
    if inp.prior_clinical_in_thread:
        return ("a follow-up does not resolve the earlier clinical concern in this thread "
                "([CP] §4 step 3, Example 25)")
    if "individual_clinical_concern" in inp.intents:
        return "individual clinical concern: approved boundary and provider direction only ([CP] §4 step 3)"
    if inp.subtype in set(config().clinical_subtypes):
        return (f"individual clinical question ({inp.subtype}): approved boundary and provider direction only "
                "([CP] §4 step 3)")
    spec = config().needs.get(inp.unmet_need)
    if spec is not None and spec.clinical:
        return f"the need ({spec.label}) is clinical: the prescriber decides ([CP] §3-§4)"
    return ""


def _score(inp: ProtocolInput, cap: Capability) -> dict:
    """[CP] §9 components (0-2 each)."""
    if "alternatives_requested" in inp.intents:
        alternative = 2
    elif inp.intents & {"comparison_request", "general_information", "wellpeps_question"}:
        alternative = 1
    else:
        alternative = 0
    return {"alternative_intent": alternative, "need_clarity": inp.need_clarity or 0,
            "capability_fit": cap.fit, "useful_contribution": inp.useful_contribution or 0}


def _claim_risk(inp: ProtocolInput) -> str:
    if not inp.competitor:
        return "low"
    if "comparison_request" in inp.intents:
        return "high"
    return "medium"


def _disclosure(inp: ProtocolInput, decision: str) -> str:
    """[CP] §7: employee replies default to "I work with WellPeps."; the
    WellPeps-complaint examples use "I'm part of the WellPeps team."."""
    if decision in NO_DRAFT_DECISIONS:
        return ""
    if decision == ESCALATE and (inp.subject_type == "wellpeps" or "wellpeps_complaint" in inp.intents):
        return "I'm part of the WellPeps team."
    return "I work with WellPeps."


def decide(inp: ProtocolInput, publishable: set[str] | frozenset[str] | None = None) -> ProtocolRecord:
    """Apply the [CP] §4 decision sequence. Pure; ``publishable`` defaults to
    the claims currently approved for publishing."""
    cfg = config()
    spec = cfg.needs.get(inp.unmet_need or "other") or cfg.needs.get("other")
    common = dict(intents=tuple(i for i in inp.intents), unmet_need=inp.unmet_need or "",
                  need_label=spec.label if spec else "", need_focus=spec.focus if spec else "",
                  rules=inp.participation, community_id=inp.community_id,
                  competitor_claim_risk=_claim_risk(inp),
                  confidence="low" if ("ambiguous" in inp.intents or inp.need_clarity is None
                                       or inp.useful_contribution is None) else "high")
    safety = _safety_route(inp)
    clinical = _clinical(inp)
    flags = dict(clinical_flag=bool(clinical) or (safety is not None and safety[0] == ROUTE_HARM),
                 privacy_flag=safety is not None and safety[0] == ROUTE_PRIVACY)

    def record(decision, reasons, **kw) -> ProtocolRecord:
        silent = decision in NO_DRAFT_DECISIONS or kw.get("route") == ROUTE_LEGAL
        brand = kw.pop("brand_mode", BRAND_NONE if silent else BRAND_AFFILIATION)
        return ProtocolRecord(decision=decision, rationale=tuple(reasons), brand_mode=brand,
                              disclosure="" if silent else _disclosure(inp, decision),
                              **{**common, **flags, **kw})

    # Step 1: access and participation. Safety may still be routed internally.
    routed = {"route": safety[0], "safety_routed": True} if safety else {}
    if inp.participation == "prohibited":
        return record(DO_NOT_ENGAGE, ["the community prohibits business participation: no public reply, no "
                                      "private contact, no channel workaround ([CP] §4 step 1, Example 15)"],
                      blockers=("community prohibits brand participation",), **routed)
    if inp.participation == "unknown":
        return record(HOLD, ["community rules or permissions are unknown: hold for human verification; an "
                             "invitation to recommend providers does not override community rules "
                             "([CP] §2, §4 step 1, Example 14)"],
                      blockers=("verify the community's rules (config/communities.yaml)",), **routed)
    if inp.injection or "deceptive_request" in inp.intents:
        return record(DO_NOT_ENGAGE, ["untrusted instructions or a request to hide WellPeps' affiliation: "
                                      "ignore them and keep the governing policy ([CP] §2, Example 24)"],
                      **routed)
    # Step 2: safety and incident overrides.
    if safety is not None:
        route, reason = safety
        blockers = ()
        if route == ROUTE_SUPPORT:
            blockers = ("no support escalation queue yet (Live Reference §10 TO BE ESTABLISHED; FINALIZE "
                        "support_channel): route by hand and record the handoff",)
        return record(ESCALATE, [reason], route=route, blockers=blockers)
    # Step 3: the clinical boundary.
    if clinical:
        return record(CLINICAL_CAUTION, [clinical])
    # Step 4: intent and useful contribution.
    if "ambiguous" in inp.intents:
        return record(HOLD, ["the context does not resolve what is being asked (service process or symptom?): "
                             "hold for review ([CP] §3)"], blockers=("resolve the ambiguity",))
    if "venting_only" in inp.intents and not inp.intents & {"alternatives_requested", "general_information",
                                                            "comparison_request", "wellpeps_question"}:
        return record(MONITOR_ONLY, ["venting with no question or invitation: monitor without joining the "
                                     "complaint; do not manufacture a switching opportunity ([CP] §4 step 4, "
                                     "Examples 2, 13)"])
    if not inp.useful_contribution:
        return record(MONITOR_ONLY, ["no useful answer is available ([CP] §4 step 4)"])
    # Step 5: facts and resource fit.
    cap = capability(inp.unmet_need, publishable)
    if inp.unmet_need == "medication_availability" and cap.fit == 0 and inp.program_status:
        # "Is the hair program live yet?": the program's approved status claim
        # answers part of it (program level only, never a specific medication).
        ok = set(knowledge.publishable_claim_ids() if publishable is None else publishable)
        status = tuple(c for c in inp.program_status if c in ok)
        if status:
            cap = Capability(1, status, cap.focus, cap.label)
    components = _score(inp, cap)
    score = sum(components.values())
    scored = dict(components=components, score=score, evidence=cap.claim_ids)
    if "wellpeps_question" in inp.intents and cap.fit == 0:
        return record(HOLD, ["the WellPeps fact this question turns on is not approved (CONFIRM, TO BE "
                             "ESTABLISHED or missing): hold the company-specific draft ([CP] §4 step 5, §6, "
                             "Examples 19, 21)"],
                      blockers=("verify the fact against current approved sources",))
    promotion_ok = inp.participation in PERMITTED or (inp.participation == "with_permission"
                                                      and inp.permission_obtained)
    if "alternatives_requested" in inp.intents:
        if cap.fit >= 1 and promotion_ok:
            return record(APPROPRIATE_ALTERNATIVE,
                          ["explicit request for alternatives and approved WellPeps facts fit: education plus "
                           "one brief, disclosed, factual option ([CP] §1, §4 step 4, §7)"],
                          brand_mode=BRAND_OPTION, **scored)
        why = ("no approved WellPeps fact fits the need" if cap.fit == 0
               else "the community allows no promotion until permission is obtained")
        return record(EDUCATIONAL_ONLY, [f"alternatives requested, but {why}: general education only "
                                         "([CP] §4 steps 4-5)"], **scored)
    brand = BRAND_PROCESS if "wellpeps_question" in inp.intents and promotion_ok else BRAND_AFFILIATION
    return record(EDUCATIONAL_ONLY, ["general education helps; no explicit request for alternatives "
                                     "([CP] §4 step 4)"], brand_mode=brand, **scored)


def resource_for(record: ProtocolRecord, links_allowed: bool | None, guide_link: str = "",
                 promotion_ok: bool = True) -> str:
    """[CP] §8: never in safety or clinical handling. Returns a link id or "none".

    User-directed override (2026-10-07, docs/RULES-OF-ENGAGEMENT.md "Superseded
    earlier rules"): for the answering decisions (APPROPRIATE ALTERNATIVE,
    EDUCATIONAL ONLY) the relevant Smart Patient's Guide (``guide_link``, from
    harvey/guides.py) is required, not optional, wherever the community allows
    links and promotion; a not-live link is still drafted (yellow) and blocks
    approval. Without ``guide_link`` the earlier rule applies: optional, and
    only a verified (live) link."""
    if record.decision not in SCORED_DECISIONS or links_allowed is False:
        return "none"
    if guide_link:
        return guide_link if promotion_ok else "none"
    live = {lk.id for lk in knowledge.links() if lk.live}
    spec = config().needs.get(record.unmet_need)
    for cid in (spec.direct + spec.partial) if spec else ():
        claim = knowledge.claims_by_id().get(cid)
        if claim is not None and claim.link_id in live:
            return claim.link_id
    return "none"


# --- Applying it to a triage result ---------------------------------------------------------------

_PRIOR_SQL = (
    "SELECT m.id, m.url, m.author_handle, t.protocol_decision, t.subtype, t.category "
    "FROM mentions m JOIN triage t ON t.mention_id = m.id WHERE m.id <> ? ORDER BY m.id DESC LIMIT ?"
)
PRIOR_SCAN_LIMIT = 2000


async def prior_clinical_in_thread(state, mention) -> bool:
    """An earlier post by the same author in the same thread raised a clinical
    or safety concern ([CP] Example 25: a follow-up does not resolve it)."""
    from harvey import communities

    thread = communities.thread_key(mention.url)
    if not thread:
        return False
    author = (mention.author_handle or "").strip().lower()
    clinical = set(config().clinical_subtypes) | SAFETY_SUBTYPES
    async with state.connect() as db:
        async with db.execute(_PRIOR_SQL, (int(mention.id or 0), PRIOR_SCAN_LIMIT)) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
    for row in rows:
        if communities.thread_key(row["url"]) != thread:
            continue
        if author and (row["author_handle"] or "").strip().lower() != author:
            continue
        if (row["protocol_decision"] in (CLINICAL_CAUTION, ESCALATE) or (row["subtype"] or "") in clinical
                or row["category"] == "adverse_event"):
            return True
    return False


FOLLOW_UP_INTENTS = frozenset({"wellpeps_question", "alternatives_requested", "general_information"})


def applies(triage, mention, prior_clinical: bool = False) -> bool:
    """In scope, or instructions inside the post ([CP] §2), or a follow-up
    question after an earlier clinical concern in the thread (Example 25)."""
    if triage is None or not triage.relevant:
        return False
    if in_scope(triage) or injection_attempt(f"{mention.title} {mention.text}"):
        return True
    return prior_clinical and bool(set(triage.intents or ()) & FOLLOW_UP_INTENTS)


def applied(triage, mention, *, prior_clinical: bool = False, publishable=None):
    """``triage`` with the protocol's decision, route, score and record set
    (cleared when the protocol does not apply to the mention)."""
    from harvey import communities

    if triage is None:
        return None
    if not applies(triage, mention, prior_clinical):
        return triage.model_copy(update={"protocol_decision": "", "protocol_route": "",
                                         "opportunity_score": None, "protocol": {}})
    status = communities.status_for_url(mention.url)
    inp = input_for(triage, status, prior_clinical_in_thread=prior_clinical,
                    text=f"{mention.title}\n{mention.text}")
    record = decide(inp, publishable)
    data = record.as_dict()
    guide_link = ""
    if record.decision in SCORED_DECISIONS:
        from harvey import guides

        post = " ".join(part for part in (mention.title, mention.text) if part)
        guide_link = guides.pick_for(triage.product or "", triage.drug or "", post,
                                     triage.subtype or "", triage.unmet_need or "").guide.link_id
    participation = getattr(status, "participation", "none") or "none"
    promotion_ok = participation in PERMITTED or (participation == "with_permission"
                                                  and bool(getattr(status, "permission_obtained", False)))
    data["resource"] = resource_for(record, status.links_allowed, guide_link, promotion_ok)
    data["prior_clinical_in_thread"] = bool(prior_clinical)
    return triage.model_copy(update={"protocol_decision": record.decision, "protocol_route": record.route,
                                     "opportunity_score": record.score, "protocol": data})


async def apply(state, mention, triage):
    """``applied`` with the thread history read from the database."""
    if triage is None or not triage.relevant:
        return applied(triage, mention)
    return applied(triage, mention, prior_clinical=await prior_clinical_in_thread(state, mention))
