WELLPEPS

Specialized Agent Program

Build plan, platform strategy, physician tier menu, and social listening operations

Prepared 17 August 2026

Companion file: WellPeps_Agent_Governance_Workbook.xlsx

> Read this first
>
> This document is a planning instrument, not legal advice. It was compiled from public primary sources (FDA.gov, FTC.gov, statute text, platform policy pages) and credible secondary sources, all cited in the appendix.
>
> Two categories of finding require verification before you act on them. First, Reddit blocks automated access, so subreddit rule text throughout is marked UNVERIFIED — I have a way to accomplish this using a special browser to access Reddit. The browser gives the appearance of an actual human, it can go anywhere you or I can go, and it also inherits necessary cookies when needed. Second, every regulatory item requires review by qualified healthcare regulatory counsel.

# Contents

If this page appears blank, right-click below and choose "Update Field"

Contents 2

1. Executive summary 3

2. The organizing principle 5

3. Layer 0 — The governance spine 6

3.1 The Approved Claims Library 6

3.2 The Prohibited Language List 6

3.3 The Escalation Taxonomy 6

3.4 The Audit Log 6

4. Layer 1 — Listening agents (read-only) 8

5. Layer 2 — Drafting agents (human-in-the-loop) 9

5.1 Why the adversarial reviewer is not optional 9

5.2 The autonomy rule 9

6. Layer 3 — Inbound customer service 11

6.1 Three things that get wrongly lumped together 11

6.2 The architecture: a triage nurse, not a doctor 11

6.3 Chat versus voice 11

6.4 Where the money actually is 12

6.5 Build versus buy — the asymmetry 12

7. Platform strategy 13

7.1 The headline: mostly, stay out 13

7.2 The four legitimate openings 13

7.3 Get the free credential 14

7.4 Platforms beyond Reddit 14

8. The physician tier menu 16

8.1 How to price it 16

9. Social listening operations 18

9.1 Query architecture 18

9.2 Cadence and SLA 18

9.3 Coverage hours — decide now 18

9.4 Staffing reality 18

9.5 Metrics 18

10. The regulatory perimeter 20

10.1 What FDA did 20

10.2 What FTC did 20

10.3 The peptide question 20

10.4 LegitScript is the chokepoint 21

11. The 90-day sequence 22

12. Six non-negotiables 23

Appendix A — What could not be verified 24

Appendix B — Primary sources 24

# 1. Executive summary

WellPeps asked for a plan to build specialized agents armed with the physician knowledge base and approved blog content, to manage those agents across social platforms, and to package the result as options physicians can choose and pay for. This document answers that, and adds the inbound customer-service layer that the original scope did not include.

Four things are worth knowing before the detail.

### Your risk sits in the product menu, not the marketing

Research turned up something more urgent than any channel question. As of August 2026, BPC-157, TB-500, ipamorelin and CJC-1295 are not on the FDA 503A bulk drug substances list and cannot lawfully be compounded. The removal of twelve peptides from FDA's Category 2 list in April 2026 was not authorization — it made those substances eligible for review. The Pharmacy Compounding Advisory Committee vote of 23–24 July 2026 was non-binding, passed on narrow margins, and FDA's own career scientists opposed all seven substances under consideration. FDA must still complete notice-and-comment rulemaking before any of them can be compounded.

If WellPeps dispenses those compounds today, that is unapproved-new-drug exposure independent of anything an agent ever posts. And under LegitScript Standard 3, a single FDA warning letter can cost you certification — which means every paid channel on Google, Meta, Microsoft and LinkedIn simultaneously. Settle the product menu before you scale the megaphone.

### Listening is where the value is; posting is where the risk is

Of the forty-eight communities mapped for this report, the correct answer in the substantial majority is: do not have a presence. That is not a failure of the research — it is the finding. The communities with the highest purchase intent are the ones most hostile to brand participation, and the few with a legitimate sanctioned path are small. Your return comes from what you learn, plus a very small number of high-credibility physician appearances, plus product quality good enough to earn organic mention.

### The customer-service agent has the only arithmetic ROI in the program

Social listening ROI is real but fuzzy. Support deflection is cost-per-contact multiplied by contacts deflected. And the retention agent — the one that sits on the cancel flow and can offer a pause, a tier change, or a provider consult — is worth more per interaction than anything else described here. One saved subscriber beats a hundred community comments. This is a corporate-funded platform capability, not a physician add-on, and it should be built first.

### Your reputation footprint is currently blank, and that expires

Searches for WellPeps reviews return the company's own homepage and unrelated peptide brands. No Trustpilot profile, no BBB profile, no Reddit threads. Meanwhile the category has been colonized by affiliate SEO and provider-scorecard sites, and AI answer engines have nothing about WellPeps to synthesize from — so they will assemble an answer from competitor-owned content and the FDA and FTC coverage of your peers. Claiming and shaping these surfaces is far cheaper now than after volume arrives.

