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
| [SM] | WellPeps_Engagement_Goals_and_Conversation_Selection_Module_V1 (`C:\dev\W\docs\community-engagement-agent\Knowledge Base\`; same file under `Community Engagement\`) and its source note "Community Engagement - Why and When to Engage" | 1.0, Oct 10 2026, internal implementation module |
| [GP] | WellPeps_Agent_Program_Gameplan (Knowledge Base copy, Oct 8 2026; text dated Aug 17 2026) | planning instrument |
| [DG] | Derek Goldberg (WellPeps) e-mail decisions on the Pulse review | Oct 10 2026 |
| [CAV] | "Community Agent Voice" (Derek Goldberg; e-mailed Oct 10, uploaded to the Knowledge library as Must follow Oct 20): voice principles, avoid list, guide-offer wording, names | Oct 10 2026; owner decisions Oct 20 2026 (R-26) |

Originals (except [CP]): `C:\dev\W\docs\community-engagement-agent\Community Engagement-20261007T043724Z-1-001.zip` -> `Community Engagement/<file>`.
Pulse never modifies anything under `C:\dev\W`.

## Priority order (binding, user instruction 2026-10-07)

"Prioritize these new guidelines over any of our previous rules."

1. **[CP]** controls competitor mentions, dissatisfaction, provider switching,
   opportunity scoring and its decision sequence. It says later approved
   legal, clinical, privacy and compliance directives control over it.
2. **The other Community Engagement documents** ([AMG], [LR], [CT], [OM],
   [CG], [PL]) override everything that came before.
   **[SM]** (Oct 10) governs routine conversation selection before drafting.
   It supplements [CP] and these rules and replaces none of the clinical,
   privacy, safety, access, disclosure, approval or platform rules ([SM] §1).
   [DG] (Oct 10): preserve the safety, privacy and clinical overrides first,
   then let the protocol decide ordinary engagement; internal activity
   targets are warnings; explicit community restrictions, moderator
   instructions and platform limits are firm posting blocks.
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
- Enter a conversation only with an opening, a specific approved
  contribution and permission to participate; a keyword, an available eBook
  or a large audience is not enough ([SM] §1 "the operating test").

## 2. Personas and disclosure

Pulse drafts as one of two personas (`engagement.persona` in
`config/engagement_guide.yaml`). There is deliberately no anonymous persona
([CG] Option 5; [OM] §3.5).

| Persona | When | Disclosure sentence (sentence 1 or 2) | Source |
|---|---|---|---|
| `official_account` (default) | WellPeps' own accounts | "I work with WellPeps." (drafter default "I work with WellPeps on patient education."); the review desk's "Posting as: WellPeps account" switch makes it "We're the WellPeps team." ([CAV], 2026-10-20) | [OM] §3.2; [CG] Option "WellPeps Official Account"; [CP] §7 ("Official accounts use clear company identity") |
| `identified_employee` | a named team member (`display_name`, no clinical titles) | "Hi, I'm <name> — I work with WellPeps." The drafter never writes a name: the rep who posts adds their own ([CAV]: a real name only when that person posts; no invented or AI persona) | [CG] Option 1; [AMG] §4; [PL] Channel 3 / 6 |

Approved disclosure forms ([AMG] §4; [CT] Module 2; [OM] §4.2):

- Employees / community team: "I work with WellPeps." (default, [CP] §7) or
  "I'm part of the WellPeps team." (used for complaints about WellPeps,
  [AMG] §9, [CP] Examples 17-18).
- Brand Ambassadors: "I'm a WellPeps Brand Ambassador."; partners /
  influencers: "I partner with WellPeps." Pulse never drafts as an
  influencer, so these forms are yellow in a Pulse draft (R47).
- The earlier "Full disclosure: I work with WellPeps, so I'm not neutral."
  ([PL] Channel 3) stays allowed; it is no longer the default.

**Placement (owner decision 2026-10-15).** "There is no requirement that it be
first. It just needs to be obvious." The source documents only require the
disclosure to be easy to notice and not buried ([OM] §4.3; [AMG] §4; [CP] §7
"clear in-message"); "first sentence" was a Pulse implementation choice. The
rule (R3, `disclosure.within_sentences: 2` in `config/compliance_rules.yaml`):

- The disclosure appears **within the first two sentences** of the reply and
  **before any other mention of WellPeps, its programs, or a wellpeps.com
  link**. It is never only at the end, only in a profile, or after a "more"
  fold.
- It may sit in its own sentence ("I work with WellPeps.") or be woven into one
  ("Ugh, that pricing maze is the worst. I'm part of the WellPeps team, so I'm
  biased, but here's what I'd check..."). The approved forms above stay the
  source of accepted phrases (case-insensitive substring).
- A reply may, and usually should, open by responding to the person: a short,
  specific reaction, not canned empathy ("We understand your concern").
- Red (R3): no approved form in sentences 1-2; or WellPeps, "our" program /
  guide / membership / team wording, or a registry link before the form.
  Third-person / customer-voice talk stays red (R2) when there is no
  disclosure. `boundary_only` replies keep the verbatim approved response and
  still open with the disclosure.

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
   the protocol's ESCALATE route (also when the public decision is HOLD),
   **only when the post is about WellPeps** (see "Review gates and incident
   routing" below).
4. **The situation** (first match in `engagement_guide.yaml`) decides the
   reply mode: `draft` (model draft in the template's shape), `boundary_only`
   (the approved response verbatim, no model call), `stop` (empty draft for a
   human) or `no_reply`.
5. **Conversation selection** ([SM]; section 4a below) records the review
   gates on the triage: a competitor's own community, the SKIP reasons, an
   unverified central fact. Only eligible posts are drafted.
6. **Drafting** checks the community registry and the stop rules first, then
   drafts. Every draft goes through the deterministic filter (with the
   community's rules) and, for model drafts, the adversarial reviewer.
7. **A human** approves in the review desk (with the clinical gate for adverse
   events and emergencies), copies the reply and posts it by hand. Approval
   recomputes the protocol, the selection and the community permission: a
   reply where posting is held cannot be approved ([SM] §5).

### Review gates and incident routing (user decision 2026-10-09)

From the first live Reddit pull (22 real posts): Pulse was reviewing and paging
on posts that could never benefit WellPeps. A post now reaches the review queue
only through three gates, in this order (safety and permission before benefit,
so benefit is never a reason to engage with someone vulnerable, [CP] §1):

1. **Relevant** (`knowledge.on_topic`, deterministic): the post names WellPeps,
   a competitor, a WellPeps product or drug (generic, brand, misspelling,
   specific slang), or a telehealth / treatment-category term. Otherwise it is
   dropped (`off_topic`) and the urgent-keyword override never fires on it.
   Competitor aliases that are everyday words or first names ("Henry", "Hers",
   "Roman", "Sequence"...) are not a signal on their own.
2. **Allowed**: the community rules and the situation playbook permit a reply,
   and a clinical or boundary reply (`boundary_only` / `stop`) goes only to a
   post about WellPeps (`gate:not_ours`). Pulse does not answer strangers'
   side-effect, dose, lab or treatment posts at all.
3. **Worth it** (`engagement.worth_it`): a reply could plausibly benefit
   WellPeps: the post is about WellPeps or a competitor, the protocol says
   APPROPRIATE ALTERNATIVE / EDUCATIONAL ONLY, the person is buying, the
   question is about process, pricing, choosing or comparing providers, or
   WellPeps itself, or it is a general medication or treatment education
   question ([SM] §3 "General medication or treatment education: RESPOND";
   this answers R-14's open question, 2026-10-20). Otherwise
   `gate:not_worth_it`: no draft. Trends and the language bank still count it.
   The [SM] SKIP reasons (section 4a) are recorded as
   `gate:not_worth_it:<reason>`.

**Incident routing** (pending WellPeps clinical / compliance confirmation; this
narrows [AMG] §10 and [CP] Example 4, which cover adverse events "in any
thread"):

| The post | Pulse |
|---|---|
| About WellPeps (names it, or the person appears to be a WellPeps patient) | Pages the owner as before; approved boundary reply where the guide has one |
| Serious health report (emergency, self-harm, an urgent keyword such as ER / hospitalized / gallbladder, a possible minor, possible serious harm) not about WellPeps | **Out of scope: Pulse does not weigh in.** No escalation, no Urgent item, no review deadline and no reply of any kind; the post stays `triaged`, blocked from drafting, and still counts for insights (R-36: Derek Goldberg via the owner, 2026-10-22: "we do not want to weigh in on anything that is outside of our scope of services, even if it is an urgent medical issue"). Replaces the 2026-10-09 safety watch. |
| Other companies' legal, privacy or billing issues; expected or cosmetic side effects not about WellPeps | No escalation, no reply; trends only |
| A medical question or health crisis about someone else ("my father on Ozempic drinks heavily") | **No reply of any kind** (not even a boundary reply). The clinical owner when WellPeps is named; otherwise out of scope, no escalation (R-36: Derek Goldberg via the owner, 2026-10-22). Triage tag `other_person_medical` or the safety screen's `other_person` flag (session safeguard 2026-10-20; [OM] §5.3 individual questions; [GP] §6.1-6.2 "Clinical triage ... Route to humans. Full stop.") |

Basis: Pre-LegitScript plan ("Respond only where WellPeps can add value";
"Don't respond merely because a keyword appeared"); user decision 2026-10-09;
`docs/REVISIONS-LOG.md` R-11 to R-14.

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
| Complaint about another company (`complaint_other`, `billing_other`) | No reply; never pile on a competitor. Ordered AFTER the protocol's answering situations (precedence fix 2026-10-07): a competitor complaint that asks for alternatives is answered as APPROPRIATE ALTERNATIVE / EDUCATIONAL ONLY | billing_fraud for fraud accusations | — | [AMG] §13; [OM] §5.3 |
| Poster shares medical details (`personal_medical_info`) | Privacy response (CLM-AMG-08-POSTED-DETAILS) | — (privacy page in protocol scope) | reviewer (blocked until FINALIZE care_routing) | [AMG] §8; [OM] §10.2; [CT] Module 5; [CP] Example 16 |
| Wants to send records by DM (`records_dm_request`) | CLM-AMG-08-DM-RECORDS | — | reviewer (FINALIZE care_routing) | [AMG] §8; [OM] §10.3 |
| Dose / labs / stop-change / which treatment / results / is it safe / do I qualify, asked of WellPeps | Template B boundary (CLM-AMG-06-*, -25-TEMPLATE-B, -07-*, -05-QUALIFY) | — | reviewer | [AMG] §5-7, §25 B; [OM] §9, §19; [CT] Module 3 |
| The same in a third-party thread (`individual_third_party`) | No reply (outside the protocol's scope) | — | — | [OM] §5.3, §27 |
| Misinformation about WellPeps | Template D model draft; the correction must be an approved claim | — | reviewer | [AMG] §13, §21, §25 D; [OM] §13.2 |
| General health misinformation (`misinformation`) | No reply; a human decides | — | — | [OM] §13.2; [CT] Module 7 |
| Has anyone used WellPeps / how it works / pricing / general education / question | Template A model draft from approved claims | — | reviewer | [AMG] §5, §12, §17-18, §25 A |
| Competitor mentions and provider switching | See section 5 | per protocol | per protocol | [CP] |
| Minors (safety screen) and failed screens | Never any reply, boundary or not | as before | — | retained safeguard R33 |

## 4a. Conversation selection ([SM] V1, Oct 10 2026)

`harvey/engagement.py` (`skip_reason`, `unverified_central_fact`,
`competitor_community`, `selection`, `posting_hold`), recorded at triage time
as review gates and recomputed at approval (`harvey/review.py`). Every rule
cites its source; [SM] §6 evaluation cases are tests
(`tests/test_selection_safeguards.py`).

| Rule | Decision | Enforced | Source |
|---|---|---|---|
| Incidents (safety, clinical, privacy, legal, WellPeps support) go to their workflows first and are never opportunity-scored; a skipped marketing reply never suppresses an incident | ROUTE | code (situations, protocol, `escalation_kind`; gates run after) | [SM] §1, §2 step 1; [DG] |
| A competitor's own community (r/IVIMHEALTH, r/Noom, r/yourmdonline, r/hims...: the community name matches a competitor or alias, `harvey.insights` community index) is listen-only intelligence, never a reply candidate, even if permission is recorded | SKIP (`gate:competitor_community`) and approval refused | code | session safeguard 2026-10-20; [SM] §2 step 2, §3 "Not the intended positioning"; [GP] §7.1 "mostly, stay out"; [CP] §1 |
| Business participation prohibited | SKIP; no link, no guide by name, no DM workaround | code (R44 red, protocol DO NOT ENGAGE, approval refused) | [SM] §2 step 2, §3; [CP] §4 step 1 |
| Unknown rules or required moderator permission missing | HOLD posting: drafting for internal review is allowed, approval is refused ("human posts / held until permission") until the rules or the moderator's permission are recorded (config/communities.yaml or the dashboard record); competitor / switching posts are not drafted at all ([CP] HOLD) | code (`posting_hold` in `approval_blockers`) | [SM] §2 step 2, §5 "Never treat a yellow unknown-rule warning as sufficient for approval"; [CP] §2, §10; [DG] |
| A satisfied-provider update with no question ("Happy with Ro. Month three is going great.") | SKIP `satisfied_provider` | code (triage tag `satisfied_provider` or subtype `competitor_praise`, with no question intent) | [SM] §2 step 3, §3, §6 |
| A question from another provider's patient ("I use Ro. What does titration mean?") | RESPOND educationally, no switching pitch | code (a question intent keeps it open) + drafter / reviewer | [SM] §3 "CONSIDER", §6 |
| Only the cheapest seller, a coupon or an undercut ("Who can beat this price? Coupons only.") | SKIP `price_only`, even with an alternatives request; safety still routes first | code (triage tag `price_only`) | [SM] §2 step 4, §3, §6 |
| Price clarity, affordability or what a price includes ("Money is tight. What extra costs should I check?") | RESPOND (not price-only) | triage prompt (`price_clarity` need) | [SM] §3 "Intent is more important than keywords", §6 |
| Already answered well / no incremental value | SKIP `no_added_value` | triage tag `already_answered` (thread context the model can see); stale threads and existing good answers are also a human posting check ([CP] §11) | [SM] §2 step 6, §3 |
| No approved answer or offering for the question | SKIP `knowledge_gap` (useful contribution 0, not about WellPeps); recorded in the gate for a content backlog | code | [SM] §2 step 5, §3, §5 |
| Something WellPeps offers comes up only in passing ("how do you cope with sleeping apart? ...maybe GLP-1 if all else fails"): the main topic isn't one WellPeps covers, nothing calls for what WellPeps offers, AND a reply would look like WellPeps injecting itself | SKIP `passing_mention` (all three must hold; never for a post about or asking WellPeps; safety routes first) | triage tags `topic_not_covered`, `no_natural_fit`, `reply_would_intrude` + code | owner decision 2026-10-21 (R-31); [SM] §1 "Do not treat every medication mention as a prospect", "A relevant keyword ... is insufficient"; Why and When to Engage |
| Only emotional, relationship or coping support is asked | SKIP `no_useful_role` | triage tag `emotional_support_only` + code | [OM] §5.3 "Highly emotional disputes where WellPeps has no useful role"; R-31 |
| A direct WellPeps question whose central fact is awaiting verification ("What does WellPeps charge?" while pricing is FINALIZE) | HOLD (`gate:hold_unverified_fact`), nothing drafted | code: the need's direct facts (`switching_protocol.needs.<need>.direct`) exist and none is publishable | [SM] §2 step 5, §3, §6; [CP] §4 step 5 |
| General medication / treatment education | RESPOND | code (`worth_it`) | [SM] §3, §6 |
| A price, pharmacy, process or provider question that mentions the person's own insurance, prescription or doctor | RESPOND: not an individual clinical question (the clinical tag means the person asks for a clinical judgment about themselves) | triage prompt (`individual_clinical_concern`) | [OM] §18.1; [CT] General-to-Individual Boundary; R-37 |
| A mixed post: a general question plus a minor personal element, no symptoms or harm | Today: the clinical boundary only. When Derek approves `answer_first_boundary`: answer the general question, then one approved boundary sentence (CLM-AMG-APPX-WHICH-MEDICATION) | code (`protocol.mixed_post`), OFF | [OM] §18.1; memo to Derek 2026-10-22; R-37 |
| A complaint about another company that also asks for alternatives | Answered under the protocol; triage's `reply_appropriate` guess never discards it | code (`answers_alternatives_request` in the draftable query) + triage prompt | [DG]; [SM] §5 "Do not let 'complaint about another company' suppress an otherwise eligible alternatives request"; R-2 |
| Duplicate participation | Rechecked at approval: a thread with the maximum approved WellPeps replies refuses another | code (`thread_blocker`) | [SM] §2 step 6, §5; [AMG] §22 |
| A medical question or crisis about someone else | No reply; ROUTE to the clinical owner only when it is about WellPeps, otherwise out of scope (no escalation) | code (triage tag + safety screen + `escalation_kind`) | session safeguard 2026-10-20; [GP] §6.2; R-36: Derek Goldberg via the owner, 2026-10-22 |
| Priority among RESPOND cases | high: direct WellPeps questions, clear treatment-education needs, alternatives with an approved fit; standard: broader education and provider selection; never desperation, severity or follower count; scores rank RESPOND only | code (`selection().priority`; protocol score) | [SM] §4; [CP] §9 |

The review desk shows the record ([SM] §5): `engagement.selection` gives
`engagement_decision` (RESPOND / SKIP / HOLD / ROUTE), `reason_code`,
`public_permission` (allowed / prohibited / unknown / permission_missing /
platform_rules_only), `posting_hold` and `priority`; the guide decision
(section 4) is the resource decision.

### Answer first and acknowledgements (2026-10-07)

- **Model drafts answer first** ([AMG] §1-§3; [CP] §1): a short reaction to the person, the
  disclosure (sentence 1 or 2, see section 2), then the sentence that
  answers the poster's actual question in their terms from approved claims (a program's
  status claim for "is X live yet?"; what varies and what to ask for "which providers include
  follow-up?"); boundary or disclaimer wording only where the question needs it, never as the
  opener; one useful next step; no generic checklist for a specific question. The reviewer
  marks a non-responsive / generic reply `needs_human` (`NON_RESPONSIVE`).
- **Program-status claims** (`program:` in `config/claims.yaml`): offered whenever a post is about
  that program (product, drug or words). Weight Management, Sexual Wellness and Peptides &
  Wellness Therapies are "available now" ([LR] §3 APPROVED, site live). Hair Restoration is
  "available now" (site live; Derek's sign-off relayed by the owner 2026-10-22; R21 no longer blocks
  it). Hormone Optimization is "coming soon" ([LR] §3 CONFIRM, PENDING). Mental Wellness was
  removed (no longer on the site, 2026-10-22).
- **Acknowledgement on approved replies** ([AMG] §1 "adapt ordinary conversational wording only
  as needed"): a `boundary_only` reply may carry ONE sentence between the disclosure and the
  verbatim approved response that restates the stated issue neutrally ("the shipping delay
  you're describing"). A model writes it (`drafter.acknowledge`, haiku); it must be one sentence
  of at most 20 words with no "your ...", customer / patient words, company voice, medical
  content, promise, link or number absent from the post, and must add no compliance-filter hit;
  otherwise the reply is the verbatim template. Never on the emergency line or on approved
  responses that carry their own disclosure. Clinical approval still applies.

### Smart Patient's Guide when a chapter materially helps (user instruction 2026-10-07, refined the same day)

> "We must refer to a relevant guide and point to how the guide can help."

Refined after Derek Goldberg's review (2026-10-07): Pulse **considers** the relevant guide for
every answering reply and **includes** it when a specific chapter materially helps with the
question. When no chapter answers the question the guide is omitted and the reason is recorded
(`omit`: "no guide chapter answers this question"), so there is a real "no relevant guide"
outcome. Basis: [PL] Channel 1 step 6 ("Not every comment gets a link") and Channel 3 ("Don't drop
links in every response", "Don't mention WellPeps in every response", "Don't use identical
answers").

- **Where**: considered in every drafted reply in an answering situation (`guide: required` in
  `config/engagement_guide.yaml` now means "consider; include when a chapter answers"): general
  questions and education, process / eligibility / availability questions, purchase intent,
  pricing, competitor comparison, clinic recommendations, "has anyone used WellPeps?", the
  protocol's APPROPRIATE ALTERNATIVE and EDUCATIONAL ONLY decisions (including a complaint about
  another company that asks for alternatives) and corrections of misinformation about WellPeps.
- **Which guide**: the single most relevant Smart Patient's Guide (`config/guides.yaml`, from the
  site's `ebooks.ts` / `guide-pages.ts`; `harvey/guides.py`): program / product / drug / post
  words; NAD+ posts get the NAD+ guide; the series index only when no single guide fits and one
  of its own chapters answers the question.
- **How**: answer first, then ONE sentence naming the guide and how it helps with THIS question,
  naming the chapter that answers it (e.g. GLP-1 guide -> "What's actually included in the price"
  for a follow-up / pricing question; Hair Restoration guide -> "What does a good result actually
  look like?" or "Oral vs. topical: what actually changes?"). Chapter titles are verbatim guide
  content, picked by Pulse's deterministic rule, never written by the model. Vary the wording; no
  stock sentence. The provider-determines line follows only when the post is about treatment
  choice, suitability, eligibility or results ([AMG] §3: state the boundary "when necessary").
  Help, not a pitch. Always say it is free, in Derek Goldberg's wording ([CAV], owner decision
  2026-10-20): "Our free guide has a section on [topic] that explains [specifics]. Here's a link to
  get a free copy if you'd like." Naming the email is optional; ungated access is never promised
  (R49). A "section" names a verbatim chapter title, checked like a chapter. The tracked registry link where the
  community allows links; otherwise the guide by name ("our free GLP-1 Weight Loss guide on the
  WellPeps website": no URL or domain, which would count as a link). Reddit up to ~110 words.
- **Never**: adverse events, emergencies, self-harm, legal / regulatory, privacy, media,
  complaint or billing escalations, individual clinical questions (all boundary or no-reply
  situations), a possible minor (safety screen, or "I'm 16" in the post), or a community that
  prohibits promotion / has not granted permission / has unverified rules (then no guide at all,
  by name or by link: a named guide is still a company resource offer; [CP] §2 "Unknown rules
  mean HOLD", §10 "Never silently default unknown permission to allowed"; [OM] §5.1).
- **Enforced**: the compliance filter (`GUIDE`): required but missing -> yellow "missing guide
  reference", one automatic redraft with that feedback, still missing -> `needs_human`; a guide
  in an excluded situation -> red; a guide link without the email-gate disclosure -> yellow; a
  "chapter" that is not a verbatim chapter title from `config/guides.yaml` -> yellow. The
  reviewer checks relevance (wrong program -> reject) and specificity (generic -> needs_human).
  The review desk shows "Guide: <Title> -> <chapter>".
- **Approval**: the user's instruction counts as WellPeps' approval of the gated-download
  disclosure: FINALIZE `gated_download_disclosure` resolved 2026-10-07 (value recorded in
  `engagement_guide.yaml`), and the guide claims (`CLM-EDU-GUIDE*`) carry `approved_by: "WellPeps
  via user instruction (2026-10-07) ..."`. The guide links stay `live: false` in the real
  `config/links.yaml` until the site pages deploy, so a reply carrying one still cannot be
  approved (link not live).

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
disclosure, resource (the guide link where the community allows links and promotion and a
chapter answers the question; otherwise "none". The review desk shows the guide decision: link,
name only, omitted with its reason, or forbidden with its reason),
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
| `unknown` (or not registered) | Competitor / switching posts: HOLD, nothing drafted ([CP] §2). Everything else: drafted for internal review, yellow "community rules unverified", no link (a link is red) and no Smart Patient's Guide, even by name (red). **Approval is refused** ("human posts / held until permission") until the rules or a moderator's permission are recorded ([SM] §2 step 2, §5; [CP] §2, §10; [OM] §5.1; [DG]; 2026-10-20, R-20). |
| `with_permission`, permission not obtained | Yellow; no link and no promotion (red); an alternatives request is downgraded to EDUCATIONAL ONLY; **approval refused** until the permission is recorded. |
| a competitor's own community (named for a competitor or alias) | Listen-only: nothing drafted (`gate:competitor_community`) and approval refused whatever is recorded; mentions feed competitor insights ([SM] §3; [GP] §7.1; session safeguard 2026-10-20). |
| `allowed` | Normal; `links_allowed: false` still makes any link red. |
| no community (owned channels, review sites, Instagram / TikTok comments) | Platform rules only. |

**Advisory mode (owner decision 2026-10-21, R-30; needs Derek Goldberg's
sign-off).** The table above is the `enforced` mode. With `posting_holds:
advisory` (the shipped setting) the rules no longer stop drafting or
approval: Pulse drafts in every community above, treating `prohibited` and
missing permission like `unknown` (educational, no link, no promotion, no
guide); the protocol's community HOLD / DO NOT ENGAGE and the competitor
community gate are not applied. The queue card shows a "Community rules"
warning and approval needs the rep's acknowledgement, kept in the approval's
audit record. A rep may post Pulse's reply or their own and records what was
posted (text, who, when, link) as a `posted` audit event, checked by the
compliance filter. Safety, clinical and privacy routing, clinical approval,
the filter, approved claims and one reply per thread are unchanged.

Firm posting blocks vs warnings ([DG] 2026-10-10): explicit community
restrictions, a moderator's instruction (record it as `prohibited` or
`links_allowed: false`) and platform limits (character, link and hashtag
limits in the filter) block approval; internal activity targets (the 80/20
share, the per-community volume target) are warnings only.

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
  links ([AMG] §14; [CP] §8). In answering situations the relevant Smart
  Patient's Guide link is included when a guide chapter answers the question
  (user instruction 2026-10-07, refined; section 4).
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

