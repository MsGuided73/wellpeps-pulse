"""Smart Patient's Guide references.

WellPeps instruction (2026-10-07): "We must refer to a relevant guide and
point to how the guide can help", refined after Derek Goldberg's review of the
Rules of Engagement (2026-10-07): Pulse considers the relevant guide for every
answering reply and includes it when a specific chapter materially helps with
THIS question; otherwise the reply carries no guide and the omission is
recorded with its reason. Chapters come verbatim from config/guides.yaml
(the site's ebooks.ts / guide-pages.ts). Deterministic; no model calls.

- ``guide_for``: the best guide for a product / drug / post text (program
  match, then guide keywords; NAD+ prefers its own guide), else the series
  index (``specific=False``). Never None.
- ``chapters_for``: the chapter(s) that answer the question (subtype, unmet
  need, post words); empty when none does.
- ``best_guide(mention, triage)``: the pick for a mention, or None where a
  guide must never appear (adverse event, emergency, self-harm, legal /
  regulatory, privacy, media, complaints and billing, clinical / individual
  questions, a possible minor, any no-reply or boundary situation).
- ``requirement_for``: what the draft must do: ``link`` (guide + tracked
  registry link + gated-download disclosure), ``name`` (no link allowed here:
  name the guide without one), ``omit`` (considered, but no chapter answers
  this question: no guide, reason recorded), ``forbidden`` (no guide at all:
  excluded situation, or the community prohibits promotion, has not granted
  permission or has unverified rules) or ``none``.
- ``reference_hits``: the deterministic check (compliance filter):
  required but missing -> yellow "missing guide reference" (drafting redrafts
  once, then needs_human); referenced where forbidden -> red; guide linked
  without the email-gate disclosure -> yellow; a "chapter" that is not a
  verbatim chapter title of the guides -> yellow.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from harvey import knowledge, links
from harvey.models.knowledge import Guide, GuideChapter

MISSING_RULE = "GUIDE"
MISSING_REASON = "missing guide reference"
FORBIDDEN_REASON = "guide referenced in an excluded situation"
GATE_REASON = "guide offered without the gated-download disclosure (free, it asks for your email)"
CHAPTER_REASON = "names a guide chapter that is not a verbatim chapter title from config/guides.yaml"
OMIT_REASON = "no guide chapter answers this question (considered and omitted)"
UNVERIFIED_REASON = "community rules are unverified: no company resource until participation permission is verified"
# Situations / categories where a guide never appears (safety, legal, privacy,
# media, complaints and billing; Protocol §8 "never in safety / clinical").
EXCLUDED_CATEGORIES = frozenset({"adverse_event", "legal_regulatory", "privacy", "billing_fraud", "complaint"})
EXCLUDED_SUBTYPES = frozenset({
    "emergency", "self_harm", "media_inquiry", "legal_threat", "regulatory_contact", "records_dm_request",
    "personal_medical_info", "abusive", "dose_question", "lab_question", "medication_change",
    "individual_treatment", "results_question", "safety_question", "qualify_question", "symptom_report",
})
# A complaint about another company that explicitly asks for alternatives is
# answered under the protocol (APPROPRIATE ALTERNATIVE / EDUCATIONAL ONLY), so
# the guide is not excluded by its category there (complaints about WellPeps
# never reach these decisions: they escalate).
ANSWERING_DECISIONS = frozenset({"appropriate_alternative", "educational_only"})
COMPETITOR_COMPLAINT_CATEGORIES = frozenset({"complaint", "billing_fraud"})
EXCLUDED_DECISIONS = frozenset({"clinical_caution", "escalate", "hold", "do_not_engage", "monitor_only"})
# A post that says the author is under 18 ("im 16", "I'm 17", "16 years old").
_MINOR_RX = re.compile(
    r"\b(?:i\s*['’]?\s*a?m|i\s+am)\s+(?:only\s+|just\s+)?1[0-7]"
    r"(?=\s*(?:$|[.,!?;:)]|and\b|but\b|so\b|yo\b|y/?o\b|years?\b|[fm]\b))"
    r"|\b1[0-7]\s*(?:yo|y/o|years?\s+old)\b",
    re.IGNORECASE)
GATE_RX = re.compile(r"\be-?mail\b", re.IGNORECASE)
MAX_CHAPTERS = 2


@dataclass(frozen=True)
class GuidePick:
    guide: Guide
    chapters: tuple[str, ...]
    specific: bool               # False = the series index (no single guide fits)
    helps: bool = True           # a chapter of this guide answers the question

    @property
    def chapter(self) -> str:
        return self.chapters[0] if self.chapters else self.guide.default_chapter

    def as_dict(self) -> dict:
        return {"slug": self.guide.slug, "title": self.guide.title, "short": self.guide.short,
                "claim_id": self.guide.claim_id, "link_id": self.guide.link_id,
                "chapter": self.chapter, "chapters": list(self.chapters), "specific": self.specific,
                "helps": self.helps}


@dataclass(frozen=True)
class GuideRequirement:
    mode: str = "none"           # link | name | omit | forbidden | none
    pick: GuidePick | None = None
    why: str = ""

    @property
    def required(self) -> bool:
        return self.mode in ("link", "name") and self.pick is not None

    def as_dict(self) -> dict:
        return {"mode": self.mode, "why": self.why, **(self.pick.as_dict() if self.pick else {})}


NO_REQUIREMENT = GuideRequirement()


@lru_cache(maxsize=None)
def _rx(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w+-])" + re.escape(term.strip()) + r"(?![\w-])", re.IGNORECASE)


def _hits(terms, text: str) -> int:
    return sum(1 for t in terms if t.strip() and _rx(t).search(text or ""))


def _value(value) -> str:
    return getattr(value, "value", value) or ""


def all_guides() -> tuple[Guide, ...]:
    data = knowledge.guides()
    return (*data.guides, data.series)


def by_claim_id(claim_id: str) -> Guide | None:
    return next((g for g in all_guides() if g.claim_id == claim_id), None)


def guide_for(product: str = "", drug: str = "", text: str = "") -> GuidePick:
    """The single most relevant guide (never None: the series index when no
    program-specific guide fits)."""
    data = knowledge.guides()
    forbid = knowledge.compliance_rules().toggles.forbid_medication_names_in_replies
    programs = [knowledge.program_for(product, drug), *knowledge.programs_in_text(text)]
    programs = list(dict.fromkeys(p for p in programs if p))
    about = " ".join(part for part in (product, drug, text) if part)
    candidates = [g for g in data.guides if not (forbid and g.prefer_keywords)]
    best: Guide | None = None
    best_score = 0
    for guide in candidates:
        score = 0
        if programs and guide.programs and programs[0] in guide.programs:
            score += 10
        elif set(programs) & set(guide.programs):
            score += 5
        score += _hits(guide.keywords, about)
        score += 20 * min(1, _hits(guide.prefer_keywords, about))
        if score > best_score:
            best, best_score = guide, score
    if best is None:
        return GuidePick(data.series, (data.series.default_chapter,), specific=False)
    return GuidePick(best, (), specific=True)


def chapters_for(guide: Guide, subtype: str = "", need: str = "", text: str = "") -> tuple[str, ...]:
    """Up to MAX_CHAPTERS chapter titles that answer this question (book
    chapters before landing topics on a tie); empty when none does."""
    forbid = knowledge.compliance_rules().toggles.forbid_medication_names_in_replies
    from harvey.compliance import names_medication

    scored: list[tuple[int, int, GuideChapter]] = []
    for order, chapter in enumerate((*guide.chapters, *guide.landing)):
        if forbid and names_medication(chapter.title):
            continue
        score = 3 * (subtype in chapter.subtypes) + 2 * (need in chapter.needs) + _hits(chapter.keywords, text)
        if score:
            scored.append((-score, order, chapter))
    scored.sort(key=lambda item: (item[0], item[1]))
    return tuple(c.title for _, _, c in scored[:MAX_CHAPTERS])


def pick_for(product: str = "", drug: str = "", text: str = "", subtype: str = "", need: str = "") -> GuidePick:
    """The guide pick; ``helps`` is False when no single guide fits or no
    chapter of it answers the question (then no guide is offered)."""
    base = guide_for(product, drug, text)
    if not base.specific:
        series = chapters_for(base.guide, subtype, need, text)
        return GuidePick(base.guide, series or base.chapters, specific=False, helps=bool(series))
    chapters = chapters_for(base.guide, subtype, need, text)
    if not chapters:
        return GuidePick(base.guide, (base.guide.default_chapter,), specific=True, helps=False)
    return GuidePick(base.guide, chapters, specific=True)


def mentions_minor(text: str) -> bool:
    return bool(_MINOR_RX.search(text or ""))


def _post_text(mention) -> str:
    return " ".join(part for part in (getattr(mention, "title", "") or "", getattr(mention, "text", "") or "")
                    if part)


def excluded(triage, mention=None) -> str:
    """Why a guide must never appear for this mention ("" = it may)."""
    from harvey import engagement

    if triage is None:
        return "not triaged"
    if engagement.safety_blocked(triage):
        return "safety screen blocks any reply"
    if mention is not None and mentions_minor(_post_text(mention)):
        return "the author may be a minor"
    category, subtype = _value(triage.category), triage.subtype or ""
    answering = (triage.protocol_decision or "") in ANSWERING_DECISIONS
    if category in EXCLUDED_CATEGORIES and not (answering and category in COMPETITOR_COMPLAINT_CATEGORIES):
        return f"category {category}"
    if subtype in EXCLUDED_SUBTYPES:
        return f"subtype {subtype}"
    if (triage.protocol_decision or "") in EXCLUDED_DECISIONS:
        return f"protocol {triage.protocol_decision}"
    if engagement.situation_of(triage).reply != "draft":
        return "no drafted answer in this situation"
    return ""


def best_guide(mention, triage) -> GuidePick | None:
    """The guide + chapter(s) for this mention, or None (excluded situation)."""
    if excluded(triage, mention):
        return None
    return pick_for(triage.product or "", triage.drug or "", _post_text(mention), triage.subtype or "",
                    triage.unmet_need or "")


def requirement_for(situation, context=None, triage=None, mention=None) -> GuideRequirement:
    """What the draft must do about a guide in this situation and community."""
    why = excluded(triage, mention) if triage is not None else ""
    if why or situation.reply != "draft":
        return GuideRequirement("forbidden", None, why or "no drafted answer in this situation")
    policy = getattr(situation, "guide", "none")
    if policy == "none" or triage is None:
        return NO_REQUIREMENT
    pick = best_guide(mention, triage) if mention is not None else pick_for(
        triage.product or "", triage.drug or "", "", triage.subtype or "", triage.unmet_need or "")
    if pick is None:
        return NO_REQUIREMENT
    promo = getattr(context, "promotion_forbidden", "") if context is not None else ""
    if promo:
        return GuideRequirement("forbidden", None, f"the community prohibits promotion ({promo})")
    if context is not None and getattr(context, "participation", "") == "unknown":
        return GuideRequirement("forbidden", None, UNVERIFIED_REASON)
    if not pick.helps:
        return GuideRequirement("omit", pick, OMIT_REASON)
    link_reason = ("this situation never carries a link" if situation.link_policy == "none"
                   else (getattr(context, "link_forbidden", "") if context is not None else ""))
    if link_reason:
        return GuideRequirement("name", pick, f"no link here ({link_reason}): name the guide without a link")
    return GuideRequirement("link", pick, "answering situation: point to the guide with its tracked link")


# --- Deterministic check ---------------------------------------------------------------------------


def _guide_link_ids() -> set[str]:
    return {g.link_id for g in all_guides()}


def _guide_claim_ids() -> set[str]:
    return {g.claim_id for g in all_guides()}


def linked_guides(text: str) -> list[str]:
    """Guide registry link ids present in ``text``."""
    ids = _guide_link_ids()
    return [u.link.id for u in links.find_links(text) if u.link is not None and u.link.id in ids]


def names_guide(text: str, guide: Guide | None = None) -> bool:
    """The text names a Smart Patient's Guide (this one, when given)."""
    body = links.mask_registry_links(text or "", " ")
    if guide is not None and guide.programs:
        short = guide.short.replace(" guide", "")
        return bool(_rx(short).search(body)) and bool(re.search(r"\bguide\b", body, re.IGNORECASE))
    return bool(re.search(r"smart\s+patient", body, re.IGNORECASE)) or (
        bool(re.search(r"\bguides?\b", body, re.IGNORECASE)) and any(
            _rx(g.short.replace(" guide", "")).search(body) for g in all_guides() if g.programs))