# 2. The organizing principle

Build a hospital, not a roster of doctors.

You do not open a hospital by hiring specialists. You build the medical records system and the pharmacy formulary first — the shared source of truth. Then you credential specialists on top of it, each with a defined scope of practice. The nurse practitioner does not get surgical privileges. And every action gets charted.

In this program, the agents are the specialists. The knowledge base plus the approved claims library is the formulary. The audit log is the chart. If you build agents before you build the formulary, you have hired surgeons for a building with no operating room.

| Layer | What it is | Why it comes in this order |
|---|---|---|
| Layer 0 | Governance spine — claims library, prohibited language, escalation taxonomy, audit log | Nothing can be safely generated until there is a defined universe of things that may be said |
| Layer 1 | Listening agents — read-only | Zero publishing risk, immediate insight, and it earns the credibility to do anything else |
| Layer 2 | Drafting agents — human-in-the-loop | Value creation, but every output is gated |
| Layer 3 | Inbound customer service | Different buyer, different risk profile, best economics |
| Layer 4 | Orchestration — queues, SLAs, circuit breakers, audit | The thing that makes the whole system defensible |

Twenty agents are specified across these layers. The full registry — scope, autonomy level, review requirement, risk tier, and build-or-buy call for each — is Tab 04 of the companion workbook.

# 3. Layer 0 — The governance spine

Weeks 1–3. Nothing deploys until this exists. Every artifact described here has a template in the companion workbook.

## 3.1 The Approved Claims Library

Not a document — a database. Every claim carries a claim ID, the exact approved language, a source citation, the physician approver's name, an approval date, applicable programs, required disclaimers, jurisdictional limits, and an expiry date.

The rule: no claim ID, no publish. Every agent output must carry provenance back to an approved claim. This single constraint eliminates most of your hallucination and compliance risk at once, and it converts a fuzzy editorial judgement into a mechanical check that a second agent can enforce.

## 3.2 The Prohibited Language List

Twenty-five hard blocks are specified in Tab 02, drawn from actual enforcement language rather than general caution. Some of them are not obvious:

- "generic Ozempic", "same active ingredient", "clinically proven", "FDA-approved pharmacy" — each named by FDA as violative framing in the warning letters issued the week of 15 June 2026.
- Branding a compounded drug under the WellPeps name without naming the compounder — an explicit violation pattern in FDA's 3 March 2026 action against thirty telehealth companies.
- Any AI persona using "Dr.", MD, PA or RN — Delaware HB 191, effective 23 April 2026, bars non-human entities from using protected professional titles. It is not mental-health-specific; it reaches your entire offering.
- Confirming that a named individual is a patient — OCR has penalized exactly this in public review responses ($50,000 against Igbinadolor in 2022, $10,000 against Elite Dental in 2019).
- Any AI-generated favorable review or comment presented as organic — prohibited by the FTC's 2024 Rule on Consumer Reviews and Testimonials, with per-violation civil penalties, and separately by Reddit's Manipulated Content policy as updated 19 May 2026.

## 3.3 The Escalation Taxonomy

Fifteen triggers with named owners and SLAs, specified in Tab 03. Assign a person to each one before launch; "the team" is not an owner. The five that matter most: adverse event, suicidal ideation, request for individualized advice, a person self-identifying as a patient in public, and a legal or press inquiry.

> The hidden cost of listening
>
> The moment WellPeps begins monitoring social channels, it may create an obligation to capture and act on what it finds. Adverse event signals do not become less real because they arrived through a listening tool.
>
> This is the item most teams miss when they assume listening is the safe, low-commitment first step. It is still the right first step — but budget the pharmacovigilance routing alongside it, not after it.

## 3.4 The Audit Log

Every draft, reviewer, timestamp, claim IDs used, filter result, adversarial-reviewer verdict, final published text, and permalink. Tab 11 defines the schema; in production this should be a database.

This is chain of custody. It is also, in practice, your best sales asset — it is what makes a physician comfortable letting anything be published under their name.

# 4. Layer 1 — Listening agents (read-only)

Deploy first. Zero posting. Lowest risk, fastest value, and it is what earns permission for everything downstream.

