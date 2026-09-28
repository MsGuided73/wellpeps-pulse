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

## 5. Open questions
- Approved claims library content + physician/compliance sign-off.
- Named clinical owner + backup for the 15-min adverse-event SLA (incl. nights/weekends).
- Legal sign-off on Apify scraping; mention-text retention period (default 180 days).
- R38 medication-name rule and LegitScript status (R12) → config toggles.
- API mode: dollar budget instead of subscription quota throttle.
- Minors: the safety screen flags a likely under-18 seeking prescription
  weight-loss or sexual-wellness drugs, which blocks any reply and raises
  urgency to at least high, but there is no `minor` escalation kind, owner,
  or SLA yet. Decide who owns these, whether they page, and any R33 reporting
  duty, then add the kind to `ESCALATION_KINDS`.