def references_guide(text: str, claim_ids=()) -> bool:
    return bool(linked_guides(text)) or names_guide(text) or bool(set(claim_ids) & _guide_claim_ids())


def _accepted_links(pick: GuidePick) -> set[str]:
    if not pick.specific:
        return _guide_link_ids()
    return {pick.guide.link_id}


def satisfied(text: str, requirement: GuideRequirement) -> bool:
    if not requirement.required:
        return True
    pick = requirement.pick
    if requirement.mode == "link":
        return bool(set(linked_guides(text)) & _accepted_links(pick))
    return names_guide(text, pick.guide if pick.specific else None) or names_guide(text)


_CHAPTER_WORD_RX = re.compile(r"\bchapters?\b", re.IGNORECASE)


def _norm(text: str) -> str:
    text = (text or "").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).lower()


def unverified_chapter(text: str) -> bool:
    """The reply says "chapter" but quotes no verbatim chapter title of any
    guide (a paraphrased or invented chapter)."""
    body = links.mask_registry_links(text or "", " ")
    if not _CHAPTER_WORD_RX.search(body):
        return False
    norm = _norm(body)
    titles = [c.title for g in all_guides() for c in (*g.chapters, *g.landing)]
    return not any(_norm(t).rstrip("?.!") in norm for t in titles)