| Agent | Job | Priority |
|---|---|---|
| Community Cartographer | Discovers and scores communities by relevance, volume and moderator tolerance; maintains the live version of the map in Section 7 | P0 |
| Social Listening Agent | Monitors the eight query families in Tab 08; classifies every mention by intent — purchase intent, complaint, misinformation, competitor, adverse event, regulatory | P0 |
| Adverse Event Sentinel | Detects and routes possible adverse events to a named clinical owner within a 15-minute acknowledgment SLA. Mandatory the moment listening begins. | P0 |
| Competitive Intelligence Agent | Tracks Hims, Ro, LifeMD, Noom, Henry Meds, Mochi, Eden, Marek Health, Blokes/Joi, Fountain Life — pricing, claims, creative, sentiment, regulatory actions | P0 |
| Reputation Monitor | Trustpilot, BBB, app stores, "is WellPeps legit" threads, and the GLP-1 provider scorecard sites that now rank for category queries | P0 |
| Misinformation & Safety Radar | Flags dangerous advice in monitored communities — gray-market sourcing, unsafe dosing, research-chemical injection. Brand-safety value and genuine public-health value. | P1 |
| AI Visibility / AEO Agent | Tracks how WellPeps appears in ChatGPT, Perplexity, Gemini and Claude answers for category queries | P1 |

Recommended starting stack: F5Bot on the free tier today for brand, executive and risk-term early warning, Syften Standard at $49.95/month as the working tool, and one AI-visibility tool to establish a baseline. Defer enterprise listening platforms until mention volume justifies them. Full vendor comparison with verified pricing is Tab 09.

> Ask every listening vendor one question
>
> Reddit's Data API Terms require a contract for commercial use and prohibit using Reddit content for model training without consent. Any vendor claiming Reddit coverage is either a licensed Reddit data partner, operating within the free tier, or scraping in violation of terms.
>
> Make them tell you which. That answer is your risk, and it does not appear on any feature comparison chart.

# 5. Layer 2 — Drafting agents (human-in-the-loop)

| Agent | Job | Risk | Sold as |
|---|---|---|---|
| Content Repurposing Agent | One approved blog post into twelve formats — carousel, short-form script, thread, LinkedIn post, email, video description, pin. Highest volume per dollar in the program. | Low | Tier 2 |
| Owned-Channel Reply Agent | Comments and DMs on WellPeps-owned Instagram, Facebook, YouTube and X, where brand voice is expected and rules are clearest | Medium | Tier 2 |
| Community Response Agent | Drafts value-first, cited answers for Reddit and forums. Posted by a human from a verified, disclosed account. | High | Tier 1 |
| Physician Voice Agent | One per doctor. Trained on that physician's own writing, talks and clinical positions. Builds their brand, not just yours. | Medium | Tier 3 |
| Review Response Agent | Trustpilot, BBB, app stores — drafting from the HIPAA non-confirmatory template only | High | Tier 2 |
| Ad Copy Agent | Paid creative with LegitScript, Google, Meta and FDA claim pre-checks built in | High | Add-on |
| Podcast & PR Pitch Agent | Finds shows, drafts pitches, builds physician briefing documents. Very high credibility per hour of physician time. | Low | Tier 3 |
| Adversarial Compliance Reviewer | A separate agent whose only job is to kill the draft — red-team it against FDA, FTC, HIPAA, state AI law and platform rules. It never writes. | — | Bundled |

## 5.1 Why the adversarial reviewer is not optional

Separation of church and state: the writer never grades its own homework. A model asked to check its own output for compliance is being asked to find fault with reasoning it just produced and still believes. A second model, given only the draft and the rules and instructed to find the violation, catches an order of magnitude more.

Give it a bias toward refusal. A false positive costs a physician thirty seconds of review. A false negative costs a warning letter, and under LegitScript Standard 3, potentially every paid channel you have.

## 5.2 The autonomy rule

No agent publishes on a clinical topic without human approval, permanently. Not after a good quarter, not at a 99% approval rate, not for a physician who says they trust it. The autonomy column in Tab 04 marks these agents "draft only — permanently," and that designation should never be revisited.

Autonomy can be earned on genuinely low-risk, non-clinical, high-volume work — order-status responses, scheduling, formatting. That is where the efficiency gain lives, and it is enough.

# 6. Layer 3 — Inbound customer service

This layer was absent from the original scope and it has the best economics in the document. It is also a different product with a different buyer: a corporate-funded platform capability, not something individual physicians opt into. Making it optional would produce inconsistent patient experience and a compliance patchwork.

## 6.1 Three things that get wrongly lumped together

|  | Handles | Risk | Verdict |
|---|---|---|---|
| Support deflection | Order status, shipping, billing, receipts, refills, address changes | Low | Build or buy now |
| Pre-sale conversion | "Am I a candidate?", pricing, how it works | Medium — drifts clinical fast | Hard guardrails |
| Clinical triage | Dosing, side effects, symptoms, "should I…" | High — arguably practice of medicine | Route to humans. Full stop. |

Most teams build one bot and let it wander across all three. That is the failure mode.

