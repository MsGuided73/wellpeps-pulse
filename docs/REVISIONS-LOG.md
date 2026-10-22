# WellPeps Pulse: Revisions Log

A running, plain-language list of every change to how Pulse behaves, for team
review. Newest first. Each entry says what changed, why, the source behind it,
and whether it still needs a WellPeps decision or sign-off.

**Status key:** **Live** = built and tested. **In progress** = being built now.
**Needs sign-off** = built (or agreed) but waiting on a named WellPeps owner.

The detailed rules live in `docs/RULES-OF-ENGAGEMENT.md`; this log is the
summary for discussion.

---

## 2026-10-22: Every library document as Markdown; harness picks sharpened

### R-38. Markdown by default and a sharper context harness (Live; the Markdown versions need approval)
- **Markdown by default (owner):** every Word or PDF upload to the Knowledge library
  is stored as Markdown (`harvey/doc_markdown.py`): Word headings, lists, tables and
  callout boxes; for PDFs the type scale decides headings, repeated page headers and
  footers are dropped, bullets become list items, and pypdf's own spacing decides how
  pieces of a line join. Wording is unchanged; plain text is the fallback.
- **The eleven existing documents** (Gameplan, Compliance Training, Live Reference,
  Approved Messaging Guide, Community Agent Voice, Community Groups, Offering Links,
  Why and When to Engage, Selection Module, Operations Manual, Competitor Protocol)
  were converted and checked word for word against their library versions; they wait
  for approval as library #33-43. The five guides were converted earlier (#28-32).
- **Harness:** the guide chapter Pulse chose for the post now comes first after the
  voice guide; training examples, evaluation tables, deployment / acceptance /
  document-control pages, "How to use this training" and sections about another
  platform (for example LinkedIn rules for a Reddit post) are never picked; the
  drafter and reviewer get 12,000 characters each (was 8,000).
- **Measured** on today's posts (database copy, Markdown versions approved there):
  the picks are now on topic (Protocol §3 and §8, the Training's general-vs-individual
  boundary), but most price replies are still rejected or held, because the drafter
  now tries to answer the fee and pharmacy questions in its own words and no approved
  wording covers them yet (CLM-CHECKLIST-COST-COMPARE and CLM-PRICE-ALLIN wait for
  Derek). Memo: "How Pulse's context harness works" (Claude Docs).

## 2026-10-22: Price and pharmacy questions get answers, not the clinical boundary

### R-37. Fixes from the memo to Derek (items 1 and 4 Live; item 2 built, OFF; item 3 pending Derek)
- **Why:** in the 2026-10-22 fresh run, three ordinary questions (what to check when
  picking an online provider, which pharmacies compound, whether your own doctor's
  prescription gets compounded pricing) got only the clinical boundary reply
  (Template B), because triage tagged any mention of the person's insurance or
  prescription as an "individual clinical concern". Operations Manual §18.1:
  "Answer the general question first... State the boundary if individualized
  medical judgment is involved."
- **1. Narrower clinical tag (Live):** `individual_clinical_concern` now means the
  person asks for a clinical judgment about themselves (a symptom, a dose,
  starting/stopping/switching, which medication suits them, their labs, medical
  eligibility). Mentioning their insurance, prescription or doctor while asking
  about price, pharmacies, process or providers does not count (prompts/triage.md).
- **2. Answer first, then the boundary (built, OFF until Derek approves):**
  `switching_protocol.answer_first_boundary` in config/engagement_guide.yaml. When
  on, a mixed post (a general question, no symptoms or harm, not a dose / labs /
  eligibility / medication question) is answered, plus one approved boundary
  sentence (CLM-AMG-APPX-WHICH-MEDICATION). Mainly clinical posts keep the
  boundary-only reply.
- **3. Cost-comparison checklist (pending Derek):** CLM-CHECKLIST-COST-COMPARE,
  brand-neutral; not used until approved. The pricing statement (CLM-PRICE-ALLIN)
  was updated earlier the same day (c78017b).
- **4. Regression set (Live):** tests/fixtures/regression_posts.yaml (16 post
  shapes with their expected handling) and `scripts/regression_check.py`, which
  re-runs them through the real triage on a temporary database. Run it before and
  after any change to Pulse's instructions, models or selection rules.

## 2026-10-22: Out of scope means Pulse does not weigh in

### R-36. No escalation or reply for health reports that are not about WellPeps (Live; Derek's decision)
- **Source:** Derek Goldberg, relayed by the owner (2026-10-22): "we do not want
  to weigh in on anything that is outside of our scope of services, even if it is
  an urgent medical issue." The owner: an urgent item a clinician must view and
  acknowledge only when the post relates to WellPeps.
- **Before:** a serious health report not about WellPeps (an urgent keyword such
  as "seizure" or "ER", self-harm, an emergency, a medical crisis about someone
  else) went on the Urgent list as a "safety watch" item with a 24-hour review and
  an Ack button. Example: "Is there a paper white phone that can do Telehealth?"
  ("...my vision and seizure disorders").
- **After:** those posts get no escalation, no Urgent item, no deadline and no
  reply of any kind. They stay triaged, blocked from drafting by the safety
  markers and review gates, and still count for insights. Posts about WellPeps
  are unchanged: the clinical owner is paged and acknowledges.
- **Note:** Pulse can't tell whether someone is a WellPeps patient, and never
  tries; "about WellPeps" means the post names WellPeps or is about its service.
  This supersedes the Competitor Protocol's internal safety review for posts
  outside WellPeps' scope.

## 2026-10-22: Guide pages live; the Communities page

### R-35. A Communities page: every community, its clearance checklist and what is blocked (Live)
- **Why:** none of the 34 starting communities had verified rules, so no reply
  anywhere could name a Smart Patient's Guide (Competitor Protocol §2: unknown
  rules mean no company resource). The owner asked for one place to see every
  community Pulse collects from and clear them.
