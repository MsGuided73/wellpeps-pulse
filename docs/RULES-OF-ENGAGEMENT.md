# WellPeps Pulse: Rules of Engagement

This file is the source of truth for how Pulse engages in public
conversations on WellPeps' behalf. The machine-readable part lives in
`config/engagement_guide.yaml` (situations, templates, persona, the
competitor / switching protocol, FINALIZE items), `config/communities.yaml`
(community rules), `config/claims.yaml` (approved wording) and
`config/compliance_rules.yaml` (the deterministic filter). When this file and
the config disagree, fix the config; when either disagrees with a WellPeps
document, the priority order below decides.

Pulse drafts and recommends. A human reviews, edits, approves and posts.
Pulse never publishes, sends direct messages, enrolls anyone or makes a
clinical decision ([CP] cover page, §13; hard rule 3 in CLAUDE.md).

## Sources

| Tag | Document | Version |
|---|---|---|
| [CP] | WellPeps_AI_Competitor_Mentions_and_Provider_Switching_Protocol_V1 (`C:\dev\W\docs\community-engagement-agent\...V1.pdf`) | 1.0, Oct 6 2026, internal implementation draft |
| [AMG] | WellPeps Approved Messaging & Response Guide | 1.0, Aug 2026 |
| [LR] | WellPeps Approved Messaging Live Reference | 1.0, Aug 2026 |
| [CT] | WellPeps Community Engagement Compliance Training | 1.0, Aug 2026 |
| [OM] | WellPeps Comprehensive Community Engagement Operations & Training Manual (docx and the `_V1` PDF have the same text) | 2.0, Aug 2026 |
| [CG] | WellPeps Community Groups and Forums (owner's principles) | undated |
| [PL] | WellPeps Pre-LegitScript Marketing Community Strategy | Sep 2026 |

Originals (except [CP]): `C:\dev\W\docs\community-engagement-agent\Community Engagement-20261007T043724Z-1-001.zip` -> `Community Engagement/<file>`.
Pulse never modifies anything under `C:\dev\W`.

## Priority order (binding, user instruction 2026-10-07)

"Prioritize these new guidelines over any of our previous rules."

1. **[CP]** controls competitor mentions, dissatisfaction, provider switching,
   opportunity scoring and its decision sequence. It says later approved
   legal, clinical, privacy and compliance directives control over it.
2. **The other Community Engagement documents** ([AMG], [LR], [CT], [OM],
   [CG], [PL]) override everything that came before.
3. **Everything earlier** (the Aug 2026 Agent Program Gameplan, the
   registry-derived reply rules R1-R39, the conversion playbook,
   `reply_examples.yaml` and the claims added in commit 9c21c7b, Pulse
   defaults) applies only where the new guidelines are silent.

Exception: an earlier rule that protects against a specific legal or
regulatory risk the new guidelines do not address stays in force, and is
listed under [Retained earlier safeguards](#retained-earlier-safeguards--confirm-with-wellpeps)
for WellPeps to confirm. Every earlier rule that was overridden is listed
under [Superseded earlier rules](#superseded-earlier-rules).

## 1. Principles

- Educate before you promote; a reply must still be useful with every
  WellPeps mention removed ([OM] §1, §2, §27 step 7; [CT] Module 1; [AMG] §2).
- Be transparent about the WellPeps relationship; never pose as an
  independent customer, never anonymous or fake grassroots accounts ([AMG]
  §4; [OM] §3.5, §4; [CG] First Principle, Option 5; [CP] §1).
- Never give individualized medical advice: no diagnosis, no medication or
  dose recommendation, no lab interpretation, no start/stop/change advice
  ([AMG] §2, §6; [OM] §8-9; [CT] Module 3; [CP] §1).
- Protect privacy: never request, repeat or amplify health or account
  details; never confirm patient status ([AMG] §8; [OM] §10; [CT] Module 5).
- Use only current approved information; never create claims; no
  guarantees, no unsupported safety, efficacy or comparative claims ([AMG]
  §2, §7, §26; [OM] §20; [LR] §13; [CT] Module 4, 10).
- Platform and community rules come first: WellPeps approval never overrides
  them ([CT] Module 9; [AMG] §17; [OM] §5.2, §12).
- Respond to the unmet need, not the named provider; a complaint is the
  poster's account, not a fact; never infer clinical failure ([CP] §1).
- Safety, privacy, community permission and approval come before commercial
  opportunity ([CP] §1, §4, §9).
- When uncertain, do not guess: escalate or hold ([OM] §1; [CT] Appendix A;
  [CP] §4).

## 2. Personas and disclosure

Pulse drafts as one of two personas (`engagement.persona` in
`config/engagement_guide.yaml`). There is deliberately no anonymous persona
([CG] Option 5; [OM] §3.5).

| Persona | When | Opening sentence | Source |
|---|---|---|---|
| `official_account` (default) | WellPeps' own accounts | "I work with WellPeps." | [OM] §3.2; [CG] Option "WellPeps Official Account"; [CP] §7 ("Official accounts use clear company identity") |
| `identified_employee` | a named team member (`display_name`, no clinical titles) | "Hi, I'm <name> — I work with WellPeps." | [CG] Option 1; [AMG] §4; [PL] Channel 3 / 6 |

Approved disclosure forms ([AMG] §4; [CT] Module 2; [OM] §4.2):

- Employees / community team: "I work with WellPeps." (default, [CP] §7) or
  "I'm part of the WellPeps team." (used for complaints about WellPeps,
  [AMG] §9, [CP] Examples 17-18).
- Brand Ambassadors: "I'm a WellPeps Brand Ambassador."; partners /
  influencers: "I partner with WellPeps." Pulse never drafts as an
  influencer, so these forms are yellow in a Pulse draft (R47).
- The earlier "Full disclosure: I work with WellPeps, so I'm not neutral."
  ([PL] Channel 3) stays allowed; it is no longer the default.

The disclosure must be in the first sentence and easy to notice ([AMG] §4;
[OM] §4.3): a missing or buried disclosure is red (R3).

## 3. How Pulse decides

1. **Triage** (a model) classifies the post: category, subtype (the
   situation), competitor, and the protocol inputs ([CP] §3, §9): intent
   tags, the underlying need, need clarity and useful contribution (0-2).
   The urgent-keyword override and the independent safety screen run as
   before.
2. **The protocol** (`harvey/protocol.py`, pure Python) applies to mentions
   in its scope (a named competitor; an alternatives, comparison, venting,
   deceptive-request or possible-serious-harm intent; a provider-access,
   continuity, fulfillment, lab-terms or medication-availability need; a
   clinic recommendation or competitor comparison / praise; instructions
   inside the post; a follow-up after a clinical concern in the thread). It
   runs the decision sequence and stores the classification, escalation
   route, opportunity score and record on the triage row.
3. **Escalation** pages the owner for severe categories, safety subtypes and
   the protocol's ESCALATE route (also when the public decision is HOLD).
4. **The situation** (first match in `engagement_guide.yaml`) decides the
   reply mode: `draft` (model draft in the template's shape), `boundary_only`
   (the approved response verbatim, no model call), `stop` (empty draft for a
   human) or `no_reply`.
5. **Drafting** checks the community registry and the stop rules first, then
   drafts. Every draft goes through the deterministic filter (with the
   community's rules) and, for model drafts, the adversarial reviewer.
6. **A human** approves in the review desk (with the clinical gate for adverse
   events and emergencies), copies the reply and posts it by hand.

## 4. Situation playbook

"Approved response" means the wording is copied verbatim from [AMG] into
`config/claims.yaml` (`CLM-AMG-*`, `approved_by` the guide; publishable while
`claims_policy.trust_approved_messaging_guide` is true and no FINALIZE item
is open).

| Situation (id) | What Pulse does | Escalation | Approver | Source |
|---|---|---|---|---|
| Medical emergency (`emergency`) | Emergency line only (CLM-AMG-11-EMERGENCY) | clinical owner (adverse_event) | clinical / admin | [AMG] §11, §24; [OM] §14.1 |
| Possible self-harm (`self_harm`) | No reply; the clinical owner decides | adverse_event | — | [OM] §14-15 (retained safeguard) |
| Adverse event, any thread (`adverse_event`, `adverse_event_dose_change`) | The guide's public boundary reply (CLM-AMG-10-REACTION / -DOSE-CHANGE), never causation, troubleshooting, links or marketing | adverse_event, immediately | clinical / admin | [AMG] §10, §19; [OM] §14.2, §19, §25; [CT] Module 8; [CP] §5, Examples 4, 18 |
| Media inquiry to WellPeps (`media_inquiry`) | Routing line only (CLM-AMG-23-MEDIA), never substantive comment | legal owner until a media contact is named | reviewer | [AMG] §23; [OM] §15, §25; [CT] 8B |
| Journalist seeking sources elsewhere (`media_other`) | No reply | — | — | [AMG] §23; [OM] §5.3 |
| Legal threat / regulator (`legal_regulatory`, `legal_subtype`) | No substantive reply; preserve and route | legal | — | [AMG] §23; [OM] §15, §25; [LR] §14 |
| Privacy / HIPAA concern (`privacy_concern`) | No public reply drafted (minimal response is the privacy owner's call) | privacy | — | [AMG] §23; [OM] §10, §25; [CT] Module 5 |
| Abusive / threatening thread (`abusive`) | Stop: empty draft for a human; moderation / escalation by hand | — | — | [AMG] §22; [OM] §13.3 |
| Billing complaint about WellPeps (`billing_complaint`) | Template C (CLM-AMG-09-BILLING, "I'm part of the WellPeps team.") | billing_fraud | reviewer (blocked until FINALIZE support_channel) | [AMG] §9, §25 C; [CP] Example 17 |
| Complaint about WellPeps (`complaint`) | Template C (CLM-AMG-APPX-COMPLAINT); a viral one is also paged | viral_negative when urgent | reviewer (blocked until FINALIZE support_channel) | [AMG] §9, §25 C; [OM] §13 |
| Complaint about another company (`complaint_other`, `billing_other`) | No reply; never pile on a competitor | billing_fraud for fraud accusations | — | [AMG] §13; [OM] §5.3 |
| Poster shares medical details (`personal_medical_info`) | Privacy response (CLM-AMG-08-POSTED-DETAILS) | — (privacy page in protocol scope) | reviewer (blocked until FINALIZE care_routing) | [AMG] §8; [OM] §10.2; [CT] Module 5; [CP] Example 16 |
| Wants to send records by DM (`records_dm_request`) | CLM-AMG-08-DM-RECORDS | — | reviewer (FINALIZE care_routing) | [AMG] §8; [OM] §10.3 |
| Dose / labs / stop-change / which treatment / results / is it safe / do I qualify, asked of WellPeps | Template B boundary (CLM-AMG-06-*, -25-TEMPLATE-B, -07-*, -05-QUALIFY) | — | reviewer | [AMG] §5-7, §25 B; [OM] §9, §19; [CT] Module 3 |
| The same in a third-party thread (`individual_third_party`) | No reply (outside the protocol's scope) | — | — | [OM] §5.3, §27 |
| Misinformation about WellPeps | Template D model draft; the correction must be an approved claim | — | reviewer | [AMG] §13, §21, §25 D; [OM] §13.2 |
| General health misinformation (`misinformation`) | No reply; a human decides | — | — | [OM] §13.2; [CT] Module 7 |
| Has anyone used WellPeps / how it works / pricing / general education / question | Template A model draft from approved claims | — | reviewer | [AMG] §5, §12, §17-18, §25 A |
| Competitor mentions and provider switching | See section 5 | per protocol | per protocol | [CP] |
| Minors (safety screen) and failed screens | Never any reply, boundary or not | as before | — | retained safeguard R33 |

## 5. Competitor mentions and provider switching ([CP])

### Scope

A mention is in the protocol's scope when triage names a competitor, or
tags an alternatives request, a comparison request, venting, a deceptive
request or possible serious harm, or the need is provider access,
continuity, fulfillment, lab terms or medication availability, or the
subtype is a clinic recommendation or competitor comparison / praise; and
always when the post carries instructions to the tool ([CP] §2) or is a
follow-up after an earlier clinical concern in the thread ([CP] Example 25).
`switching_protocol` in `config/engagement_guide.yaml` lists these.

### Decision sequence ([CP] §4), as `harvey.protocol.decide`

1. **Access and participation.** Community prohibits business
   participation: DO NOT ENGAGE. Rules or permissions unknown: HOLD (an
   invitation to recommend providers does not override community rules).
   Safety observations are still routed internally. Instructions inside the
   post or a request to hide the affiliation: DO NOT ENGAGE ([CP] §2,
   Example 24).
2. **Safety and incident overrides:** ESCALATE (route: adverse_event for an
   emergency, a serious / unexpected / historical treatment-related health
   report or possible serious harm; legal for attorney, regulator or a media
   inquiry to WellPeps; privacy for posted records; billing_fraud or support
   for a complaint involving WellPeps). Marketing and resources suppressed.
3. **Clinical boundary:** CLINICAL CAUTION for symptoms, testing needs,
   eligibility, labs, dose changes, interactions, medication decisions, or a
   follow-up in a thread with an earlier clinical concern.
4. **Intent and useful contribution:** venting only or nothing useful:
   MONITOR ONLY; an ambiguous post ("Is this normal?"): HOLD; an explicit
   request for alternatives with an approved fact that fits and a community
   that allows promotion: APPROPRIATE ALTERNATIVE; otherwise EDUCATIONAL ONLY.
5. **Facts and resource fit:** a WellPeps-specific question whose central fact
   is not approved: HOLD ([CP] Examples 19, 21); an alternatives request with
   no approved fact: EDUCATIONAL ONLY.
6. **Review record** (below). No automated publishing.

| Classification | What Pulse drafts | Required review ([CP] §4) | Situation |
|---|---|---|---|
| APPROPRIATE ALTERNATIVE | Model draft: education plus ONE brief, disclosed, factual option ("WellPeps is one option you can evaluate"), no superiority | routine authorized review and claim checks | `protocol_appropriate_alternative` |
| EDUCATIONAL ONLY | Model draft: general useful answer, no promotion; WellPeps facts only if asked about WellPeps | routine authorized review | `protocol_educational_only` |
| CLINICAL CAUTION | The approved boundary for the subtype (dose, stop/change, labs, eligibility, symptoms, results, safety, else Template B); no resource, no testing promise | trained reviewer; clinical escalation if required | `protocol_clinical_*` |
| ESCALATE | Approved safety / privacy / complaint holding language (adverse-event reply needs a clinical approver); legal: nothing | designated incident owner immediately | the guide's safety situations, else `protocol_escalate_*` |
| MONITOR ONLY | Nothing | optional trend review | `protocol_monitor_only` |
| HOLD | Nothing until the blocker is resolved | resolve context, permission or evidence | `protocol_hold` |
| DO NOT ENGAGE | Nothing; no DM, no channel workaround | record reason | `protocol_do_not_engage` |

The review desk recomputes the decision with the current config before
approval: a draft whose live decision is HOLD, DO NOT ENGAGE or MONITOR ONLY
cannot be approved (e.g. a community's rules changed).

### Opportunity score ([CP] §9)

Only after the gates and only for EDUCATIONAL ONLY and APPROPRIATE
ALTERNATIVE; otherwise null. Four components, 0-2 each, total 0-8:

| Component | 0 / 1 / 2 | Who supplies it |
|---|---|---|
| Alternative intent | none / general interest (comparison, general information, a WellPeps question) / explicit alternatives request | Pulse, from the intent tags |
| Need clarity | unclear / broad / specific service need | triage (model) |
| Approved capability fit | unavailable / partial / directly supported | Pulse: `switching_protocol.needs` maps each need to approved claims; only publishable claims count ([CP] §6) |
| Useful contribution | none / generic / specific and helpful | triage (model) |

6-8 high, 3-5 moderate, 0-2 low (proposed internal prioritization, not a
clinical score). High opportunity still needs an explicit alternatives
request for APPROPRIATE ALTERNATIVE. Desperation, vulnerability or severity
is never scored. The review desk sorts safety incidents first, unresolved
gates second, then the rest ([CP] §9).

### Brand modes ([CP] §7)

| Mode | When | Limits |
|---|---|---|
| none | MONITOR / HOLD / DO NOT ENGAGE / legal ESCALATE | no company contribution |
| affiliation only | education, clinical and safety replies | identity disclosure is not a sales pitch |
| brief factual option | APPROPRIATE ALTERNATIVE | one concise fact; no superiority; no promise it is right for them |
| requested process detail | the person asks about WellPeps (EDUCATIONAL ONLY) | approved facts and a relevant approved link only |

### Capability limits ([CP] §6)

Approved ongoing support (CLM-LR-02-ONGOING-SUPPORT) is never a
response-time promise, direct physician access, video visits or 24/7 care.
Lab access, ordering, necessity and price inclusion are separate and none is
approved (Live Reference §6 Labs: CONFIRM), so lab questions about WellPeps
HOLD and lab-inclusion wording is red. Switching never promises the same
medication or dose, approval, immediate fulfillment, record transfer or
uninterrupted treatment. Price replies use only approved program-specific
inclusions; never "everything included". Compounding is never promised as
suitable, safer or superior. The protocol's lab sentence ("WellPeps can make
lab testing available when a provider determines it is appropriate") is NOT
activated (finalize `lab_terms`).

### Prohibited behaviors and how they are caught

| Behavior ([CP] §1, §7, §8, §11) | Caught by |
|---|---|
| Naming / repeating the provider | R48 (code, red) |
| Adopting the accusation ("We hear that all the time", "I've heard that too") | R49 (red) |
| Implied superiority ("Unlike them", "We actually care", "We do it right", "Finally, a provider that...", "better support") | R49, R41 (red) |
| Inferring clinical failure ("should have tested you", "negligent", "they missed") | R49 (red) |
| Switching pressure ("Switch to WellPeps", "Try us", "sign up now") | R49 (red) |
| Response-time, lab, "everything included", same-dose and transfer promises | R49 (red) |
| DM migration / unsolicited outreach ("I'll DM you", "DM me") | R49 (red); Pulse has no messaging capability |
| Vote manipulation ("upvote") | R49 (red) |
| Repeated promotional comments | repeated-link rule (R43 yellow), one reply per thread (stop rule), human review |
| Posing as an independent customer | R2 / R49 (red), disclosure rule R3 |
| Marketing attached to a safety response | boundary-only situations (no model draft, no link), filter |

### Required output record ([CP] §10)

Stored on `triage.protocol_json` (no post text): version, decision and
label, rule-based rationale, route and whether safety was routed despite a
public hold, community rule status, intent tags, need (label and focus),
risks (clinical flag, privacy flag, competitor-claim risk, confidence),
opportunity components / total / band (or null), brand mode and limits,
disclosure, resource (always "none" until a verified live link fits),
evidence (the approved claim ids behind the WellPeps facts), required
review, blockers, queue rank, and "human only" publishing. The review desk
adds the escalation handoff status ([CP] §5: never "escalated" unless the
page went out; the support route has no queue yet, so it shows as not handed
off). The audit log keeps prompt/model, reviewer edits and the posting
outcome.

### Examples as evaluation cases ([CP] §12-§14)

`tests/fixtures/protocol_examples.yaml` holds all 25 examples with the
triage inputs a model would supply. `tests/test_protocol_eval.py` checks for
each: the protocol's decision, the opportunity score and band (null when
gated), the brand mode, disclosure and route, the Pulse situation and what
it drafts (no model draft and no link for clinical / safety), that serious
reports reach the designated owner, that the protocol's suggested reply
passes the filter (no red, at most 90 words), and that each "Do not say"
line is red. Re-run it after any prompt, model, retrieval or source change
([CP] §14).

### Human posting workflow ([CP] §11)

Before posting, the human confirms the thread is current and no WellPeps
representative has already replied (Pulse also refuses a second draft in a
thread with one waiting), checks community permission and account role,
verifies every claim, disclosure and link, removes any clinical inference,
competitor accusation, unsupported comparison or switching pressure,
resolves blockers and routes incidents (editing a marketing draft never
satisfies a safety escalation), posts only from an authorized account and
marks it posted with the permalink. Follow-ups are new mentions and are
reclassified; nothing auto-replies.

## 6. Platform and community rules

`config/communities.yaml` is the registry ([PL] Channel 3: "maintain a
database of each community's rules"). Every community starts `unknown`.

| Rule status | Effect |
|---|---|
| `prohibited` | No reply at all (audit `skipped`, never retried); the protocol says DO NOT ENGAGE; a draft is red (R44). |
| `unknown` (or not registered) | Competitor / switching posts: HOLD, nothing drafted ([CP] §2). Everything else: drafted, yellow "community rules unverified", no link (a link is red). |
| `with_permission`, permission not obtained | Yellow; no link and no promotion (red); an alternatives request is downgraded to EDUCATIONAL ONLY. |
| `allowed` | Normal; `links_allowed: false` still makes any link red. |
| no community (owned channels, review sites, Instagram / TikTok comments) | Platform rules only. |

Platform notes ([OM] §12; [CT] Module 9; [AMG] §17-20; [PL]): Facebook
groups need admin permission for promotion and consistent participation;
Reddit replies must be useful without WellPeps, disclose whenever WellPeps is
discussed, respect moderators, no repetitive links, vote manipulation or
pile-ons; Instagram / TikTok keep comments educational and escalate safety
comments (TikTok drafts are always yellow, R34); LinkedIn stays professional
with no patient-specific discussion.

## 7. Link rules

- Answer the question in words first; a link alone is red (R43, [AMG] §14;
  [OM] §11.1).
- At most one link, only from `config/links.yaml` through a cited claim
  (R6), only when it directly answers the question and the community allows
  links ([AMG] §14; [CP] §8).
- Never in safety, emergency or individual clinical replies ([CP] §8).
- No verified link (`live: false`): yellow and approval blocked; never a
  generic homepage or invented slug ([CP] §8).
- Say plainly that the Smart Patient Guides ask for an email; "free" must
  match the access terms ([CP] §8; finalize `gated_download_disclosure`).
- The same link in the same community within 7 days: yellow "repeated link"
  ([AMG] §14; [OM] §11.3).
- No assessment or checkout links before LegitScript (R8, retained).

## 8. The 80/20 guideline

About 80% education and 20% promotion is a planning principle, not a
per-reply quota or an exception to platform rules; where a community
prohibits promotion the share is zero ([CP] §1; [OM] §2.1; [CT] Module 1;
[CG] 80/20 rule; [AMG] §17). Pulse shows the share per community on the
review desk and per platform / community on the Analytics "Education vs
promotion (80/20)" card (`GET /api/analytics/engagement-mix`); it never
blocks or flags a reply on it. A reply counts as promotional when it carries
a link, cites a promotional claim, names a price or uses a call to action
(`harvey/promotion.py`).

## 9. When to stop ([AMG] §22; [OM] §13.3)

- Two approved WellPeps replies in a thread already: no third (empty draft
  for a human).
- Another WellPeps reply is waiting for review in the thread: no second
  draft ([CP] §9; [PL] "Don't have five employees pile onto the same
  thread").
- The same person repeats an individual medical request after a boundary
  reply: the graceful close (CLM-AMG-22-CLOSE) for a human.
- The person keeps posting private medical information after a privacy
  reply: no reply.
- Abusive or threatening thread: no reply; moderation and escalation by hand.
- A moderator asks WellPeps to stop, or the issue is escalated and public
  discussion could interfere: the human stops (record it in
  `config/communities.yaml` when it is a community rule).

## 10. Escalation ([AMG] §23-24; [OM] §14-15, §25; [LR] §14; [CP] §5)

| Issue | Public action | Route |
|---|---|---|
| Medical emergency | emergency line only | clinical owner (adverse_event), immediately |
| Adverse event / serious health report (any provider) | boundary reply, clinical approver | adverse_event, immediately |
| Privacy / HIPAA concern | none (privacy owner decides); posted records in protocol scope: privacy reply | privacy, immediately |
| Legal threat, regulator | none | legal, immediately |
| Media inquiry | routing line only | legal until a media contact is named |
| Billing / account complaint about WellPeps | Template C | billing_fraud |
| Other complaint about WellPeps | Template C | viral_negative when urgent; otherwise support by hand (no queue yet) |
| Threat / serious safety concern, self-harm | none | adverse_event |

Escalations ignore quiet hours, page with a link and a category only, are
re-paged by the sweep when unsent or past SLA, and show "NOT handed off" on
the review desk until the page went out.

## 11. Words and phrases to avoid

From [AMG] §7, §9, §26, [LR] §13.4, [OM] §19 and every [CP] "Do not say"
line; all are red in the filter (R13, R14, R17, R40, R41, R48, R49) and the
drafter sees them as its "never write" list:

Guaranteed / guaranteed results; everyone qualifies; completely safe; no
risk; no side effects; best treatment for you; you should take...; you
need...; stop taking...; this is definitely caused by...; this will fix...;
better than your doctor's treatment; better / safer / more effective /
cheaper than [competitor]; "That's not true."; "You're wrong."; "You must
have misunderstood."; "Nobody else has complained."; "Delete your comment and
DM me."; "Most people lose at least 25 pounds."; "Start low and increase
after a month."; "skip it"; "Your labs look fine."; "It's very safe.";
"probably unrelated"; "We hear that about [provider] all the time."; "We have
better support."; "Switch to WellPeps — we care more."; "they should have
tested you"; "We include labs."; "Try us"; "our formulation is easier on
your stomach"; "Go back to your old dose."; "We are cheaper than [provider]."
; "we offer better medical care"; "That is negligent care."; "Choose
WellPeps; the others just sell prescriptions."; "exactly which medication you
need"; "Double up"; "We will match your dose."; "would never treat you that
way"; "I'll DM you"; "Your results show they missed a problem."; "That
cannot happen under our policy."; "Our medication did not cause that."; "All
lab tests are included."; "We answer within an hour, unlike [provider]."; "We
can get anything compounded for you."; "their care is unsafe"; "We prevent
reactions"; "I'm just a happy independent customer."; "sign up now and we'll
solve the problem".

Preferred alternatives ([AMG] §26): "Results vary by individual." "No
specific outcome can be guaranteed." "A licensed healthcare provider can
determine whether treatment is appropriate." "I can provide general
educational information." "I don't want to speculate." "I can explain the
WellPeps process." "For your privacy, please don't post personal medical
information here."

## 12. Enforced by

| Rule | Where | Tests |
|---|---|---|
| Situations, templates, approved responses, clinical approval | `config/engagement_guide.yaml`, `harvey/engagement.py`, `harvey/drafting.py` | `tests/test_engagement_routing.py`, `tests/test_engagement_drafting.py` |
| Competitor / switching decision sequence, scoring, record | `harvey/protocol.py`, `switching_protocol` in the guide config, `harvey/agents/triager.py` | `tests/test_protocol_eval.py`, `tests/test_protocol_pipeline.py` |
| Escalation of protocol routes | `harvey/escalation.py` (`escalation_kind`) | `tests/test_engagement_routing.py`, `tests/test_protocol_eval.py` |
| Disclosure forms, words to avoid, R40-R42, R47-R49 | `config/compliance_rules.yaml`, `harvey/compliance.py` | `tests/test_engagement_rules.py`, `tests/test_protocol_eval.py`, `tests/test_compliance.py` |
| Approved wording and FINALIZE | `config/claims.yaml` (`CLM-AMG-*`, `CLM-LR-*`, `finalize`), `knowledge.publishable_claim_ids` | `tests/test_knowledge.py`, `tests/test_engagement_rules.py` |
| Community rules | `config/communities.yaml`, `harvey/communities.py`, filter context (R44) | `tests/test_engagement_rules.py`, `tests/test_engagement_drafting.py` |
| Link rules | `config/links.yaml`, `harvey/links.py`, R6 / R8 / R43 | `tests/test_links.py`, `tests/test_engagement_rules.py` |
| Stop rules, one reply per thread | `harvey/engagement.py` (`stop_decision_from`) | `tests/test_engagement_drafting.py`, `tests/test_protocol_pipeline.py` |
| 80/20 as a planning metric | `harvey/promotion.py`, `engagement.mix_report`, Analytics card | `tests/test_engagement_rules.py`, `tests/test_engagement_review.py` |
| Approval gate (clinical approver, FINALIZE, live protocol decision, community rules) | `harvey/review.py`, `harvey/auth.py` (`approve_clinical`) | `tests/test_engagement_review.py` |
| Review desk display (CSP-safe) | `harvey/web/app.js` (`engagementHtml`, `protocolHtml`), `replies.js` | `tests/test_engagement_review.py` |
| Persona | `engagement.persona` | `tests/test_engagement_drafting.py` |
| Drafter / reviewer instructions | `prompts/draft.md`, `prompts/review.md`, `prompts/reply_rules.md`, `prompts/triage.md` | `tests/test_protocol_pipeline.py`, prompt leak tests |
| No autonomous posting | no publish / message path in Pulse | `tests/test_protocol_eval.py` |

## Superseded earlier rules

| Earlier rule | Now | Overriding source |
|---|---|---|
| R38: no medication or drug-brand names in replies (`forbid_medication_names_in_replies: true`) | Names allowed for general treatment-category education (yellow for review); never an individual recommendation or dose; compounded-equivalence stays red (retained R15/R16) | [CT] Module 3 (general-to-individual boundary: "the difference between semaglutide and tirzepatide" can be answered generally); [CG] Official Account; [OM] §9.1; [PL] "Review first: medication names" |
| Conversion playbook step 3: ONE WellPeps fact in every purchase-intent / question reply | WellPeps facts only when the person asks about WellPeps or explicitly asks for alternatives in a community that allows it; otherwise the disclosure only | [CP] §1, §7 (brand modes); [OM] §12.2 ("If a WellPeps mention is not necessary... consider leaving it out"); [AMG] §2 |
| Conversion playbook: the program's guide claim pushed right after the disclosure, "one CTA (the guide)", guide link on purchase intent | A resource is optional, only when it directly answers and the community allows links, never in safety / clinical replies, email gate stated; the guide claim is offered after the fixed claims | [CP] §8; [AMG] §14; [OM] §11.1; [PL] Channel 1 step 6 ("Not every comment gets a link") |
| Default disclosure "Disclosure: I work with WellPeps, so I am not neutral." (CLM-R3-DISCLOSURE) | Default "I work with WellPeps." (old form still accepted) | [AMG] §4; [CP] §7 |
| CLM-PRICE-FOLLOWUP "...included in the one monthly price" as a fixed playbook claim | Not offered first; FINALIZE `pricing`; the Live Reference's approved ongoing-support fact is used instead | [CP] §6; [LR] §6 ("One Simple Price. Everything Included." needs verification); [AMG] §12 FINALIZE |
| CLM-PRICE-ALLIN (includes "standard shipping") | FINALIZE `pricing` | [LR] §6-7 (shipping CONFIRM); [CP] §6 |
| `config/reply_examples.yaml` (four playbook examples) | Rewritten: primary disclosure, no unsolicited WellPeps fact, no "one monthly price", email gate stated, 40-90 words | [CP] §7-8; [AMG] §4 |
| 80/20 as a per-reply gate (partial work: R45 yellow; no link / education only when a community is over 20%) | Planning metric only (review desk chip, Analytics card) | [CP] §1; [OM] §2.1 |
| Severe categories are never drafted (`NO_DRAFT_CATEGORIES`, before 2026-10-06); third-party adverse events get no reply (partial work) | Adverse events in any thread get the guide's boundary reply for a clinical approver, plus the page; billing / complaints about WellPeps get Template C | [AMG] §9-10, §19, §25; [OM] §19, §25; [CP] §5, Examples 4, 17-18 |
| Unknown community rules: draft yellow, no link (partial work, for every post) | For competitor / switching posts: HOLD, nothing drafted (other posts unchanged) | [CP] §2, §4 step 1, Example 14 |
| Third-party individual medical questions: no reply ([OM] §5.3) | In the protocol's scope: CLINICAL CAUTION with the approved boundary | [CP] §4 step 3, Examples 3, 8, 11 |
| R31 red for "your care team" | Generic "your care team" allowed; "your WellPeps care team" stays red | [CP] Example 1 ("ask how you can contact your care team") |
| R31 yellow for "DM us / message me" | Red (R49): no DM migration or unsolicited messages | [CP] §1, §8, §11, Example 15; [OM] §12.1; [AMG] §9 |
| R7 / R9: personal health question -> thank, general education, suggest a provider (model-written) | The approved boundary wording verbatim (Template B, §6 responses), no model call | [AMG] §6, §25 B; [OM] §9.3 |
| Prompt line "no medication or brand names" (draft.md) | Medication names only for general education; never which medication or dose is right for someone | as R38 above |

## Retained earlier safeguards — confirm with WellPeps

These earlier rules protect against a specific legal or regulatory risk the
new guidelines do not address (or address less strictly). They stay in force
until WellPeps confirms or drops them.

| Safeguard | Risk | Where | Guideline position |
|---|---|---|---|
| R15 / R16: never imply a compounded medication is the same as, equivalent to or a generic of a brand drug, or FDA-approved; no brand name as a WellPeps product | FDA misbranding / warning-letter language | `compliance_rules.yaml` R15, R16 | [LR] §13.1 ("Compounded medications are not FDA-approved as finished drug products") and [PL] "Review first: compounded" support it; no explicit sameness rule |
| R12: no LegitScript / NABP certification claims while pending, no "certification-pending" wording, no "FDA-approved pharmacy" | LegitScript denial / revocation; false accreditation | R12 (toggle `allow_certification_claims: false`) | [PL] DON'T list ("certification-pending language") agrees; pharmacy accreditation not addressed |
| R8: no assessment, intake or checkout links and no "Start your assessment" before LegitScript | platform / LegitScript circumvention | R8 | [PL] lists such links as "Review first" (Pulse is stricter: red); [AMG] §15, §19 give assessment-link patterns with a FINALIZE slot |
| R31: never reference the person's order, prescription, account, records, "your provider" or confirm patient status | HIPAA / patient-status disclosure | R31 (`patient_confirmation`) | [OM] §8, [CT] Module 5 forbid confirming patient status; the phrase list is Pulse's inference |
| R33 + safety screen: possible minors never get a reply (boundary or not) | weight-loss marketing to minors | R33, `safety_screen` | not addressed |
| Self-harm: no reply, clinical owner paged | crisis handling | `self_harm` situation | [OM] §15 (credible safety threats) only in general terms |
| R13 / R14 / R17 / R27: no outcome numbers, safety absolutes, universality, unsourced statistics | FTC substantiation | prohibited / yellow patterns | consistent with [AMG] §7, §26; [CT] Module 4 |
| R39: no urgency / scarcity language | LegitScript / FTC | R39 | not addressed explicitly |
| R37 / R6: hashtag limits, at most one link | platform spam rules | `limits` | consistent with [OM] §11.3; [PL] |
| Slack and the #pulse-query bot carry a link and a category only, never post text | PHI minimisation | `harvey/notify/slack.py`, `harvey/slackbot/` | consistent with [CP] §2, [OM] §22.2 |

## FINALIZE — needed from WellPeps

Consolidated from [AMG] FINALIZE notes and Appendix B, [LR] CONFIRM / TO BE
ESTABLISHED items and Appendices A-B, [CT] Appendix B, [OM] Appendix C and
[CP] §13. Machine copy: `finalize:` in `config/engagement_guide.yaml` (set
`value` when provided). **Blocks approval**: claims that depend on it cannot
be approved. **Blocks go-live**: Pulse must not be used for real posting
until it is provided.

| Key | Needed from WellPeps | Blocks approval | Blocks go-live | Pulse until then |
|---|---|---|---|---|
| `support_channel` | Approved customer-support channel, complaint-routing language and complaint escalation queue | yes | — | Template C replies drafted but not approvable; support route shows "not handed off" |
| `care_routing` | Provider / care routing for existing patients (portal / provider messaging) | yes | — | Privacy replies drafted but not approvable |
| `provider_routing` | Assessment / provider-access link or routing instruction | yes | — | CLM-AMG-05-TALK-TO-PROVIDER not approvable |
| `assessment_url` | Production assessment URL and any assessment disclosure | yes | — | Assessment-link responses keep their [slot]; R8 still holds them |
| `learning_center_links` | Wellness Learning Center link library by topic | yes | — | Only `config/links.yaml` links (not live yet) |
| `pricing` | Current pricing table and exact program-specific inclusion language (shipping, labs, supplies, cancellation / refund, peptide, sexual-wellness, NextGen, Biotin prices) | yes | — | No price claim is approvable; prices in replies are yellow |
| `lab_terms` | Testing availability and terms per program | yes | — | Lab-inclusion wording red; WellPeps lab questions HOLD |
| `gated_download_disclosure` | Validated guide links and the gated-download disclosure | yes | — | Guide claims not approvable; links not live |
| `program_descriptions` | Program and treatment-category descriptions; Hormone Optimization / Mental Wellness status; sexual-wellness catalog | — | — | No program-description claims |
| `adverse_event_contact` | Named adverse-event / clinical safety contact and reporting procedure | — | yes | Pages go to `escalation.clinical_owner` |
| `emergency_procedure` | Emergency escalation procedure and any required wording | — | yes | Guide wording; paged as adverse_event |
| `escalation_contacts` | Named privacy / compliance, legal, regulatory and media contacts | — | yes | Media / legal / regulatory share the legal owner |
| `after_hours` | After-hours procedure and response-time SLAs | — | yes | Escalations page around the clock |
| `failed_handoff_fallback` | Monitored queues, response expectations, fallback contact when a handoff fails | — | yes | Unsent pages retried and shown as not handed off |
| `authorization_matrix` | Which routine responses trained staff may post without case-by-case approval; official-account owners and thresholds | — | yes | Every reply needs a recorded human approval |
| `account_access` | Platform / account access and moderation permissions | — | yes | Pulse drafts only |
| `community_rules` | Each community's rules (participation, links, permission) | — | yes | Unknown: competitor posts HOLD, others yellow with no link |
| `listening_access` | Approved listening access per platform / community; rule verification and moderation permissions | — | yes | Public data only |
| `engagement_log` | Designated engagement logging system | — | yes | Pulse's audit log |
| `program_owner` | Owner of the Community Engagement Program; Live Reference owner / approver | — | yes | — |
| `protocol_approval` | Approve the protocol module; owner for versions; account roles and publication approvals (compliance, clinical safety, community operations leads) | — | yes | Protocol applied as written; labels / scores are proposed rules |
| `knowledge_base` | Connect the medication Knowledge Base (not supplied), Live Reference and resource catalog; freshness checks | — | yes | Facts only from `claims.yaml`; medication-availability questions HOLD |
| `data_retention` | Minimum-data storage, restricted incident access, retention, deletion, audit logs | — | yes | Minimal author data; no post text in the protocol record |
| `regression_run` | Run the example set and real-world tests (sensitive data removed); fix unsafe outputs; re-run on changes | — | yes | `tests/test_protocol_eval.py` covers the deterministic parts |
| `influencer_disclosures` | Platform-specific influencer / ambassador disclosure examples | — | — | Pulse never drafts as an influencer |
| `influencer_escalation` | Influencer / ambassador escalation contact and acknowledgement process | — | — | — |
| `fda_disclaimer` | Where the wellness-therapy FDA disclaimer is required and its exact language | — | — | No disclaimer claim |
| `training_pass_score` | Whether the 80% Knowledge Check pass score is adopted | — | — | — (people training) |
| `no_autonomous_publishing` | Autonomous publishing and unsolicited messaging disabled; reviewer approves every reply | — | — | **Provided by design** |

Also open in the source documents (no Pulse effect): [LR] Appendix A
CONFIRM items (peptide prices, shipping inclusion, cancellation wording,
support phone / hours / email, sexual-wellness catalog, NextGen and Biotin
prices, Hormone / Mental Wellness status, influencer examples, FDA
disclaimer) are covered by `pricing`, `program_descriptions`,
`support_channel`, `influencer_disclosures` and `fda_disclaimer` above.

## Document conflicts and resolutions

| Conflict | Resolution |
|---|---|
| 80/20 as a ratio to keep ([CG], [CT] Module 1) vs "planning principle, not a per-reply quota" ([CP] §1, [OM] §2.1) | Planning metric only |
| Disclosure "Disclosure: I work with WellPeps... not neutral" ([PL] Channel 3) vs "I work with WellPeps." ([AMG] §4, [CP] §7) | Default to the guide / protocol form; the [PL] form stays allowed |
| Adverse events in third-party threads: avoid ([OM] §5.3) vs boundary reply ([AMG] §10, [CP] Example 4) | Boundary reply for a clinical approver plus the page ([CP] controls; [AMG] §10 is not limited to WellPeps) |
| Individual medical questions in third-party threads: avoid ([OM] §5.3) vs CLINICAL CAUTION reply ([CP] Examples 3, 8, 11) | In the protocol's scope: boundary reply; otherwise no reply |
| [LR] §5 lists APPROVED prices vs [AMG] §12 FINALIZE and [CP] §6 ("do not train the tool on those figures") | No price claims; FINALIZE `pricing` |
| [LR] §6 "Approved Pricing Phrase" vs [CP] §6 (only program-specific inclusions) | Added as CLM-LR-06-PRICING-PHRASE but FINALIZE `pricing` |
| [CP] §6 lab sentence vs [LR] §6 Labs CONFIRM | Not activated (FINALIZE `lab_terms`) |
| Unknown community: HOLD ([CP] §2) vs draft and review ([OM] §28 pre-post checklist) | HOLD in the protocol's scope; elsewhere drafted yellow with no link, and a human reviews before posting |
| Posted records: privacy response ([AMG] §8) vs ESCALATE privacy ([CP] Example 16) | In the protocol's scope: privacy page plus the [AMG] §8 response; elsewhere the response only ([CT] 5B: "consider privacy escalation depending on circumstances") |
| WellPeps complaint: Template C ([AMG] §9) vs ESCALATE support ([CP] §3) | Both: Template C drafted; support handoff by hand until the queue exists |
| Media inquiry: routing line ([AMG] §23) vs "no substantive comment" ([LR] §14, [OM] §25) | The routing line is not substantive: drafted plus the page |
| Assessment links: patterns with a link ([AMG] §15, §19) vs "review first" ([PL]) vs R8 (red) | R8 retained (safeguard); the guide's assessment wording keeps its [slot] |
| [CG] prefers an identified employee; [OM] §3.2 and [CP] §7 cover official accounts | Default `official_account`; `identified_employee` is a config option |
| "WellPeps is one option you can evaluate" ([CP] §7) is not in [AMG] | Used as connecting wording in APPROPRIATE ALTERNATIVE drafts, not as a factual claim |
| [PL] "Happy to send you the link if useful" vs [CP] §8 (no outreach, no DM) | Offer only in the thread; Pulse never sends anything |
| Ops Manual docx vs `_V1` PDF | Same text (both read in full); cited as [OM] |

## Changing these rules

Change this file and the config together; keep the eval green
(`.venv/Scripts/python -m pytest -q tests/test_protocol_eval.py tests/test_engagement_*.py`).
A new WellPeps directive goes into the priority order above: record what it
supersedes in the tables.