## 6.2 The architecture: a triage nurse, not a doctor

Put a classifier as step one, before any generation happens. Its only job is to decide which hallway the conversation goes down — clinical or non-clinical. It does not answer anything. It routes.

That is the emergency-room triage nurse. She does not treat you; she decides whether you sit in the waiting room or go straight back. Get that one decision right and everything downstream is manageable. Get it wrong and no amount of prompt engineering saves you.

## 6.3 Chat versus voice

Chat has an eraser. Voice has a live mic.

Chat is auditable, editable before send, easy to gate, cheap, latency-tolerant and asynchronous. You can run it in suggest-mode — drafts for a human — for months before granting any autonomy. Start here.

Voice carries a materially heavier compliance load, and the 2026 legal landscape has made it heavier still:

- State AI disclosure laws. Fourteen new state chatbot laws were enacted in 2026 alone. California SB 243 (effective 1 January 2026) and Oregon SB 1546 (effective 1 January 2027) both carry a private right of action at $1,000 per violation. New York's Article 47 has been live since 5 November 2025 with penalties up to $15,000 per day.
- Mental-health-specific prohibitions. Tennessee bars AI from representing itself as a qualified mental health professional. Nevada prohibits AI programmed to provide what would constitute professional mental or behavioral healthcare. Illinois goes furthest — the WOPR Act bans AI therapy and, in a sleeper clause, bans detecting emotions or mental states in clients at all, which plausibly captures routine sentiment analysis.
- Call recording consent in California and roughly eleven other two-party-consent states, plus TCPA exposure on anything outbound.
- BAA required. Any vendor touching patient conversations needs a signed Business Associate Agreement, which disqualifies most consumer voice AI platforms immediately.

> Your Mental Wellness program makes this a launch blocker
>
> The WellPeps site lists Mental Wellness as coming soon. Illinois may be incompatible with an AI-assisted mental wellness product outright — consider geo-fencing it until counsel clears the position. California, New York, Nevada, Utah, Tennessee and Delaware obligations are live today.
>
> Build one national floor rather than fifty state variants: persistent non-human disclosure at session start and every three hours; a published crisis protocol with automated detection and 988 referral; no emotion or mental-state inference; no ad targeting derived from user inputs; no clinical titles on any AI persona; and no representation of therapeutic capability anywhere in marketing.
>
> One cheap win: Utah HB 452 offers an affirmative defense to operators who file a written compliance policy with the Division of Consumer Protection. Take it.

## 6.4 Where the money actually is

Two places, and the second is bigger.

Deflection. The top contact drivers in this business are predictable — where is my shipment, billing questions, refill timing, HSA and FSA receipts, address changes. All non-clinical, all high-volume, all mechanical. Unlike social listening, the math here is not arguable.

Retention. GLP-1 telehealth churn is severe. An agent sitting on the cancel flow that can offer a pause, a lower-cost tier, a dose-adjustment consult, or a shipping-cadence change is worth more per interaction than anything else in this plan. If you build one agent this quarter, build that one.

## 6.5 Build versus buy — the asymmetry

Build the social and content agents. Your differentiation there is the physician knowledge base. No vendor can sell you that.

Buy the support agents. The differentiation there is HIPAA infrastructure, telephony, BAAs and audit tooling — commodity plumbing you would spend a year rebuilding badly. The healthcare-specific voice and chat vendor category exists precisely because generic tools do not clear the bar.

# 7. Platform strategy

Forty-eight communities were researched and scored across the six WellPeps programs. The full map, with sizes, sourcing dates, promo tolerance, sensitivity ratings and verification status, is Tab 05 of the workbook. What follows is the strategic read.

## 7.1 The headline: mostly, stay out

In the majority of communities mapped, the correct answer for a telehealth brand is to have no presence at all. The pattern is consistent: the communities with the highest commercial intent are the ones that most actively police brand participation, because they have been targeted relentlessly.

Three examples make the point:

- r/loseit (4.2 million members) reportedly bans standalone weight-loss medication posts and separately bans naming online pharmacies. The single largest weight-loss audience on Reddit is structurally hostile to your entire category. Paid is the only route in.
- r/HealthInsurance (121,000) carries the quoted rule "Solicitation will result in a permanent ban" and warns members to report unsolicited DMs. This is your highest accidental-ban risk — one well-meaning representative following up privately with someone asking about coverage ends your presence there permanently. Write the SOP forbidding it today.
- r/Testosterone (240,000) shows 79 clinic reviews and the word "reputable" appearing 21 times. It is the highest commercial-intent community in the map — and your reputation is being decided there without you. The play is not to post. It is to earn the organic mention through transparent pricing, named prescribers, real lab panels and easy cancellation.