- **After:** a fourth main tab, Communities (`harvey/web/communities.js`,
  `GET /api/communities`). Every community Pulse knows of, including ones seen
  only in collected posts, with a link to the group, posts collected, a clearance
  checklist (rules read, brand replies allowed, links allowed, moderator contact)
  and, in plain words, what stays blocked until it is cleared (no guide, no links,
  rep acknowledgement, a competitor's own community). Admins record a community's
  rules from its card ("Update rules"); the record wins over the starting list, as
  before. Operator HQ keeps a one-line summary with a link.
- **Bug fixed:** recording rules with a moderator contact for a community that was
  not on the starting list crashed.

### R-34. The Smart Patient's Guide pages are live (Live)
- All six guide URLs return their own guide page on wellpeps.com (checked
  2026-10-22 with `pulse links check`), so `config/links.yaml` marks them
  `live: true`: a reply that carries a guide link is no longer held at approval
  for "link not live yet". Where a community's rules are unverified, the guide is
  still left out (R-35 is how to clear them).
- Shipping is included in the one monthly price (owner, 2026-10-22); the pending
  pricing claim CLM-PRICE-ALLIN is redrafted from the owner's facts for Derek.

## 2026-10-22: Product list matches wellpeps.com; no membership fee

### R-33. Two pre-certification phrase rules dropped (Live; owner decision)
- **Before:** the filter blocked "Start your (free) assessment" (R8) and any reply
  saying WellPeps offers NAD+, sermorelin, glutathione, MIC + B12, Lipo-C or methylene
  blue (R18). Both dated from before the site's programs went live.
- **After** (owner, 2026-10-22): both phrase rules are gone. Unchanged: R8's link
  rules (no checkout, intake or assessment links; only registry links backed by a
  claim), R13 (anti-aging / longevity / detox claims), R19 (research peptides), and
  facts only from approved claims.

### R-32. Catalog and pricing model brought up to date; Hair Restoration is available (Live)
- **Source:** the owner's inventory of the live wellpeps.com category pages
  (2026-10-22) and the owner's pricing note (2026-10-21): no membership fee; each
  treatment has one monthly price covering the provider visit, ongoing support
  through the patient portal and the medication if prescribed. Lab tests are extra:
  optional for most treatments, required only for hormone therapy, and low cost.
  The owner prefers "one monthly price" to "all-inclusive" (labs are extra).
- **After:** `config/products.yaml` lists the 18 live offerings with today's prices
  (reference only: Pulse never quotes them; prices in replies come only from
  approved claims). The membership block is replaced by `pricing:` (one monthly price).
  Hair Restoration is live as five named products (Oral Finasteride, Oral
  Minoxidil, Topical Minoxidil, Topical Minoxidil + Finasteride, Topical Finasteride
  + Minoxidil + Tretinoin) instead of four older category cards. NAD+ comes as an
  injection or a nasal spray. Mental Wellness, no longer on the site, is removed
  with its pending status claim.
- **Hair Restoration is available:** Derek signed off (relayed by the owner,
  2026-10-22). CLM-STATUS-HAIR now reads "WellPeps' Hair Restoration program is
  available now." and is approved; filter rule R21 no longer blocks saying so (its
  regrowth-promise pattern stays); the hair reply example answers "Yes".
- **Still open:** Rule R49 keeps
  blocking "all inclusive" / "everything included", which the owner agrees with.
  Shipping is included (owner, 2026-10-22). The pending pricing claim
  CLM-PRICE-ALLIN carries the owner's chosen wording and waits for Derek: "One monthly price covers the provider visit, ongoing support through the patient portal, the medication if it’s prescribed, and shipping. The price doesn’t change as your dose goes up, and there’s no membership fee. The one thing that isn’t included is lab testing. Most treatments don’t need labs to get started, but you can order them at a reduced rate if you want them. Hormone replacement therapy is the only one that requires labs first."
- **Unchanged:** listening words and compliance patterns that mention "membership"
  (people still discuss other companies' memberships).

## 2026-10-21: Passing mentions and posts with no useful role are skipped

### R-31. A post that only mentions what WellPeps offers in passing gets no reply (Live; owner decision)
- **Why:** r/snoring "Psychology of separate beds" reached the review desk. A spouse
  asked how others cope with sleeping apart; GLP-1 came up once, as a last resort in
  their ENT plan. Triage called it general education, and the selection rules said
  RESPOND. Only the drafter's refusal kept a reply from being written.
- **Source:** WellPeps' own documents already say this: "Do not treat every
  medication mention as a prospect" and "A relevant keyword ... is insufficient on
  its own" (Selection Module); "Relevance to a medication alone is too broad" (Why
  and When to Engage); avoid "Highly emotional disputes where WellPeps has no useful
  role" ([OM] §5.3). The owner set the test (2026-10-21).
- **After:** triage answers three questions separately. The post is skipped as
  `passing_mention` only when all three are true:
  1. its main question or topic isn't about anything WellPeps covers;
  2. it doesn't naturally call for anything WellPeps offers;
  3. a reply would seem out of touch, as if WellPeps were injecting itself for
     commercial reasons.
  A post that asks only for emotional, relationship or coping support is skipped as
  `no_useful_role`. Neither applies to a post about WellPeps or one that asks
  WellPeps something, and safety, clinical and privacy routing still come first.
  Skipped posts are still collected and counted for insights; the card's "Why this
  post" line says why. A personal post that also asks a real medication question
  ("does GLP-1 help snoring?") is still answered.
- **Checked:** today's triage run with and without the new questions on every
  relevant live post: the separate-beds post and a UGC casting call are now skipped;
  no question WellPeps should answer was skipped.
- **Bug fixed alongside:** the on-topic check read only a post's body, not its
  title, so a post naming the drug only in the title ("Trying to find the cheapest
  legit compounded tirzepatide...") was dropped as off-topic. It now reads both.

## 2026-10-21: Community rules become advisory; record what was posted

### R-30. Pulse drafts in every community; the rep decides and records what they posted (Needs sign-off)
- **Before:** where a community's rules were unknown, prohibited brand replies
  or still needed a moderator's permission, approval was refused (a firm
  block); prohibited and competitor-owned communities got no draft at all, and
  competitor / switching posts there were held without a draft. "Mark as
  posted" recorded only that the approved text went out.