Activity target (2026-10-20, [PL] Channel 3 "Don't overwhelm a single
subreddit/community"; [DG] "internal activity targets are warnings"): the
review desk warns (`engagement.activity_warnings`, shown under
`engagement.warnings`) when WellPeps already has 3 approved replies in the
community in the last 7 days, or the community's 80/20 share is over. Pulse
proposes the number; it never blocks.

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
  `config/communities.yaml` or the dashboard's community record when it is a
  community rule; it then blocks approval, [DG]).
- At approval, a thread that already has the maximum approved WellPeps
  replies refuses another one ([SM] §5 "Recheck ... duplicate participation").

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
| Any of the above **not about WellPeps** | none | none: out of scope, Pulse does not weigh in (R-36: Derek Goldberg via the owner, 2026-10-22; replaces the 2026-10-09 safety watch) |

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

Session safeguards (2026-10-20, R-20): never suggest a DM, private message,
call, text or e-mail ("Give us a call", "email me", "reach out privately",
"hop on a quick call"; R49, red; [CP] §1, §8, §11; [GP] §7.1); never ask anyone
to post an order number, account or health details in public ("post your
order number", "share your dose"; R32, red; [AMG] §8); never claim access to
the person's records or confirm they are a customer ("we'll look into your
account", "your WellPeps order", "your order has shipped"; R31, red; [OM] §8;
[CT] Module 5; [GP] §3.2, §12); never invent a clinical team or authority
("I'm on the WellPeps clinical team"; R2, red; [SM] §4); never promise
ungated access to a guide ("no email required", "instant download"; R49, red;
[SM] §4; [CP] §8). Neutral referral wording ("contact your prescribing
provider", "ask your provider") is allowed. Routing to WellPeps' approved
support channel is the approved wording, not a private invitation.

Length: "aim for" 40-90 words ([CP] §7) is a target for the drafter and the
reviewer, never a filter rule; only the platform's character limit is.

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
| Answer first; program-status claims; reviewer NON_RESPONSIVE -> needs_human | `prompts/draft.md` ("Answer first"), `drafter.ANSWER_FIRST`, `claims.yaml` `program:` (`drafter.programs_for`), `prompts/review.md` | `tests/test_answer_first.py` |
| One acknowledgement sentence before a verbatim approved response | `harvey/agents/acknowledger.py`, `prompts/acknowledge.md`, `drafting._acknowledged`, `engagement.acknowledged_reply` | `tests/test_acknowledgements.py` |
| No autonomous posting | no publish / message path in Pulse | `tests/test_protocol_eval.py` |
| Conversation selection ([SM]): SKIP reasons, central-fact HOLD, competitor communities, general education, selection record, posting hold at approval, duplicate participation, activity warnings | `harvey/engagement.py`, `harvey/review.py` (`_selection_blockers`), `harvey/agents/triager.py`, `prompts/triage.md` | `tests/test_selection_safeguards.py`, `tests/test_review_gates.py`, `tests/test_engagement_state.py` |
| Someone else's medical question: no reply (clinical owner only when about WellPeps) | `prompts/triage.md`, `prompts/safety_screen.md`, `harvey/agents/safety_screen.py`, `harvey/agents/triager.py`, `harvey/escalation.py` | `tests/test_selection_safeguards.py` |
| Private channels, public details, R31 narrowed, invented clinical authority, ungated guide access, length as a target | `config/compliance_rules.yaml`, `prompts/draft.md`, `prompts/reply_rules.md`, `prompts/review.md` | `tests/test_safeguard_rules.py`, `tests/test_compliance.py` |

## Superseded earlier rules

| Earlier rule | Now | Overriding source |
|---|---|---|
| R38: no medication or drug-brand names in replies (`forbid_medication_names_in_replies: true`) | Names allowed for general treatment-category education (yellow for review); never an individual recommendation or dose; compounded-equivalence stays red (retained R15/R16) | [CT] Module 3 (general-to-individual boundary: "the difference between semaglutide and tirzepatide" can be answered generally); [CG] Official Account; [OM] §9.1; [PL] "Review first: medication names" |
| Conversion playbook step 3: ONE WellPeps fact in every purchase-intent / question reply | WellPeps facts only when the person asks about WellPeps or explicitly asks for alternatives in a community that allows it; otherwise the disclosure only | [CP] §1, §7 (brand modes); [OM] §12.2 ("If a WellPeps mention is not necessary... consider leaving it out"); [AMG] §2 |
| Conversion playbook: the program's guide claim pushed right after the disclosure, "one CTA (the guide)", guide link on purchase intent | A resource is optional, only when it directly answers and the community allows links, never in safety / clinical replies, email gate stated; the guide claim is offered after the fixed claims | [CP] §8; [AMG] §14; [OM] §11.1; [PL] Channel 1 step 6 ("Not every comment gets a link") |
| [CP] §8 / the row above: "a resource is optional", protocol record `resource` "none" unless a live link fits, EDUCATIONAL ONLY "no guide offer" | **User-directed override (2026-10-07), refined the same day after Derek Goldberg's review**: every answering reply (incl. APPROPRIATE ALTERNATIVE and EDUCATIONAL ONLY) considers the most relevant Smart Patient's Guide and includes it, naming a chapter, when a chapter materially helps; otherwise it is omitted with the reason recorded; the protocol record's `resource` is that guide link where the community allows links and promotion. Safety, clinical, legal, privacy, media, complaint / billing, minors and no-promotion communities stay excluded | Binding user instruction 2026-10-07 ("We must refer to a relevant guide and point to how the guide can help"); section 4 |
| Default disclosure "Disclosure: I work with WellPeps, so I am not neutral." (CLM-R3-DISCLOSURE) | Default "I work with WellPeps." (old form still accepted) | [AMG] §4; [CP] §7 |
| CLM-PRICE-FOLLOWUP "...included in the one monthly price" as a fixed playbook claim | Not offered first; FINALIZE `pricing`; the Live Reference's approved ongoing-support fact is used instead | [CP] §6; [LR] §6 ("One Simple Price. Everything Included." needs verification); [AMG] §12 FINALIZE |
| CLM-PRICE-ALLIN (includes "standard shipping") | FINALIZE `pricing` | [LR] §6-7 (shipping CONFIRM); [CP] §6 |
| `config/reply_examples.yaml` (four playbook examples) | Rewritten: primary disclosure, no unsolicited WellPeps fact, no "one monthly price", email gate stated, 40-90 words | [CP] §7-8; [AMG] §4 |
| Disclosure must be the FIRST sentence of every reply (Pulse choice, 2026-10-06; `first_sentence`) | Within the first two sentences, before any other WellPeps mention; replies open by responding to the person (2026-10-15) | Owner decision 2026-10-15; [OM] §4.3; [AMG] §4; [CP] §7 ("easy to notice", not buried) |
| Drafter shape: disclosure, answer, provider line on every reply | Reaction, disclosure, answer, one next step; familiar voice (contractions, varied openings, no stock closing); provider line only for treatment choice, suitability, eligibility, results or safety, worked into the answer | Owner decision 2026-10-15; [AMG] §3 ("when necessary") |
| 80/20 as a per-reply gate (partial work: R45 yellow; no link / education only when a community is over 20%) | Planning metric only (review desk chip, Analytics card) | [CP] §1; [OM] §2.1 |
| Severe categories are never drafted (`NO_DRAFT_CATEGORIES`, before 2026-10-06); third-party adverse events get no reply (partial work) | Adverse events in any thread get the guide's boundary reply for a clinical approver, plus the page; billing / complaints about WellPeps get Template C | [AMG] §9-10, §19, §25; [OM] §19, §25; [CP] §5, Examples 4, 17-18 |
| Unknown community rules: draft yellow, no link (partial work, for every post) | For competitor / switching posts: HOLD, nothing drafted (other posts unchanged) | [CP] §2, §4 step 1, Example 14 |
| Third-party individual medical questions: no reply ([OM] §5.3) | In the protocol's scope: CLINICAL CAUTION with the approved boundary | [CP] §4 step 3, Examples 3, 8, 11 |
| R31 red for "your care team" | Generic "your care team" allowed; "your WellPeps care team" stays red | [CP] Example 1 ("ask how you can contact your care team") |
| R31 yellow for "DM us / message me" | Red (R49): no DM migration or unsolicited messages | [CP] §1, §8, §11, Example 15; [OM] §12.1; [AMG] §9 |
| R31 yellow for "reach out privately"; calls, texts and e-mails unchecked | Red (R49): any call, text, e-mail or private-channel suggestion (2026-10-20) | session safeguard; [CP] §1, §8, §11; [GP] §7.1 |
| R31 red for the bare phrases "your order", "your prescription", "your provider", "your refill"... | Only record access, order / account status claims and WellPeps-relationship wording are red (2026-10-20) | Derek Goldberg's review (2026-10-07) and Pulse's response; [OM] §8; [CT] Module 5 |
| R-14: a general education question not about WellPeps or a competitor is "not worth it" | RESPOND when it is a relevant general medication or treatment education question with an opening; the [SM] SKIP reasons still apply | [SM] §1, §3, §6 (Oct 10) |
| Unknown or not-yet-permitted community: yellow, approval possible | Approval refused until rules or permission are recorded (drafting still allowed) | [SM] §2 step 2, §5; [CP] §2, §10; [DG] |
| A model draft needs triage's `reply_appropriate` | Also drafted when the protocol answers an explicit alternatives request (APPROPRIATE ALTERNATIVE or EDUCATIONAL ONLY with `alternatives_requested`) | [DG]; [SM] §5 |
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
| R8: no assessment, intake or checkout links before LegitScript (the "Start your assessment" phrase ban was dropped by the owner 2026-10-22) | platform / LegitScript circumvention | R8 | [PL] lists such links as "Review first" (Pulse is stricter: red); [AMG] §15, §19 give assessment-link patterns with a FINALIZE slot |
| R31 (narrowed 2026-10-20): never claim access to the person's records or account ("we'll look into your account", "your order has shipped") and never confirm or imply they are a WellPeps customer ("as our patient", "your WellPeps order") | HIPAA / patient-status disclosure | R31 (`patient_confirmation`) | [OM] §8, [CT] Module 5, [GP] §3.2, §12 forbid confirming patient status. The bare phrases "your order / prescription / provider" were Pulse's over-broad inference and are no longer red (agreed with Derek Goldberg's reviewer; "contact your prescribing provider" is approved wording) |
| R33 + safety screen: possible minors never get a reply (boundary or not) | weight-loss marketing to minors | R33, `safety_screen` | not addressed |
| Self-harm: no reply; the clinical owner is paged only when the post is about WellPeps (R-36: Derek Goldberg via the owner, 2026-10-22) | crisis handling | `self_harm` situation | [OM] §15 (credible safety threats) only in general terms |
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
| `gated_download_disclosure` | Validated guide links and the gated-download disclosure | yes | — | **Resolved 2026-10-07 by user instruction** (WellPeps' approval: "free, it asks for your email"); links still `live: false` until the pages deploy, which blocks approval |
| `program_descriptions` | Program and treatment-category descriptions; Hormone Optimization / Mental Wellness status; sexual-wellness catalog | — | — | Program-status claims only: Weight / Sexual Wellness / Peptides from [LR] §3 APPROVED; Hair (site Coming Soon vs [LR] §3), Hormone and Mental Wellness ([LR] CONFIRM) stay PENDING |
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

## PENDING: needs Derek Goldberg's sign-off (not implemented)

Raised in the 2026-10-20 review session; Pulse keeps its current behaviour
until WellPeps decides.

| Item | Pulse today | Needed |
|---|---|---|
| Company voice "we" on owned channels and review sites ("We're the WellPeps team") | Not on the approved disclosure list; drafts use "I work with WellPeps." / "I'm part of the WellPeps team." | Approved company-voice wording for official accounts ([OM] §3.2; [CP] §7; [AMG] §4) |
| Paraphrasing approved claims | Facts only in an approved claim's words; the acknowledgement sentence is the only adaptation | Whether, and how far, reviewers / the drafter may paraphrase approved wording ([AMG] §1) |
| Review-time statements ("a provider usually reviews within ...") | No approved response-time fact; R49 red | Approved review-time wording, if any ([CP] §6) |
| The mandatory "it asks for your email" sentence | Kept (owner instruction 2026-10-07; natural wording); ungated-access promises are red | [SM] §4 says the sentence is not required in every comment when the landing page explains the email requirement: confirm before relaxing |

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
| General education "not worth it" (R-14, Oct 9) vs "General medication or treatment education: RESPOND" ([SM] §3, Oct 10) | [SM] (newer WellPeps guidance): RESPOND; the SKIP reasons still apply |
| Unknown community: HOLD ([CP] §2) vs draft and review ([OM] §28) vs "internal drafting may be allowed, posting approval blocked" ([SM] §2) | Competitor / switching posts HOLD (nothing drafted); other posts drafted for review and approval refused until permission is recorded |
| Mandatory email-gate sentence (owner 2026-10-07) vs not required in every comment ([SM] §4) | Kept pending Derek's confirmation (PENDING table) |

## Changing these rules

Change this file and the config together; keep the eval green
(`.venv/Scripts/python -m pytest -q tests/test_protocol_eval.py tests/test_engagement_*.py`).
A new WellPeps directive goes into the priority order above: record what it
supersedes in the tables.