## 7.2 The four legitimate openings

Four assets have a documented, sanctioned path for a physician-affiliated brand. These are where effort should concentrate.

| Asset | Why it works and what to do |
|---|---|
| ExcelMale.com | 24,000+ members, 33,000+ threads, expert-moderated, identifiable owner, already monetizes clinic sponsorship, and its audience is explicitly there to find a TRT provider. Highest-value single asset on the map. Approach the owner directly about sponsorship, and back it with substance — free lab-interpretation guides, transparent published pricing, named physicians with licenses listed. |
| Clinical Peptide Society | Professional society with a provider directory, certification program, case registry and published clinical consent templates. Best risk-adjusted investment available: high-intent inbound from a directory patients and referring clinicians actually search, a documented clinical-governance story that directly supports LegitScript Standards 1, 2, 7 and 8, and zero gray-market adjacency. |
| r/tirzepatidecompound | 187,000 members and, per third-party sourcing, the only large GLP-1 subreddit with an explicit business path: businesses require moderator approval and identifying flair before posting. "Provider Recommendations" is an existing sanctioned post category. Apply for flair; post under a named account; never claim equivalence to approved drugs. |
| r/tressless | 547,000 members, moderate sensitivity, and the most permissive community in the hair category. Your best candidate for a legitimate physician appearance. Modmail first, never post first. Propose a verified-flair dermatologist AMA under a named physician. Credibility there is bought with candor about side effects and nothing else. |

## 7.3 Get the free credential

Reddit opened its Verified Profiles program to businesses in public beta on 9 July 2026. It is free, requires only Reddit Pro and an account in good standing with a display name matching the business name, and explicitly does not grant posting rights or affect moderation. That is precisely why it is worth having — it converts WellPeps from an unidentified account into an identified entity, which is what makes the moderator conversation credible.

## 7.4 Platforms beyond Reddit

| Platform | Reality | Play |
|---|---|---|
| YouTube | Most underrated channel available to you. Long-form physician explainers rank in Google and get cited by AI assistants. | Fund now. Everything else is rented; this compounds. |
| Trustpilot | Highest-leverage reputation surface. Responses allowed on every tier including free. Strict rules: no incentives, no gating, no cherry-picking, invitations to all customers at the same journey point. | Claim and manage now, while the profile is still blank. Template-locked responses only, for HIPAA reasons. |
| BBB | Profiles and letter grades exist for non-accredited businesses too — you do not opt in to being rated. Occupies multiple page-one slots per brand. | High value per unit of effort. Claim the profile. |
| Google Business Profile | Requires in-person customer contact and explicitly excludes online-only businesses. A pure 50-state telehealth operation likely does not qualify. | Plan around its absence. No review stars in branded search. |
| AI answer engines | WellPeps has almost no third-party footprint, so engines will assemble an answer from competitor affiliate content and FDA/FTC coverage of peers. | Instrument now. Owned, citable, factually grounded pages are both the AEO play and the reputation play — same work. |
| Instagram | Carousels, Reels, DM capture. Meta requires written permission for prescription drug ads and bans condition-attribution creative. | Fund after YouTube. |
| TikTok | Prescription drug promotion banned across essentially all markets. Hair loss falls under cosmetics and cannot make medical claims. No permitted mental health category. | Cannot carry your products. Trend detection and unbranded education only. |
| Facebook Groups | Largest patient-community surface by raw membership, but Mochi Health's own brand-run GLP-1 group was shut down. | Listen. Do not build owned community infrastructure on Meta. |
| LinkedIn | Undervalued if there is any employer or benefits-broker path, plus physician recruiting. | Cheap, low risk, worth a presence. |
| Newsletter | The only audience you actually own. No platform can remove it. | Physician-authored, agent-assisted. Start early. |
| Podcast circuit | Very high credibility per hour of physician time. | Agent-supported pitching, human delivery. |

# 8. The physician tier menu

Gym memberships: day pass, standard, personal training. Physicians choose what they want to pay for; governance is bundled into all of it and is never optional.

| Tier | Name | What's included | Buyer |
|---|---|---|---|
| 0 | The Stethoscope | Listening only. Dashboards, weekly digest, scored community map, competitor tracking, misinformation radar, reputation monitoring. No posting, near-zero regulatory exposure. | Physician |
| 1 | The Scribe | Everything above, plus drafted responses and content queued for approval. The physician approves, edits, or declines. Nothing publishes without them. | Physician, per seat |
| 2 | The Practice Manager | Owned-channel management, content calendar, repurposing engine, review responses, monthly performance report. | Practice |
| 3 | The Chief of Staff | A dedicated voice agent per physician. Thought leadership, AMA and podcast pitching, personal newsletter, KOL positioning, professional society directory placement. | Physician, premium |
| Base | Governance | Claims library maintenance, prohibited-language filter, adversarial compliance review, escalation routing, audit log, adverse event sentinel. BUNDLED INTO EVERY TIER. | Bundled |
| Add-ons | — | AI visibility and AEO monitoring · paid media agent · 24/7 crisis monitoring · competitor deep-dives · clinical content production | Practice |
| Platform | Inbound Customer Service | Triage router, non-clinical deflection, retention and save flow. Corporate-funded cost center, not a physician option. | WellPeps corporate |

