# WellPeps Pulse — Refocus Plan

Fork of Harvey (MIT, ethanplusai/harvey) refocused from cold-email sales to
**social listening, compliant reply drafting, and market-trend intelligence** for
WellPeps (US telehealth: GLP-1s, peptides, hair restoration, sexual wellness).

Governance source: *WellPeps_Agent_Program_Gameplan* (17 Aug 2026) — listening
first, drafting human-in-the-loop, no autonomous posting on clinical topics ever,
no PHI, audit everything, "no claim ID, no publish".

Knowledge inputs: `C:\dev\hyperagents\wellpeps\registry\` (competitors, products,
keywords, reply-compliance-rules R1–R39). **Supplier costs in products.md must
never enter this repo or any prompt.**

## Decisions (2026-09-27)
- Harvey-based service owns scheduling, Claude calls, data, approvals. Hermes OS
  may later get a **read-only** status/brief hook; it never approves or posts.
- Prototype on Claude subscription (`claude -p`); switch to a WellPeps
  `ANTHROPIC_API_KEY` before launch. Claude calls stay tool-less.
- Keep package name `harvey` through build; optional mechanical rename later.
- Slack alerts carry **link + category only** (no post text) to limit PHI spread.
- Escalation/urgent handling ignores quiet hours; urgent-capable sources tick every 5 min.
- Independent safety-screen pass on health-related mentions (defense against
  prompt-injected escalation suppression): a second, narrow haiku call
  (`harvey/agents/safety_screen.py`, `prompts/safety_screen.md`) on every
  mention that triage did not escalate and that names a medication, product,
  or health term. Adverse event / self-harm -> escalate; minor -> no reply,
  urgency >= high; unparseable -> no reply, urgency >= high, no page.
  Toggle: `triage.safety_screen` (default on).
- A severe triage category (adverse_event, legal_regulatory, privacy,
  billing_fraud) always escalates, at any model urgency; `escalation_kind` is
  the single source of truth. A viral negative is paged but stays `triaged`.
- Category-level discussion in WellPeps' markets (GLP-1, peptides, hair,
  sexual wellness, hormones/TRT) is relevant even when no brand is named;
  misinformation there is relevant with no reply by default.
- A red compliance-filter draft is re-prompted once with the hit reasons;
  at most 2 drafter calls per mention. Both attempts are audited.
- Mention text is capped at 20,000 chars (title 500) at ingest; the mentions
  list API returns a 2,000-char preview.
- Triage keeps `drug` (generic/category term discussed) apart from `product`
  (a WellPeps SKU, only when the post is about WellPeps).
- Phase 7 auth: argon2id (argon2-cffi); sessions are sha256(token) rows with
  a per-session CSRF token and 12 h sliding expiry; login throttle is
  in-memory (single dashboard process). Roles: viewer, reviewer, clinical
  (ack any escalation, no draft actions: separation of duties), admin.
  Approval needs a non-red filter, >= 1 claim, and (default) publishable
  claims; editing an approved reply voids the approval (approved -> in_review).

- Storage: SQLite stays the default and the test backend. Production data
  goes to the Supabase project **wellpeps-pulse** (us-east-1, Postgres 17),
  in a dedicated `pulse` schema with RLS on and no anon/authenticated
  access. The schema is not exposed through the Data API. It is applied
  from `db/postgres/*.sql`, never by the app. The app picks Postgres when
  `PULSE_DATABASE_URL` is set, and `tests/test_postgres_schema.py` keeps
  the two schemas in step.

## 1. Module fate
| Module | Fate |
|---|---|
| main.py | ADAPT — keep quiet hours, sleep, backoff, gather isolation; new `decide_next_action` |
| brain.py | KEEP — rewrite SYSTEM_PROMPT; add `usage.backend: cli|api` later |
| integrations/quota.py, usage.py | KEEP |
| state.py | ADAPT — keep connect/migrations/settings/runs/actions/usage_events; new schema |
| gate.py | ADAPT → compliance.py |
| config.py | ADAPT — drop persona/product/ICP/channels/sales env keys |
| dashboard.py + web/* | ADAPT — keep shell, usage, start/stop/logs; Outbox desk → Review desk |
| cli.py | ADAPT — keep run/dashboard/status/usage |
| agents/* , collectors/discover+profile, pipeline, export, signals, sales integrations, trainer, setup, sales models, prompts/*, skills/* | REMOVE |
| Dockerfile, compose, pyproject | ADAPT — drop playwright/dnspython/aiosmtplib; add auth deps |

## 2. Data model (fresh `data/pulse.db`, new migration v1)
Keep: settings, runs, actions, usage_events.
- **sources** — collector, config_json, enabled, owned, last_cursor, last_run_at
- **mentions** — source_id, platform, external_id, url (NOT NULL), url_norm, author_handle,
  parent_external_id, text, title, lang, posted_at, collected_at, engagement_json,
  owned_channel, status (new→triaged→drafted→in_review→approved/rejected/posted/dropped/escalated), run_id.
  UNIQUE(platform, external_id) where external_id != '' ; UNIQUE(url_norm)
- **triage** — mention_id PK, relevant, subject_type, subject, competitor, product, category,
  sentiment, urgency, urgency_reason, reply_appropriate, phrases_json, model, created_at
- **escalations** — mention_id, kind, owner, notified_at, sla_due_at, acked_at, acked_by, breached
- **drafts** — mention_id, version, text, claim_ids_json, model, filter_ok, filter_hits_json,
  review_verdict (pass|reject|needs_human), review_reasons_json, tier, created_at; UNIQUE(mention_id, version)
- **audit_log** — append-only (trigger blocks UPDATE/DELETE): mention_id, draft_id, event, actor,
  claim_ids_json, filter_result_json, verdict_json, final_text, permalink, at
- **briefs**, **trend_terms**, **language_bank**, **users**, **sessions**

## 3. Heartbeat
1. escalation sweep (always; re-page unacked past SLA)
2. due collectors (no Claude calls)
3. triage batch (haiku) — urgent → escalation + Slack immediately; deterministic keyword override
4. draft (stronger model) → compliance filter → adversarial reviewer → in_review
5. daily/weekly Pulse brief
6. idle
Steps 4–6 respect quiet hours and budget; 1–3 do not (triage has budget priority).

## 4. Phases (TDD — tests first each phase)
- **P1** Strip sales; new models/state/config; stub heartbeat; trim cli/dashboard. Green tests; no sales refs.
- **P2** Knowledge YAML (`config/`) + `compliance.py` filter; supplier-cost leak test.
- **P3** Collector framework + fixture collector + ingest (dedupe, runs).
- **P4** Triage agent (FakeBrain tests; keyword urgency override).
- **P5** Escalation + Slack notifier (no-op without webhook).
- **P6** Drafter + adversarial reviewer; end-to-end fixture test.  *(Blocked for real use on a compliance-signed claims.yaml.)*
- **P7** Auth + dashboard (Urgent, Feed, Review desk, Pulse, Usage, Settings).
- **P8** Pulse trends, briefs, language bank. *(done)*
- **P9** Real collectors: F5Bot/Syften, Apify, Meta Graph read.
- **P10** Meta publish behind flag (off) + "Copy reply & open post".

## Phase 8 decisions (2026-09-27)
- Trends are deterministic (no Claude): terms counted once per mention;
  unigrams to trigrams within a clause, never across a stopword or dropped
  token; velocity = (window/day + 0.5) / (baseline/day + 0.5) over a 28-day
  baseline; score = velocity × log(1 + count); a sub-term that only appears
  inside a ranked longer term is dropped. Only triaged, relevant, not-dropped
  mentions count; a mention's time is `posted_at`, else `collected_at`.
- Briefs: daily = previous local day, weekly = previous Mon–Sun, local to
  `usage.quiet_hours.timezone` (no separate Pulse timezone). One brief per
  (period, window_start); the heartbeat builds the daily after
  `pulse.daily_brief_hour`, the weekly from `pulse.weekly_day` on (catch-up
  through Sunday) once the daily is done, and never for an empty window.
- The brief prompt carries aggregates only, and nothing seen in fewer than 2
  mentions (terms, complaint themes, language-bank phrases): a one-post
  anecdote could point at its author. Phrases with a link, handle or e-mail
  never enter the prompt.
- 1–7 action cards are accepted (the prompt asks for 3–7) so a thin week
  doesn't force the fallback; extra cards are cut at 7. Cards citing numbers
  absent from the payload are stripped and logged.
- Slack gets the headline + top 3 card titles + dashboard link, with URLs,
  handles, e-mails and any banked phrase scrubbed out.
- Migration v5 rebuilds the unused v1 `briefs`, `trend_terms`,
  `language_bank` tables (never written before Phase 8) and adds
  `language_bank_mentions` so banking is idempotent per mention.

## Slack decisions (2026-10-06)
- Three private channels in the DCB Consulting workspace: escalation pages
  -> #pulse-alerts (`SLACK_WEBHOOK_URL`), briefs -> #pulse-briefs
  (`SLACK_BRIEFS_WEBHOOK_URL`, falling back to the alerts webhook), questions
  -> #pulse-query. Channel IDs are env/config only, never code.
- The #pulse-query bot is **read-only**: it never approves, acks, posts,
  escalates or edits; action requests get a pointer to the dashboard, where
  the audit log records them. No interactivity, no slash commands.
- **Socket Mode** (outbound WebSocket, app-level token with
  connections:write): no public URL, no request signing surface, no inbound
  port on the server. Its own compose service (`slackbot`) so a Slack outage
  or crash never touches the heartbeat or dashboard; without tokens it idles
  healthily.
- **Aggregates only**: the plan model (haiku) only picks one of ten fixed
  intents plus validated filters (pydantic `QuerySpec`); the deterministic
  analytics code runs it (no model-written SQL); the answer model (sonnet)
  sees the aggregate result only, never the question or any mention text.
  Privacy floor as in Phase 8 (counts < 2 hidden, sentiment needs 3).
  Answers are numerically verified (invented numbers stripped, templated
  fallback) and scrubbed of handles, links (except the dashboard), e-mails
  and quotes of 8+ words.
- Scopes: app_mentions:read, chat:write, reactions:write, incoming-webhook.
  groups:read was considered and left out: the bot never looks channels up.
- Only `app_mention` events. Elsewhere one short "I only answer in
  #pulse-query" reply; DMs are ignored (no Messages tab, no im scopes).
- Limits: 100 answered questions per local day, 20 per user per rolling
  hour (`slack_query:`); refusals don't count. Counted in the existing
  `actions` table (no schema change): `slack_query` / `slack_query_refused`
  rows, `agent = slackbot:<user id>`. The question is stored truncated to 200
  characters with links/handles/e-mails stripped, for audit.
- Quiet hours don't apply to the bot (on-demand, staff-initiated).

## Conversion playbook decisions (2026-10-06)
- **Playbook** (`prompts/draft.md`): for `purchase_intent` and `question`,
  (1) disclosure as the first sentence, (2) a general, provider-neutral answer
  (what to ask any provider), (3) ONE WellPeps fact from a claim, (4) at most
  one guide link through a guide claim for the program discussed, (5) the
  provider-determines close. One CTA, no hype/urgency, no prices unless a
  claim states one, no medication names while R38 is on, Reddit under 90
  words. The drafter always offers disclosure, the program's guide claim,
  provider checklist, `CLM-PRICE-FOLLOWUP` and provider-determines first for
  those categories; triage `drug` maps to a program through products.yaml
  (tirzepatide/semaglutide -> Weight Loss guide, minoxidil/finasteride ->
  Hair, tadalafil/sildenafil -> Sexual Wellness, sermorelin/glutathione/NAD+
  -> Healthy Aging; the NAD+ guide names the molecule, so R38 skips it).
  Few-shot examples in `config/reply_examples.yaml` (PENDING, style only;
  each must pass the filter apart from "link not live yet").
- **Disclosure is deterministic** (`disclosure:` in compliance_rules.yaml):
  every Pulse reply is a brand reply, so a first sentence without an approved
  form ("I work with WellPeps", ...) is RED (R3); third-person / customer-voice
  talk about WellPeps without it is RED too, with its own R2 reason. The
  reviewer also checks undisclosed affiliation, astroturfing tone and link
  relevance.
- **Links registry** (`config/links.yaml`): https only, `allowed_domains`
  (wellpeps.com), no query/fragment, no sign-up/checkout paths. A claim may
  name one link (`link_id`). The filter: link outside the registry -> red;
  registry link not backed by a cited claim's `link_id` -> red; `live: false`
  -> yellow "link not live yet"; `max_links: 1` stays.
- **Link-not-live approval blocker**: approval is refused while the draft
  carries a registry link with `live: false`; the review desk shows a
  "Tracked link" chip with a live/not-live badge and the utm_content.
  `pulse links check` (HEAD, GET fallback, 10 s, redirects followed) records
  the result in `settings.links_check` and prints which `live:` flags to
  flip; it never edits links.yaml.
- **UTM scheme** (`harvey/links.py`, applied at draft time and shown in the
  draft so humans copy the full URL): `utm_source=<platform>`,
  `utm_medium=social_reply`, `utm_campaign=pulse`, `utm_content=m<mention id>`,
  `utm_term=<subreddit>` when known. Other query params are kept, utm keys
  never duplicated. Words inside our own registry URLs are masked before the
  pattern scan (a drug-named subreddit in utm_term is not an R38 hit).
  Stored per draft in `drafts.link_json` (migration v7,
  `db/postgres/0003_draft_links.sql`).
- **Measurement**: `GET /api/analytics/replies` (+ `format=csv`) and the
  Analytics "Replies & links" card: approved/posted replies by platform and
  by link, and the utm_content list. To match conversions in Google
  Analytics 4: Explore -> dimension "Session manual ad content"
  (utm_content), filter session campaign = `pulse`, export, and join on the
  CSV's `utm_content` (Shopify: the same UTM values on the order's landing
  session / customer journey). No external calls from Pulse.

## Rules of engagement decisions (2026-10-07)
- **Source of truth**: `docs/RULES-OF-ENGAGEMENT.md`. Priority (binding user
  instruction 2026-10-07): the Competitor Mentions and Provider Switching
  Protocol V1 for its domain, then the other WellPeps Community Engagement
  documents, then earlier rules only where those are silent (retained
  safeguards listed for WellPeps to confirm; superseded rules listed with the
  overriding source).
- **Situations** (`config/engagement_guide.yaml`): first match on category,
  subtype, subject, protocol decision and route decides draft / approved
  boundary reply (verbatim guide wording, no model call) / stop / no reply.
  The draftable query is the same list compiled to a SQL CASE. Adverse events
  in any thread get the guide's boundary reply for a clinical approver plus
  the page; complaints about WellPeps get Template C.
- **Protocol** (`harvey/protocol.py`): triage supplies intent tags, the need
  and two 0-2 judgement scores; Pulse runs the decision sequence (access ->
  safety -> clinical -> intent -> facts -> record), scores opportunity only
  after the gates (0-8; 6+ high, 3-5 moderate), and stores the decision,
  route, score and record (no post text) on triage (migration v8,
  `db/postgres/0004_engagement_protocol.sql`). Unknown community rules HOLD
  competitor / switching posts; the protocol's ESCALATE route pages even when
  the public decision is HOLD. The review desk recomputes the decision before
  approval and sorts safety first.
- **80/20** is a planning metric only (review-desk chip, Analytics card,
  `GET /api/analytics/engagement-mix`), never a per-reply gate.
- **R38 superseded**: medication names allowed for general education
  (yellow); the compounded-equivalence rules stay red.
- **Clinical approval**: new permission `approve_clinical` (clinical, admin);
  reviewers cannot approve adverse-event / emergency replies, clinical users
  can approve only those.
- **One reply per thread**: a thread with a WellPeps reply waiting for review
  gets no second draft (Protocol §9).

## 5. Open questions
- Everything in the "FINALIZE — needed from WellPeps" checklist of
  `docs/RULES-OF-ENGAGEMENT.md`, especially the go-live blockers (protocol
  approval, named escalation contacts, after-hours and failed-handoff
  fallback, community rules, listening access, data retention, the
  regression run) and the approval blockers (support channel, care routing,
  pricing, lab terms, gated-download disclosure).
- WellPeps to confirm the "Retained earlier safeguards" table.
- Compliance sign-off for the new claims (`CLM-PRICE-FOLLOWUP`,
  `CLM-PRICE-ALLIN`, `CLM-EDU-*`) and the reply examples (all PENDING).
  `CLM-PRICE-ALLIN` says standard shipping is included (site PRICE_NOTE) but
  products.yaml `membership.shipping_note` says shipping is shown at
  checkout, and the membership price text predates the one-monthly-price
  model: confirm which is current before approving.
- Flip the guide links to `live: true` in config/links.yaml once the site's
  guide pages are deployed (they 404 as of 2026-10-06) and `pulse links check`
  is green. Until then approval of any reply with a guide link is blocked.
- R38 decided 2026-10-07: superseded by the guidelines (names allowed for
  general education, yellow for review); the NAD+ guide is offered for NAD+.
- Approved claims library content + physician/compliance sign-off.
- Named clinical owner + backup for the 15-min adverse-event SLA (incl. nights/weekends).
- Legal sign-off on Apify scraping; mention-text retention period (default 180 days).
- LegitScript status (R12) → config toggle `allow_certification_claims`.
- API mode: dollar budget instead of subscription quota throttle.
- Slack query bot: confirm with compliance that storing the first 200
  characters of staff questions in `actions` is acceptable, and remind staff
  not to type patient details into Slack.
- Minors: the safety screen flags a likely under-18 seeking prescription
  weight-loss or sexual-wellness drugs, which blocks any reply and raises
  urgency to at least high, but there is no `minor` escalation kind, owner,
  or SLA yet. Decide who owns these, whether they page, and any R33 reporting
  duty, then add the kind to `ESCALATION_KINDS`.
