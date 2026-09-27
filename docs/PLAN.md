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
- **P8** Pulse trends, briefs, language bank.
- **P9** Real collectors: F5Bot/Syften, Apify, Meta Graph read.
- **P10** Meta publish behind flag (off) + "Copy reply & open post".

## 5. Open questions
- Approved claims library content + physician/compliance sign-off.
- Named clinical owner + backup for the 15-min adverse-event SLA (incl. nights/weekends).
- Legal sign-off on Apify scraping; mention-text retention period (default 180 days).
- R38 medication-name rule and LegitScript status (R12) → config toggles.
- API mode: dollar budget instead of subscription quota throttle.