## 8.1 How to price it

Platform fee plus per-seat plus usage, measured per approved item. Tab 10 of the workbook lists the cost drivers behind each tier so you can price against your own basis.

Two structural notes. First, Tier 0 is your sales engine as much as your product — showing a physician what is being said about their specialty in the wild sells the next tier without a pitch. Second, Tier 3 is where physician motivation and economic value point the same direction, which makes it both the highest-margin line and the hardest for a competitor to copy.

> The one pricing rule that matters
>
> Never let a physician buy posting without compliance. The governance layer must be bundled into the base of every tier.
>
> An unbundled program where a doctor buys the megaphone and skips the filter is how this becomes a headline instead of a business — and under LegitScript Standard 3, one such incident can cost every physician on the platform their paid acquisition channels simultaneously.

# 9. Social listening operations

## 9.1 Query architecture

Eight query families, specified with example terms in Tab 08: brand and misspellings; named people; product and program terms; competitors; unbranded category intent; risk terms; gray-market watch; and AI answer engine tracking.

The risk family is the one that justifies the whole program — scam, lawsuit, sued, fraud, class action, side effect, ER, hospitalized, reaction, FDA, FTC, attorney general, reporter, refund, cancel.

## 9.2 Cadence and SLA

| Signal | Cadence | Acknowledgment SLA |
|---|---|---|
| Crisis, adverse event, legal | Real-time alert | 15–60 minutes |
| Purchase intent and complaints | Four sweeps daily | Same business day |
| General mentions | Daily digest | Next business day |
| Trends and share of voice | Weekly report | — |
| Strategic review | Monthly | — |

## 9.3 Coverage hours — decide now

24/7 or business hours. This is a real decision with a real cost, and it should be made deliberately rather than discovered during an incident. GLP-1 discourse peaks at night and on weekends, which is exactly the window most teams do not staff.

## 9.4 Staffing reality

Agents cut cost by roughly 60 to 70 percent, not 100. You will still need approximately one human reviewer per 50 to 100 outbound items per day, a named clinical reviewer on call, and a designated escalation owner. Budget for the humans or the program collapses the first time something goes wrong at eleven o'clock on a Saturday night.

## 9.5 Metrics

- Share of voice and sentiment trend against the competitor set
- Intent-qualified conversations surfaced per week
- Response rate and median time to response
- Escalations caught, by category, with resolution time
- Misinformation corrections made
- Assisted conversions via UTM, promo codes and "how did you hear about us"
- AI answer engine citation rate and sentiment
- First-pass approval rate per agent — the leading indicator of whether the claims library is working

# 10. The regulatory perimeter

This section exists because the research changed the shape of the recommendation. The full watchlist with dates, authorities and severity ratings is Tab 07.

## 10.1 What FDA did

- 3 March 2026 — FDA publicly warned thirty telehealth companies over compounded GLP-1 marketing. Two named violation patterns: claims implying compounded products are equivalent to FDA-approved drugs, and branding a compounded drug under the telehealth firm's own name without disclosing who compounded it. FDA stated it monitors claims across all media platforms — which means community posts, not just advertising.
- Week of 15 June 2026 — twenty-five further warning letters on a misbranding "net impression" theory under FDCA sections 502(a) and 502(n).
- 30 April 2026 — FDA proposed permanently excluding semaglutide, tirzepatide and liraglutide from the 503B bulks list, regardless of future shortages. Ro has already pivoted entirely to branded, FDA-approved GLP-1s. That is the post-crackdown model.
- Peptide vendors — warning letters to Gram Peptides (31 March 2026) and Wholesale Peptide (17 June 2026) establish that "research use only" disclaimers do not defeat intended use inferred from total marketing. Your website copy, social posts, testimonials and any brand-account community post are all evidence.

## 10.2 What FTC did

- FTC v. NextMed (final order December 2025) is the template: $150,000 plus injunctive terms for misrepresenting cost, unsubstantiated weight-loss claims, fake testimonials and manipulated reviews, and negative-option billing violations.
- FTC, Utah and California v. Hims & Hers (filed 29 July 2026) shifted the attack surface from clinical to consumer-protection and data-privacy: sharing sensitive health data with Meta and Snap despite privacy promises, charging consumers immediately on intake-form submission rather than after provider consultation, and hidden cancellation.