def reference_hits(text: str, claim_ids, requirement: GuideRequirement | None):
    """(red, yellow) Hit lists for the guide rule (compliance.Hit)."""
    from harvey.compliance import Hit

    if requirement is None or requirement.mode == "none":
        return [], []
    if requirement.mode != "forbidden" and references_guide(text, claim_ids) and unverified_chapter(text):
        return [], [Hit(MISSING_RULE, "guide", "", CHAPTER_REASON)]
    if requirement.mode == "forbidden":
        if references_guide(text, claim_ids):
            return [Hit(MISSING_RULE, "guide", "", f"{FORBIDDEN_REASON} ({requirement.why})")], []
        return [], []
    yellow = []
    pick = requirement.pick
    if not satisfied(text, requirement):
        target = pick.guide.short if pick.specific else "Smart Patient's Guides index"
        how = (f"cite {pick.guide.claim_id} with its link" if requirement.mode == "link"
               else "name it without a link (\"our free " + pick.guide.short + " on the WellPeps website\")")
        yellow.append(Hit(MISSING_RULE, "guide", pick.guide.short,
                          f"{MISSING_REASON}: point to the {target} ({how}) and say how it helps, naming "
                          f"\"{pick.chapter}\""))
    elif linked_guides(text) and not GATE_RX.search(links.mask_registry_links(text, " ")):
        yellow.append(Hit(MISSING_RULE, "guide", pick.guide.short, GATE_REASON))
    return [], yellow