- **After** (`posting_holds: advisory` in config/communities.yaml; set it to
  `enforced` to go back):
  - Listening and drafting go ahead in every community. Where the rules aren't
    cleared, drafts stay educational: no link and no promotion (prohibited
    communities are drafted like unverified ones).
  - The queue card shows a "Community rules" warning in plain words (unverified
    rules, a prohibition, missing permission, or a competitor's own community).
    Approve and copy stays off until the rep ticks "I've read this. Posting
    here is my decision." The approval's audit record keeps the warning.
  - Every card has "Record what was posted" ("I posted my own reply" while in
    review): the text actually sent, who posted it, when, and an optional link.
    It goes in the audit log (a second record is a correction, never an
    overwrite), marks the post posted, and runs the compliance filter on what
    went out (the "no claim ID" rule is not counted for a rep's own words); a
    red result is shown on the card for compliance.
  - Unchanged: safety, clinical and privacy routing; clinical approval for
    adverse-event and emergency replies; the compliance filter and approved
    claims for Pulse's own drafts; one reply per thread; no posting by Pulse.
  - Posts held earlier only for community rules are re-checked when the worker
    starts (`pulse reapply-holds` does it by hand). A post skipped earlier in a
    prohibited community is not re-drafted (none in the live data).
- **Why:** owner, 2026-10-21: "I would like the listening and collection of
  relevant posts to proceed no matter the community rules", with the rep
  choosing to post Pulse's reply or their own, and a record of what was sent,
  by whom and when.
- **Overrides:** Derek Goldberg's 2026-10-10 decision that explicit community
  restrictions are firm posting blocks; Compliance Training Module 9 ("WellPeps
  approval does not override a ... community rule"); the Selection Module's
  HOLD for unknown rules; the Competitor Protocol's HOLD / DO NOT ENGAGE for
  community access, and the listen-only rule for competitors' own communities.
- **Needs sign-off:** Derek Goldberg.

## 2026-10-21: Community rules instead of moderator permission

### R-29. A rep's reading of a community's posted rules clears it for posting (Needs sign-off)
- **Before:** the Operator HQ form was called "Record permission" and asked
  what the moderators said, so clearing a community looked like it needed a
  moderator's OK.
- **After:** the form is "Record community rules". A WellPeps rep reads each
  community's posted rules and records them; "Yes, brand replies are allowed"
  clears the community for approval with no moderator contact. Where the
  rules ban promotion or say to ask the moderators first, the rep chooses
  "No" until a moderator says yes. The note records which rule they relied
  on. No change to Pulse's logic: recorded rules already lifted the posting
  hold (Selection Module §2 step 2: "until the rules or the moderator's
  permission are recorded"); explicit restrictions stay firm blocks ([DG]).
- **Why:** owner decision 2026-10-21, to launch Reddit without waiting on
  moderator replies for all 34 communities.
- **Needs sign-off:** Derek Goldberg (owner confirming with him).

## 2026-10-21: Friendlier drafts, harness on

### R-28. Warmer drafting voice; the context harness is on (Live)
- **Before:** after R-26 the drafts read stiffer: the voice section was mostly
  prohibitions, approved claims were pasted in as written, the drafter saw the
  opening of the Compliance Training, and approving the 37,000-character
  Competitor Protocol (a newer must-follow document) would have pushed the
  voice guide out of the drafter's 6,000 characters. The harness pinned any
  must-follow section that said "avoid" or "style", and picked training-quiz
  sections ("10. Correct answer: C").
- **After:**
  - Voice section rewritten as a positive picture first (a kind,
    well-informed friend who works on patient education: react to what they
    are trying to figure out, answer their question, talk to them, end on
    something useful); Derek's avoid list kept, shortened, at the end.
  - Approved facts in the drafter's own sentences: talk to the person, split
    long claims, contractions; every fact and qualifier kept; only the one to
    three claims the person needs.
  - Never repeat a poster's personal health details back to them (Approved
    Messaging Guide §8): the 2026-10-21 comparison caught "since you mentioned
    compounded semaglutide" (reviewer reject).
  - The library block always puts must-follow "Voice and style" documents
    first, harness off or on.
  - Harness: only "Voice and style" documents are pinned as voice (all their
    sections); `exclude_headings` in config/harness.yaml skips quizzes and
    answer keys.
  - Style examples rewritten warmer (same claims; all pass the filter).
  - **`harness.enabled: true`** in harvey.yaml (the code default stays off).
- **Why:** owner, 2026-10-21: "The answers have lost the finesse they
  previously exhibited", then "let's give it a go!!" after the off/on
  comparison (post 28: harness on passed review; off was rejected).
- **Watch:** replies still fall back to general process facts when no
  approved claim answers the question (post 28 asked about records and
  doses): more approved claims, and Derek's decision on paraphrasing claims,
  matter more than tone now.

## 2026-10-20: Context harness example (Proposal B)

### R-27. The drafter and reviewer get the library sections that fit the post (Live, off by default)
- **Before:** the drafter saw the first 6,000 characters of the Knowledge
  library (about 2% of it), in document order; the reviewer saw none of it.
- **After (only once `harness.enabled: true` is set in harvey.yaml; it ships
  `false`, so live drafting is unchanged):**
  - Library documents are split into sections at their headings (markdown,
    or numbered / ALL-CAPS / short-line headings in extracted Word and PDF
    text), at most about 1,500 characters each, and tagged with programs,
    medications, situations and topics (`config/harness.yaml`, public words
    only).
  - The drafter gets, within 8,000 characters: the Must-follow voice sections
    first (at most half the room while other sections match, so a long voice
    document can't crowd them out), then Must-follow sections that match the post, then reference
    sections by how well they match (program, medication, situation, unmet
    need, topic). Whole sections only; never Example documents; a document
    for another program is left out, as today.
  - The reviewer gets exactly the same sections, under "Reference sections
    the drafter used (guidance, not facts)".
  - The header is unchanged: library text guides tone and scope only; facts,
    numbers and prices still come only from approved claims.
  - Each draft records which sections it relied on (`drafts.context_json`:
    ids and names, never text); the drafted audit event lists the ids. The
    Pulse queue card shows one gray line: "Relied on: Community Agent Voice >
    Voice principles; ...".
  - Any harness error falls back to today's block for the drafter and to no
    block for the reviewer; drafting never fails because of it.
  - `scripts/harness_compare.py` drafts chosen mentions with the harness off
    and on, on a copy of the database, and prints both (records nothing;
    Claude on the subscription only).
- **Why:** owner-approved design for WellPeps' compliance lead ("Pulse
  knowledge design", Proposal B); built as a working example to compare
  before turning it on.
- **Source:** `docs/HARNESS-HANDOFF.md`.
- **Needs:** the owner runs the comparison and decides whether to turn it on;
  the 7 older PDFs with no extracted text need a re-upload to take part.

---

## 2026-10-20: Derek Goldberg's Community Agent Voice

### R-26. Voice principles, guide-offer wording, dashboard claim, posting as (Live)
- **Before:** the drafter's voice rules were Pulse's own; every guide offer
  had to say "it's free, it just asks for an email"; the drafting prompt even
  suggested "Happy to help." as filler; the reviewer checked compliance only;
  "We're the WellPeps team." did not count as a disclosure; and no approved
  claim covered the patient dashboard.
- **After:**
  - **Voice** (prompts/draft.md): Derek's seven principles (answer the actual
    question; conversational and informed; warm without performing empathy;
    helpful without pushing; confident within the role; transparent; brief
    but substantive, usually three to five sentences) and his avoid list
    (marketing language, scripted empathy, uninvited sales prompts, fake
    familiarity, stock closings). Preferred disclosure: "I work with WellPeps
    on patient education." The drafter never writes a person's name or an AI
    persona.
  - **Avoid list in the filter:** new yellow rule R50 (a warning to reword,
    never a block).
  - **Reviewer voice check** (prompts/review.md, check 15): judged apart from
    compliance; findings are "VOICE" notes shown in blue on the review card and
    never change the verdict.
  - **Guide offers** in Derek's wording: "Our free guide has a section on
    [topic] that explains [specifics]. Here's a link to get a free copy if
    you'd like." The filter now requires "free" instead of "email"; promising
    ungated access stays red (R49); a "section" title is checked against the
    guide's chapters like a "chapter".
  - **Disclosure:** "We're the WellPeps team." / "We are the WellPeps team."
    are accepted forms for the official account.
  - **Posting as** on each Pulse queue card: Me ("I work with WellPeps on
    patient education.") or WellPeps account ("We're the WellPeps team."). It
    swaps the disclosure only; saving re-runs the filter.
  - **New claim CLM-CAV-01-PATIENT-DASHBOARD** (yellow): "WellPeps includes
    ongoing support and a personal patient dashboard to chat with your medical
    provider if treatment is prescribed." Offered for provider-access and
    care-process questions; never a response time, 24/7, instant or direct
    physician access (Protocol §6).
- **Why:** owner, 2026-10-20: "switch to Derek's wording", "Add the dashboard
  claim from Derek's wording. We do have a personal patient dashboard that
  allows the user to chat with their medical provider", "Everything else is a
  go as well".
- **Source:** "Community Agent Voice" (Derek Goldberg, Oct 10; Knowledge
  library, Must follow).
- **Needs from Derek:** confirm the dashboard wording against Protocol §6
  ("direct physician access").

## 2026-10-20: Editing Knowledge documents

### R-24. Edit documents in the Knowledge library (Live)
- **Before:** a library document could only be replaced by uploading a new
  file; its words and details could not be changed in the dashboard.
- **After:** reviewers and admins see **Edit** on each document that is in
  use, in review or rejected. The form shows the whole text and the details
  (kind, must follow / reference / example, programs, approved by, takes
  effect, note). **Send edit for review** stores a *new version* in review
  that replaces the original; nothing changes in place. The version in use
  stays in use, and stays what the drafter reads, until an admin approves the
  edit; approving retires the old version ("Replaced"). Editing a document
  still in review replaces it at once; a rejected document can be fixed and
  sent again. One edit at a time per document. A must-follow document still
  needs a named approver for the new version. A PDF whose text could not be
  read can be made usable by pasting its text. Each edit is in the audit log
  (who, which document, which fields changed; never the text). Routes:
  `GET /api/knowledge/documents/{id}/text`, `POST /api/knowledge/documents/{id}/edit`.
- **Why:** owner, 2026-10-20: "please allow me to edit the Documents in the
  Library within the Agent's knowledge base".

### R-25. Remove documents from the Knowledge library (Live)
- **After:** admins see **Remove** on every document. After a "Yes, remove"
  confirmation the document is **deleted**, together with its older versions
  (replaced or rejected) and any edit of it still waiting for review; no other
  document keeps a "Replaces" link to it. The agent stops using it at once.
  There is no restore: upload the file again if it is needed later. Removing a
  waiting edit alone keeps the version in use. The append-only audit log keeps
  one "removed" event (who, which document, its previous status; never the
  text), as it does for every change. Route:
  `POST /api/knowledge/documents/{id}/remove` (admin).
- **Why:** owner, 2026-10-20: "I want to be able to REMOVE any docs that are no
  longer relevant or in use" and "I don't want reference to a doc that I have
  chosen to remove".

## 2026-10-20: Scheduled Reddit collection

### R-23. Daily Reddit collection with a daily spend cap (Live)
- **Before:** live Reddit collection (R-9, R-17) was off pending WellPeps legal
  sign-off; pulls were manual, at most $0.25 per run and $4.00 a month.
- **After:** `collectors.apify_reddit.enabled: true`, once a day
  (`interval_minutes: 1440`). Caps: 30 posts and $0.25 per run (Apify's own
  hard cap), a new **daily budget** of $5.00 (`daily_budget_usd`: no run,
  scheduled or manual, starts once today's runs (UTC) cost that much) and
  $155 a month as a backstop. The relevance sort and keyword gate (R-17) are
  unchanged. At today's settings a run costs about $0.12 to $0.25, so actual
  spend is far below the cap unless `max_items` is raised.
- **Why:** owner, 2026-10-20: "switch on daily Reddit collection. Let's limit
  the spend to $5.00 per day."
- **Legal:** scraping approved under the current guardrails (public posts
  only, minimal author info, a permalink on every mention, capped runs) for
  Reddit, Facebook groups and competitors' Trustpilot pages. Mark St. Pierre
  received oral approval from counsel; **written confirmation to follow**.
- **Note:** scheduled runs need the worker heartbeat running (Operator HQ →
  Start, or `pulse run`). Deployed, triage of the new posts bills the
  Anthropic API key.

## 2026-10-20: Questions we couldn't answer

### R-22. Recurring unanswered questions listed for the team (Live)
- **Before:** when Pulse skipped a question because no approved answer exists
  (Selection Module "skip or flag a knowledge gap") or held a direct WellPeps
  question because its central fact is not approved yet (R-20 HOLD), the
  reason was stored with the post but nobody could see the pattern.
- **After:** the Pain points roll-up (`harvey/insights.py`) has a fourth group,
  **Questions we couldn't answer**, grouped by topic (the need people state,
  else the medication) and by kind: "No approved answer" or "WellPeps fact not
  approved yet". Same privacy rules as pain points: a topic is listed only
  when at least 2 posts raise it in the window; up to 2 short quotes (no
  handles, links or e-mails) with a link to the post; compared with the
  previous window. Shown in Operator HQ (Pain points panel), the Briefs page,
  `pulse brief` output, one line of labels in the Slack brief, and
  `GET /api/insights/pain-points` (`knowledge_gaps`). Claude never sees this
  list; the brief prompt is unchanged. Limit: `top_knowledge_gaps` in
  `config/insights.yaml`.
- **Live data today:** empty. Posts triaged before R-20 (Oct 20) carry no skip
  reason, and none of the older "nothing useful to add" posts is a real
  question (they are safety escalations, complaints or ads). The list fills
  as new posts are triaged.
- **Why:** owner, 2026-10-20 ("Yes, please do this").
- **Source:** "Community Engagement – Why and When to Engage": "collect
  recurring skipped questions that lack approved answers. Those are useful
  signals for future eBooks, website content and Knowledge Base updates."
- **Use:** Derek / marketing read it to decide which approved answers, guide
  chapters or Knowledge documents to add next.

## 2026-10-20: Review desk APIs

### R-21. Review desk APIs: AI rewrite, knowledge documents, community permissions, spend (Live)
- **Before:** a reviewer could only edit a draft by hand; reference material
  (voice notes, approved wording, program status) lived in `config/` files that
  only an engineer could change; what a person learned about a community's
  rules (may we reply, may we link) had to be typed into
  `config/communities.yaml`; and nobody could see what the month's Claude and
  Apify calls had cost.
- **After:**
  - **AI rewrite** (`POST /api/mentions/{id}/rewrite`, reviewers and up): pick a
    passage of the draft and give a short instruction ("shorter", "warmer");
    Claude (agent `rewriter`, sonnet, never haiku, set in `harvey.yaml`)
    rewrites only that passage. Fixed rules go with every call: return only the
    passage; keep the meaning; add no facts, numbers, prices, timelines,
    outcomes, guarantees or medical advice; keep the WellPeps disclosure, links
    and guide or chapter titles exactly; never name or criticize other
    providers; never suggest DMs, calls or sharing order or health details;
    never claim to be a customer, patient or clinician; friendly plain voice,
    no emoji, hashtags or em dashes. Pulse then runs the deterministic
    compliance filter on the whole draft with the new passage in place and
    shows green, yellow or red with the rules hit; a rewrite that loses a link
    or the disclosure is thrown away. The saved draft does not change: the
    reviewer applies the text and saves it, and the normal save runs the filter
    again. Each rewrite is written to the audit log (who, which post, the
    instruction; never the text). Cap: 60 rewrites per person per day
    (`rewrite.daily_cap_per_user`).
  - **Knowledge documents** (Knowledge tab): upload .txt, .md, .docx or .pdf
    (up to 10 MB) and say what it is (approved messaging, compliance rule,
    voice and style, program status, guide, community rules, facts and FAQ,
    training, other), how much weight it has (must follow, reference, example),
    which programs it covers, who approved it and from when. A must-follow
    document needs a named approver. Uploads wait "in review" until an admin
    approves or rejects them; approving a replacement retires the document it
    supersedes. Active must-follow and reference documents for the post's
    program are shown to the drafter as guidance (at most 6,000 characters,
    must-follow first, newest first), headed "facts and claims still only from
    the approved claims list": a document can change tone and scope, never add
    a claim. A PDF is read only if a PDF reader is installed on the server;
    otherwise it is stored as "extraction pending" with no text.
  - **Community permissions** (Communities tab): an admin records, per
    community, whether brand replies are allowed, whether links are allowed, a
    moderator contact and a note. The recorded value wins over
    `config/communities.yaml` everywhere Pulse checks a community (triage,
    drafting, the compliance filter, approval), and clears the "community rules
    are unverified" hold. Each change goes to the audit log with the old and new
    values.
  - **Spend:** this month's Claude cost (in total and per agent) and Apify cost
    (total and run count). Under a Claude subscription the Claude figure is the
    API-equivalent the CLI reports, not a bill.
  - The review queue list can now include each post's draft, compliance
    result, community rules and guide chapter in one call.
- **Why:** the new review desk design needs these to work live: rewrite buttons,
  an upload screen for the people who own the wording, and the community and
  cost views. Migration `db/postgres/0005_review_desk.sql` (schema version 9)
  must be applied before this build starts on Postgres.
- **Source:** owner request for the merged Review Desk design (2026-10-20); the
  rewrite rules restate the Approved Messaging & Response Guide and
  `docs/RULES-OF-ENGAGEMENT.md`.
- **Needs from WellPeps:** a named owner for document approval (who may click
  Approve) and for recording community permissions.

## 2026-10-20: WellPeps guidance Oct 8-10 and session safeguards

### R-20. WellPeps guidance Oct 8-10 + session safeguards (Live)
- **Before:**
  - Pulse drafted any relevant post that passed the three R-14 gates. It had
    no rule for satisfied-provider updates, cheapest-price or coupon requests,
    already-answered threads or questions with no approved answer, and it
    skipped general treatment-education questions (R-14's open question).
  - A direct WellPeps question whose key fact is not approved ("What does
    WellPeps charge?") was drafted around the missing fact.
  - A post in a competitor's own subreddit (r/IVIMHEALTH, r/Noom,
    r/yourmdonline) could be drafted like any other.
  - A medical question about someone else ("my father on Ozempic drinks
    heavily") could get a boundary reply or slip through.
  - In a community whose rules were never checked, a draft was only yellow and
    could be approved.
  - A competitor complaint that also asked for alternatives was dropped if
    triage guessed "no reply".
  - The filter caught DMs but not calls, texts, e-mails or "reach out
    privately" (yellow), nor requests to post order or health details.
  - R31 blocked the bare phrases "your order", "your prescription", "your
    provider", even in neutral wording.
  - The reviewer prompt still said the disclosure belongs in the first
    sentence (R-16 moved it to the first two).
- **After:**
  - **Conversation selection** (`harvey/engagement.py`): the Selection Module's
    SKIP reasons are review gates: `satisfied_provider`, `price_only`,
    `already_answered` (new triage tags) and a knowledge gap (useful
    contribution 0). A direct WellPeps question whose central fact is not
    approved is HOLD (`gate:hold_unverified_fact`). General medication or
    treatment education now counts as worth a reply. Every post gets a record
    for the review desk: RESPOND / SKIP / HOLD / ROUTE, a reason code, public
    permission, posting hold and priority. The Module's §6 cases are tests.
  - **Competitor communities are listen-only:** a community named for a
    competitor (the insights community index) is never drafted, and a draft
    there can never be approved. Its mentions still feed competitor insights,
    and safety still routes.
  - **Someone else's health:** triage tag `other_person_medical` and a new
    safety-screen flag `other_person`. No reply of any kind; the post goes on
    the safety watch (the clinical owner if WellPeps is named).
  - **Posting held until permission:** in a community whose rules are
    unverified, that needs a moderator's permission not yet recorded, that
    prohibits brand replies, or that belongs to a competitor, Pulse may still
    draft for review but approval is refused ("human posts / held until
    permission"). Recording the rules or permission (config or the dashboard
    record, R-21) lifts it. Approval also rechecks duplicate participation in
    the thread.
  - **Warnings vs blocks:** the 80/20 share and a new per-community activity
    target (3 approved replies in 7 days) are warnings on the review desk,
    never blocks. Community restrictions, moderator instructions and platform
    limits are firm blocks.
  - **Alternatives requests are never discarded:** when the protocol answers
    an explicit alternatives request, the post is drafted even if triage
    guessed "no reply"; the triage prompt says so too.
  - **Filter (red):** any call, text, e-mail or private-channel suggestion;
    asking anyone to post an order number, account or health details; an
    invented clinical team ("I'm on the WellPeps clinical team"); promising
    ungated guide access ("no email required"). R31 narrowed to the core rule:
    claiming access to records, stating an order or account status, or tying
    the person to WellPeps ("your WellPeps order"); "contact your prescribing
    provider" and "your order" alone are fine. All approved responses pass.
  - **Length:** 40-90 words stays a target ("aim for"), never a filter rule;
    the drafter, reviewer and rules list say so.
  - Prompts updated: triage, safety screen, draft, reviewer (disclosure in the
    first two sentences; new SELECTION and OTHER_PERSON checks), rules list.
- **Why:** owner, 2026-10-20: Pulse must "function according to the
  safeguards and knowledge base identified and relied upon" in the review
  session.
- **Sources:**
  - WellPeps Engagement Goals and Conversation Selection Module V1 (Oct 10):
    §1 operating test; §2 steps 1-7; §3 respond/skip table; §4 priority and
    resource; §5 integration requirements; §6 evaluation cases.
  - "Community Engagement - Why and When to Engage" (its source note).
  - Agent Program Gameplan (Knowledge Base copy, Oct 8): §3.2 no confirming
    patients; §6.1-6.2 clinical questions go to humans; §7.1 "mostly, stay out"
    and no unsolicited DMs; §12 non-negotiables.
  - Derek Goldberg's e-mail decisions (Oct 10): consider the guide every time;
    activity targets are warnings, restrictions are firm blocks; safety,
    privacy and clinical overrides first, then the protocol; a complaint
    asking for alternatives is not discarded.
  - Pulse's response to Derek's review (Oct 7): R31 narrowing, unknown-community
    approval block.
- **Rules of Engagement:** new section 4a, the community table, words to avoid,
  retained safeguards (R31), superseded rules, a PENDING table and conflict
  rows.
- **Needs sign-off (PENDING, not built):**
  - company voice "we" on owned channels and review sites;
  - paraphrasing approved claims;
  - Hair Restoration status;
  - review-time statements;
  - whether the "it asks for your email" sentence can be dropped when the
    landing page explains it (Module §4).
- **Watch:** the activity target (3 per 7 days) and the knowledge-gap skip are
  Pulse proposals; check them against the first weeks of live data.

## 2026-10-19: Pain points & emerging ICPs

### R-19. Stricter competitor complaints, listed from one post (Live)
- **Before:** a competitor "complaint" was any mention naming a competitor that
  read as mildly negative (sentiment -0.2 or the "negative" label), so a neutral
  price comparison listing Henry Meds counted as a complaint about Henry Meds;
  and competitor complaints shared the privacy floor of 2, hiding single ones.
- **After:** `insights.yaml competitor_complaint`: the competitor must be named
  by triage or attributed from its own community AND the post must show one
  real complaint signal: a complaint / billing / legal category, a venting,
  alternatives-requested or legal intent, sentiment -0.4 or lower, or concrete
  complaint wording ("charged me", "up front", "never sent", "refund",
  "couldn't cancel"...). Competitor complaints list from 1 post; pain points
  and segments keep the floor of 2. On live data (14 days): Ivim Health and
  Noom Med listed with quotes; the Henry Meds price comparison no longer counts.
- **Why:** owner, 2026-10-19: "lower the public posts for competitor tracking
  to 1" and "tighten the rules with regard to competitor complaints".

### R-18. Pain points & emerging ICPs (Live)
- **Before:** Pulse stored per mention the competitor, category, sentiment,
  the unmet need and intents (protocol record) and verbatim phrases, but
  nothing rolled them up for the team. A post in r/IVIMHEALTH whose triage left
  the competitor empty counted for nobody.
- **After:**
  - **Community attribution:** when triage names no competitor but the
    mention's community (the subreddit in the collected data, or the `/r/<name>/`
    in the link) is named like a competitor or one of its aliases (case,
    spaces, punctuation and accents ignored: r/IVIMHEALTH is Ivím Health,
    r/Noom is Noom Med), the roll-up attributes it to that competitor
    with source "community". Short names (under 4 letters) and everyday words
    (Found, Eden, Roman, Mochi ...) never match on their own.
    Stored triage rows are not changed.
  - **Roll-up** (`harvey/insights.py`, no Claude): for the brief's window,
    against the window before it. *Pain points:* mentions by the need people
    state (complaints with no stated need by category), ranked with
    complaint-like mentions counted twice, 1-2 verbatim phrases each (at most
    120 characters, no links or handles) with mention id and permalink and the
    competitors involved. *Competitor complaints:* per competitor (named or
    community-attributed): count, main pain, worst sentiment, one example.
    *Emerging ICP signals:* four segments (Insurance drop-out, Price
    comparer, Safety-first seeker, Unhappy with current provider), each
    flagged new, growing, steady or declining against the previous window.
  - **Rules are editable** in `config/insights.yaml`: the complaint
    definition, the minimum count (2: nothing seen in fewer mentions is listed),
    the segment rules (AND of clauses; each clause is an OR of intents,
    needs, categories, keywords, "a competitor is named", "reads as a
    complaint") and the community name rules.
  - **Brief:** a "Pain points & emerging ICPs" section in the stored brief
    and the Pulse tab (also under Live trends), one line of labels in the
    Slack brief and the `pulse brief` output. The model sees counts and
    labels only (no quotes, links or ids) and may add at most two sentences of
    interpretation; a sentence with a number that is not in the data drops the
    note. Quotes, links and numbers come from the roll-up.
  - **API:** `GET /api/insights/pain-points?days=7` (any signed-in role,
    read-only, same shape as the brief section).
- **Why:** owner, 2026-10-19: "We are tracking complaints to identify
  ongoing pain points and emerging ICPs."
- **Review:** the segment keywords are a first draft; WellPeps marketing
  should read `config/insights.yaml` and adjust them. Counts are small while
  collection is capped, so most segments will show "new" until a second window
  exists.

## 2026-10-19: Reddit collection relevance

### R-17. Tighter Reddit collection (Live)
- **Before:** the Reddit search ran with `sort: new` over 8 searches, and every
  result was stored and sent to triage. Measured 2026-10-15: of 14 posts
  pulled, 9 were off-topic (r/atljobs, r/CAguns, r/Kingdom, r/BunaTV...), and
  6 of those contained none of our terms at all. Apify bills per returned
  item, and each junk post also cost a Claude triage call.
- **After:**
  - Searches are sorted by relevance (`collectors.apify_reddit.sort`, default
    `relevance`; also `new`, `top`, `hot`, `comments`). The 48-hour lookback is
    unchanged, so results stay recent.
  - A keyword gate (`require_keyword_match`, on by default) drops any result
    whose title and body contain none of Pulse's listening terms: the brand,
    product, generic, brand-name, slang and misspelling lists in
    `config/keywords.yaml`, competitors and aliases from
    `config/competitors.yaml`, and `extra_terms` (GLP-1, GLP1, telehealth,
    telemedicine, compounded, weight loss medication, hair loss, erectile
    dysfunction, peptide, peptides). Whole words, case-insensitive ("find" is
    not "fin", "nada" is not "NAD"). The same topic check triage already uses.
  - Dropped posts are counted and shown in the ingest report, e.g.
    "9 skipped (9 off-topic)".
  - Searches rewritten to sharper exact-phrase, buying-intent queries (for
    example "switching" telehealth semaglutide, "Henry Meds" semaglutide).
    Caps unchanged: 30 items, $0.25 per run, $4.00 per month. Still off.
- **Why:** 9 of 14 posts were off-topic on 2026-10-15; this saves Apify credit
  and triage calls.
- **Verify with one capped pull** (`pulse ingest --apify-reddit`) before
  turning scheduled collection on, and check the off-topic count.

## 2026-10-15: Owner decision on disclosure placement and voice

### R-16. Disclosure placement and familiar voice (Live)
- **Before:** every reply had to carry the WellPeps disclosure in its very
  first sentence ("I work with WellPeps."), and the drafter always opened with
  it. Replies read as formulaic; the provider-boundary line was appended
  to most of them.
- **After:**
  - The disclosure must appear **within the first two sentences** and
    **before any other mention of WellPeps, its programs or a wellpeps.com
    link**. It is never only at the end, only in a profile, or after a "more"
    fold. It can be its own sentence or woven into one ("I'm part of the
    WellPeps team, so I'm biased, but here's what I'd check..."). The approved
    phrases are unchanged.
  - Drafts open by responding to the person (a short, specific reaction, not
    canned empathy), then disclose, then answer, then one next step. They use
    contractions and plain words, vary their openings and have no stock closing
    line. The provider line ("A licensed healthcare provider determines...")
    appears only for treatment choice, suitability, eligibility, results or
    safety, worked into the answer. The guide's email gate can be phrased
    naturally ("it's free, it just asks for an email").
  - The deterministic filter (R3), the reviewer prompt, the rules list, the
    five style examples and the Rules of Engagement are updated to match.
    `boundary_only` replies (approved text posted verbatim) are unchanged and
    still open with the disclosure.
- **Why:** owner instruction 2026-10-15: "Please don't say I'm with WellPeps
  1st... There is no requirement that it be first. It just needs to be obvious.
  I want the messages to sound more familiar." WellPeps' own documents only ask
  that the disclosure be easy to notice and not buried (Operations Manual
  §4.3; Approved Messaging Guide §4; Competitor Protocol §7 "clear
  in-message"). "First sentence" was a Pulse implementation choice, as already
  told to Derek.
- **Needs sign-off:** Derek (confirms placement within the first two sentences
  is acceptable).

---

## 2026-10-09: Changes from the first live data pull

The first live Reddit pull (22 real posts) showed Pulse reviewing and paging
on posts that could never benefit WellPeps. Agreed changes:

### R-14. Three-gate review: relevant, allowed, worth it (Live)
- **Before:** every relevant-looking post with a question was drafted.
- **After:** a post reaches the review queue only if it (1) is relevant: it
  involves WellPeps, a competitor, a WellPeps program/treatment, or someone
  shopping for telehealth; (2) is allowed: community rules permit it and it
  is not an individual clinical question; and (3) is worth it: a reply could
  plausibly benefit WellPeps (choosing a provider, how it works, comparing
  price or programs, asking about WellPeps, or a guide chapter answers it).
  Everything else feeds trends and the language bank only.
- **Order matters:** safety and permission are checked before benefit, so
  benefit is never a reason to engage with someone vulnerable.
- **Why:** Pre-LegitScript plan ("Respond only where WellPeps can add value";
  "Don't respond merely because a keyword appeared"); first live pull.

### R-13. Side-effect posts that don't involve WellPeps (Live, needs sign-off)
- **Before:** an adverse event in any thread got the approved safety reply
  and paged the clinical owner.
- **After:**
  - WellPeps named, or the person appears to be a WellPeps patient: page the
    clinical owner and draft the approved safety reply (unchanged).
  - Serious (ER, hospitalization, liver, pancreas, gallbladder, allergic
    reaction, self-harm, emergency) with the provider not named: no page, no
    reply; listed on a daily **safety watch** a person scans.
  - Another provider named, or expected effects (shedding, nausea,
    puffiness, fatigue): nothing; trends only.
- **Why:** user decision 2026-10-09; first live pull paged on routine
  hair-shedding chatter and drafted an unsolicited WellPeps reply to a
  stranger's side-effect post.
- **Needs sign-off:** WellPeps clinical / compliance lead. This narrows the
  Approved Messaging Guide §10 and Competitor Protocol Example 4, which apply
  to adverse events "in any thread". Also confirm whether WellPeps has any
  duty to act on side effects it reads about involving other providers.

### R-12. Keyword escalations only on relevant posts (Live)
- **Before:** the emergency-keyword safety net ("lawsuit", "fraud",
  "gallstones"...) escalated any post containing the word.
- **After:** it fires only when the post involves WellPeps, a competitor, a
  WellPeps program/treatment or telehealth; legal, privacy and billing pages
  only when WellPeps is the subject.
- **Why:** first live pull escalated a religious-law post to legal and a
  business-news roundup as billing fraud.

### R-11. Buying-focused Reddit searches (Live)
- **Before:** 8 broad searches; about half the results were off-topic.
- **After:** exact-phrase searches aimed at people choosing or comparing
  providers; side-effect-heavy searches removed.
- **Why:** first live pull; saves Apify credit.

### R-15. Fixes found while testing R-12 to R-14 (Live)
- A person can still escalate any post by hand from the review desk (legal,
  adverse event or safety watch), even when it isn't about WellPeps; the
  automatic WellPeps-only rule no longer blocks a human's decision.
- Pages and the dashboard keep showing why a post was urgent (keyword, safety
  screen...) after a review gate is recorded.

### Open question for the team
- **General treatment-education posts** (e.g. "how do GLP-1s work?", no
  provider named, nothing about buying): under R-14 these are not drafted,
  because a reply is unlikely to benefit WellPeps. The Competitor Protocol's
  Example 10 treats such a post as worth an educational answer. Decide
  whether general education should count as "worth it".
  **Answered by R-20 (2026-10-20):** WellPeps' Selection Module V1 (Oct 10)
  §3 says "General medication or treatment education: RESPOND"; Pulse now
  drafts them (the Module's SKIP reasons still apply).

### R-10. Suspected seeded posts flagged (Proposed)
- Several shopper posts appeared the same morning with similar wording and
  the same lesser-known providers named. Proposal: flag posts that look
  seeded for a person to check before WellPeps engages.

---

## 2026-10-07 to 2026-10-09: Live data, billing, guide refinements

### R-9. Live Reddit collection through Apify (Live; scheduling off pending legal sign-off)
- Pulse can pull public Reddit posts through Apify (`trudax/reddit-scraper-lite`),
  capped at 30 posts and $0.25 per run and $4 per month; Apify's account
  limit also applies. Live posts go to a separate live database, never the
  demo. Scheduled collection stays **off** until WellPeps' legal sign-off on
  scraping; pulls are by hand meanwhile. A slow run is stopped but the posts
  it already collected (and paid for) are kept.
- **Needs sign-off:** WellPeps legal (scraping of Reddit, and later Meta / TikTok).

### R-8. Who pays for Claude (Live)
- Local runs and demos use the Claude subscription; the deployed version
  uses the Anthropic API key automatically. API keys are never passed to the
  local Claude CLI, so billing can't silently switch.

### R-7. Guide only when a chapter helps (Live)
- **Before:** every answering reply had to include a Smart Patient's Guide.
- **After:** Pulse considers the guide every time and includes it only when a
  specific chapter answers the question; otherwise it is omitted and the
  reason recorded.
- **Why:** Derek Goldberg's review; Pre-LegitScript plan ("Not every comment
  gets a link"; "Don't mention WellPeps in every response").

### R-6. No guide in communities with unchecked rules (Live)
- A guide is a company resource offer, so none (not even by name) where a
  community's rules haven't been verified. Basis: Competitor Protocol §2,
  §10; Operations Manual §5.1.

### R-5. Chapter titles must be real (Live)
- A reply that cites a "chapter" not found word-for-word in the guides is
  flagged for review.

### R-4. Provider line only when relevant (Live)
- "A licensed provider determines whether treatment is appropriate" only on
  treatment, suitability, eligibility or results questions, not on process,
  availability, pricing or service answers. Basis: Approved Messaging Guide §3
  ("states the boundary when necessary").

### R-3. Program status only when asked (Live)
- The reviewer flags a "coming soon / available now" sentence the poster
  didn't ask for.

### R-2. Competitor complaints asking for alternatives get answered (Live, bug fix)
- A complaint about a competitor that asks "who else should I try?" was
  silently dropped. It now gets an answer under the competitor protocol;
  pure venting still gets none.

### R-1. Open items from Derek's review not yet built
- Block approval of replies in communities with unverified rules
  (drafting allowed, posting not). **Built in R-20.**
- Clinical-only approval of safety replies (admins only with a clinical role).
- Escalation stages "action pending" and "resolved".
- Narrow the privacy phrase rule to record access / patient-status
  confirmation. **Built in R-20.**
- Real-model evaluation set built from raw posts.
- Record the Competitor Protocol's approval status, approvers and date.
- Approved wording for official-account company voice.