> Check this today
>
> If WellPeps charges customers at intake-form submission rather than after a licensed provider reviews them, that is the precise theory now in active federal litigation against the category leader.
>
> Together with the pricing page, testimonial page and cancellation flow, this is the cheapest material exposure to eliminate in the entire program — and it has nothing to do with agents.

## 10.3 The peptide question

Stated plainly, because it outranks everything else in this document.

| Compound | Status as of August 2026 | Consequence |
|---|---|---|
| Tesamorelin | FDA-approved as Egrifta (2010) | Approved finished drug; compounding a copy is separately restricted. Counsel required. |
| PT-141 / bremelanotide | FDA-approved as Vyleesi (2019) | Compounded "PT-141" is not the approved product. Different regulatory analysis. |
| Sermorelin | Formerly approved (Geref, discontinued) | Not settled, not on the 503A list. Do not describe as FDA-approved. |
| BPC-157 / TB-500 | Removed from Category 2 in April 2026; PCAC recommended 8–6–1 in July 2026 — non-binding | NOT on the 503A list. NOT lawfully compoundable today. |
| Ipamorelin | Remains on the Category 2 list; 503A nomination withdrawn | Not compoundable. It was NOT among the twelve peptides freed in April 2026. |
| CJC-1295 | Nomination withdrawn | Not on the list. Not compoundable. |

The distinction that marketing teams keep getting wrong: removal from Category 2 is not authorization. It means FDA no longer flags a significant safety risk; it does not put the substance on the 503A bulks list. FDA must complete notice-and-comment rulemaking first, and its own career scientists opposed all seven substances at the July hearing, finding the studies short in duration, small in sample size, and insufficient to establish safety or effectiveness.

Any marketing implying the July vote legalized these compounds is exactly the misleading-net-impression conduct FDA is currently prosecuting.

## 10.4 LegitScript is the chokepoint

Certification gates advertising on Google, Meta, Microsoft and LinkedIn, and payment processing in several merchant categories. Application is $975 non-refundable plus roughly $2,150 per year per website, on a three-to-six-month timeline. Plan every paid-media launch backwards from that date.

Four standards decide your outcome. Standard 2 prohibits facilitating the prescribing of medications lacking necessary authorization — the direct collision with the peptide menu. Standard 3 disqualifies applicants with recent or repeated warning letters — meaning one FDA letter can cost you every paid channel at once. Standard 8 requires transparent, non-misleading claims. Standard 9 extends to advertising conduct on platforms, which means undisclosed influencer or community seeding is a certification risk even if FDA never notices.

One further item to check before scoping: certification requires a policy prohibiting controlled-substance prescribing. If the Mental Wellness program touches stimulants, that may jeopardize the certification and therefore the entire paid acquisition capability.

# 11. The 90-day sequence

| Weeks | Phase | Work | Gate |
|---|---|---|---|
| 1–3 | Governance | Claims library populated and physician-signed. Prohibited language compiled into filters. Escalation taxonomy with named owners. Disclosure policy. Legal review of the peptide menu and the intake-billing flow. | Nothing deploys until counsel signs |
| 1–3 | Quick wins in parallel | F5Bot free tier live. Reddit Pro and verified business profile applied for. Trustpilot and BBB profiles claimed. Utah safe-harbor policy filed. | All zero-risk, all this month |
| 3–6 | Listen only | Community map validated by a human against live subreddit rules. Baseline metrics. Competitor teardown. AI visibility baseline. | Zero posts published |
| 6–10 | Draft, owned channels | Drafting agents in suggest-mode on WellPeps-owned properties only. Support deflection agent in suggest-mode. Retention flow designed. | 95%+ first-pass approval before advancing |
| 10–16 | Community, carefully | Moderator outreach to r/tirzepatidecompound and r/tressless. ExcelMale sponsorship conversation. Clinical Peptide Society certification and directory listing. | One community at a time |
| 16+ | Scale | Per-physician voice agents. Selective autonomy release on proven non-clinical intents. Tier menu to market. | Never reduce review on clinical content |

Note the ordering. Listening comes before drafting, owned channels come before community channels, and the four legitimate community assets come last — after there is a governance record to point to when a moderator asks who you are.

# 12. Six non-negotiables