def missing(gate) -> bool:
    """The gate carries the "missing guide reference" hit."""
    return any(h.rule_id == MISSING_RULE and h.reason.startswith(MISSING_REASON) for h in gate.hits)


def chapter_line(guide: Guide) -> str:
    """The guide's chapters as approved guide content for the prompts."""
    titles = [c.title for c in guide.chapters] + [c.title for c in guide.landing]
    return "; ".join(f'"{t}"' for t in titles)


# --- Approved guide wording ------------------------------------------------------------------------


def _phrase_rx(phrase: str) -> str:
    words = [re.escape(w).replace("'", "['’]").replace('"', '["“”]') for w in phrase.split()]
    return r"\s+".join(words)


@lru_cache(maxsize=8)
def _wording_rx(key: tuple) -> re.Pattern[str] | None:
    phrases = sorted({p for p in key if p.strip()}, key=len, reverse=True)
    if not phrases:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(_phrase_rx(p) for p in phrases) + ")", re.IGNORECASE)


def approved_wording() -> tuple[str, ...]:
    """Guide titles, "<name> guide" forms and chapter titles: verbatim,
    approved guide content (config/guides.yaml)."""
    out = []
    for guide in all_guides():
        out += [guide.title, guide.short]
        out += [c.title.rstrip("?.!") for c in (*guide.chapters, *guide.landing)]
    return tuple(out)


def mask_guide_wording(text: str, placeholder: str = "[guide]") -> str:
    """``text`` with approved guide titles and chapter titles masked, so the
    review-only (yellow) wording patterns don't flag a guide's own name or
    chapter ("GLP-1 Weight Loss", "Oral vs. topical")."""
    rx = _wording_rx(approved_wording())
    return rx.sub(placeholder, text or "") if rx else (text or "")