- No autonomous posting on clinical topics. Ever. Human approval, permanently, regardless of performance history.
- Always disclose. Agent-drafted, physician-reviewed, brand-affiliated. Reddit requires AI content to be tagged and prohibits content presenting itself as human-generated. The FTC's reviews rule makes undisclosed AI-generated favorable content a per-violation penalty. This is not a gray area and it is not a survivable mistake.
- No PHI, ever. Never confirm in public that anyone is a patient. One non-confirmatory template for every review response; substance moves to a private channel.
- No individualized medical advice. General education plus "talk to a licensed provider." No exceptions for helpfulness.
- Log everything. The audit trail is the product. It is what makes the program defensible and what makes physicians willing to pay for it.
- One account per human, never per agent. No sock puppets, no aged accounts, no agency that supplies them. The Rippling case in March 2026 shows the failure mode is not a quiet ban — it is a pinned moderator post naming your company, which then becomes permanent training data for search engines and AI assistants.

# Appendix A — What could not be verified

Stated plainly so that nothing here is mistaken for settled fact.

- Subreddit rule text. Reddit blocks automated access; every mirror tested was behind a proof-of-work challenge. Almost all rule text in Tab 05 is marked UNVERIFIED. A person with a normal browser can confirm roughly twenty /about/rules/ pages in about an hour. This is the highest-value missing input in the package.
- Some r/loseit and r/xxfitness rule quotes come from a third-party list maintained by a company that sells Reddit accounts and engagement campaigns. The text reads authentically, but the source has an interest. Confirm before writing it into strategy.
- Facebook group sizes, privacy settings and rules could not be retrieved at all — Facebook blocks automated fetching. Six relevant GLP-1 groups were confirmed to exist by URL only.
- GummySearch operating status. Two research passes disagreed: per-subreddit data dated August 2026 was retrievable, but the pricing page reportedly shows a 30 November 2025 shutdown notice. Verify before budgeting for it. Note that most subreddit sizes in this report trace to that source.
- Competitor pricing for Henry Meds, Eden, Mochi, Marek Health, Blokes/Joi and Fountain Life. Nearly every available source was affiliate content with an incentive to distort. Recommend direct intake-flow testing.
- PCAC vote tallies are reported by trade press and counsel commentary but were not confirmed against FDA's own published minutes.
- HIPAA and BAA availability for most listening tools. Sprout Social is the notable exception, and its BAA is narrowly scoped to inadvertent PHI uploaded by a social media user rather than to intentional PHI handling.

# Appendix B — Primary sources

Federal and regulatory

- FDA — FDA Warns 30 Telehealth Companies Against Illegal Marketing of Compounded GLP-1s (3 March 2026)
- FDA — Certain Bulk Drug Substances for Use in Compounding That May Present Significant Safety Risks (Category 2 list, current)
- FDA — Bulk Drug Substances Used in Compounding Under Section 503A of the FD&C Act
- FDA — Pharmacy Compounding Advisory Committee meeting, 23–24 July 2026
- FDA — Warning Letters: Gram Peptides (31 March 2026); Wholesale Peptide (17 June 2026); USApeptide.com (26 February 2025)
- FTC — FTC and States Act Against Hims & Hers over Deceptive, Unlawful Privacy Practices (29 July 2026)
- FTC — Final Order Against Telehealth Provider NextMed (December 2025)
- FTC — Rule on the Use of Consumer Reviews and Testimonials, 16 CFR Part 465
- HHS OCR — enforcement actions on PHI disclosure in online review responses

State AI law

- Orrick — 2026 State Chatbot Laws: Key Provisions and Regulatory Trends
- Holland & Knight — States Continue Efforts to Regulate AI in Healthcare: 2026 Legislation Review
- Transparency Coalition — 14 new state chatbot safety laws enacted in 2026
- California SB 243; New York GBL Article 47; Illinois PA 104-0054 (WOPR Act); Nevada AB 406; Utah HB 452; Tennessee SB 1580; Delaware HB 191; Oregon SB 1546; Idaho SB 1297; Nebraska LB 525

Platform policy

- Reddit Help — Manipulated Content and Misleading Behavior (updated 19 May 2026)
- Reddit Help — Verified profiles on Reddit (updated 9 July 2026); What is Reddit Pro (30 March 2026)
- Reddit — Data API Terms
- Google Ads — Healthcare and Medicines policy
- Meta Transparency Center — Drugs and Pharmaceuticals ad standards
- TikTok Advertising Policies — Healthcare and Pharmaceuticals
- Trustpilot — Guidelines for Businesses (June 2026)
- LegitScript — Healthcare Certification standards and pricing

Academic

- Nobles et al., Examining Peer-to-Peer and Patient-Provider Interactions on a Social Media Community Facilitating Ask the Doctor Services (PMID 32724726)
- Ayers et al., Comparing Physician and Artificial Intelligence Chatbot Responses to Patient Questions Posted to a Public Social Media Forum, JAMA Internal Medicine

Prepared for WellPeps · 17 August 2026 · Not legal advice.
