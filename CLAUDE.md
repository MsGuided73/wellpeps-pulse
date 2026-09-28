# WellPeps Pulse — notes for Claude

Pulse is WellPeps' social-listening and compliance-review system. It is a
fork of Harvey (MIT, Ethan Rogers), and the Python package is still named
`harvey`. It collects public mentions, triages them, escalates urgent ones,
and drafts replies that humans review. It is **not** a sales or outreach
tool.

The roadmap and data model are in `docs/PLAN.md`. Read it before changing
behavior. Current status: **Phase 8 complete**. The data layer, config, idle
heartbeat, minimal dashboard, knowledge files (`config/*.yaml`) and the
deterministic compliance filter (`harvey/compliance.py`) exist.
- Phase 3: collector registry + JSONL fixture collector (`harvey/collectors/`)
  and `harvey/ingest.py` (`pulse ingest --fixture [DIR]`).
- Phase 4: triage agent (`harvey/agents/triager.py`, `prompts/triage.md`)
  with the `knowledge.urgent_override` safety net, wired into the heartbeat.
- Phase 5: `harvey/escalation.py` (escalate / sweep / ack, SLA from
  `escalation.sla_minutes`) and `harvey/notify/slack.py`. Every Slack
  message is built by `escalation.build_page`; keep it link + category only.
  The sweep runs every cycle; `pulse escalations`, `pulse ack`.
- Phase 6: drafter (`harvey/agents/drafter.py`, `prompts/draft.md`),
  adversarial reviewer (`harvey/agents/reviewer.py`, `prompts/review.md`),
  shared rule list `prompts/reply_rules.md`, and `harvey/drafting.py`
  (draft -> compliance filter -> reviewer -> `in_review`). Prompt plumbing
  (nonce delimiters, single-pass `{{placeholders}}`) is in
  `harvey/agents/prompting.py`. The reviewer must never run on haiku.
  Real use is blocked until `config/claims.yaml` is compliance-signed.
- Hardening after review/live test: an independent safety screen
  (`harvey/agents/safety_screen.py`, `prompts/safety_screen.md`, agent
  `safety`) re-checks health-related mentions triage didn't escalate
  (`triage.safety_screen` in harvey.yaml); every severe category escalates
  (`escalation_kind` is the source of truth); red drafts get one rewrite
  (max 2 drafter calls); the draft prompt's "never write" list comes from
  `examples:` in `config/compliance_rules.yaml` (each must match its rule).

- Pre-7 fix: triage has `drug` (generic/category term discussed,
  normalized via `knowledge.drug_lookup`); `product` (a WellPeps SKU) is
  set only when the mention is about WellPeps.
- Phase 7: `harvey/auth.py` (argon2id passwords, sha256-hashed session
  tokens in an HttpOnly SameSite=Strict cookie, per-session CSRF header,
  in-memory login throttle, roles viewer/reviewer/clinical/admin in
  `PERMISSIONS`, admin bootstrap from env, bind guard), `harvey/review.py`
  (urgent/feed/detail reads; edit/approve/reject/copied/mark-posted/manual
  escalate), `harvey/dashboard.py` (routes + auth/CSRF/security-header
  middleware), `pulse user add|list|disable`, `scripts/seed_demo.py`
  (DEMO DATA into a throwaway `PULSE_DB_PATH`). The UI (`harvey/web/`)
  runs under a strict CSP: no inline script/style, no `on*=` handlers;
  render server data only through `escHtml`/`safeHref`.

- Phase 8: `harvey/trends.py` (deterministic: tokenizer, term velocity,
  share of voice, sentiment shift, mixes, complaint themes, language bank
  upsert), `harvey/pulse_store.py` (its SQL), `harvey/briefs.py` (windows
  local to `usage.quiet_hours.timezone`, the `pulse` agent call, pydantic
  validation + one retry + "tables only" fallback, numeric verification of
  action cards, idempotent per (period, window_start), Slack message built
  only by `build_brief_message`), `prompts/brief.md`, config `pulse:`,
  migration v5 (briefs / trend_terms / language_bank rebuilt,
  language_bank_mentions), `pulse brief` / `pulse trends`, the dashboard
  Pulse tab (`harvey/web/pulse.js`) and its API. The brief prompt carries
  aggregates only: never mention text, handles, URLs or mention ids, and
  nothing seen in fewer than 2 mentions. Heartbeat priority: triage > draft >
  brief > idle; briefs respect quiet hours and the budget.

Later phases add everything else. Don't build ahead of the phase you've
been asked to do.

## Hard rules (never break these)

1. **Never put supplier costs in prompts.** The same goes for margins and
   internal pricing, and for any file that ends up in a prompt
   (`prompts/`, `skills/`, config fed to agents). The knowledge registry's
   `products.md` holds supplier costs, so never copy it wholesale.
2. **All Claude calls stay tool-less.** Every call goes through
   `harvey/brain.py`, which uses `--tools ""`, a strict MCP config, and no
   setting sources. Python does all I/O. The model only reads text and
   returns text.
3. **No autonomous posting.** Pulse never publishes a reply without an
   explicit human approval that is recorded in the audit log. Clinical
   topics are never auto-published under any flag.
4. **The audit log is append-only.** Don't add code paths that UPDATE or
   DELETE `audit_log` rows, and don't weaken or drop the triggers that
   block it (SQLite and Postgres). Corrections go in as new events.
5. **Schema changes go in both backends.** Append the SQLite migration to
   `MIGRATIONS` in `harvey/state.py` *and* add a new numbered file in
   `db/postgres/` (`0002_...sql`, idempotent, RLS on, bumps
   `pulse.schema_version` to `len(MIGRATIONS)`). Never edit a released file
   on either side. `tests/test_postgres_schema.py` enforces table, column and
   index parity.

Also:

- No PHI. Slack alerts carry a link and a category only, never post text.
- Collectors read public data only, keep only minimal author info, and
  store a permalink on every mention.
- Escalation handling ignores quiet hours.

## Working here

- Tests: `.venv/Scripts/python -m pytest -q`. Work test-first.
- Imports smoke check: `.venv/Scripts/python -c "import harvey.main, harvey.dashboard, harvey.cli, harvey.state"`
- CLI: `pulse run | dashboard [--host H] | status | ingest | usage |
  escalations | ack | user add|list|disable | brief | trends` (`harvey` is
  an alias).
- The dashboard binds to 127.0.0.1 by default; `--host` anything else is
  refused until an active admin exists. Every /api route except /api/login
  needs a session; every POST needs X-CSRF-Token. Approval needs a non-red
  filter result and publishable claims (`review.require_publishable_claims`).
  Nothing posts automatically: "copied" / "mark posted" only record what a
  human did by hand.
- The DB is `data/pulse.db`, and `PULSE_DB_PATH` overrides it. Setting
  `PULSE_DATABASE_URL` (env or `.env`) switches to Postgres, the Supabase
  `pulse` schema. See `db/postgres/README.md`. Tests always run on SQLite
  (`tests/conftest.py` blanks the URL). Postgres tests are
  `-m postgres` with a disposable `PULSE_TEST_DATABASE_URL`.
- SQL is written once in portable SQLite form: `?` params, `RETURNING id`
  (no `lastrowid`), `TRUE`/`FALSE` and Python bools for flags,
  `ON CONFLICT ... DO UPDATE` with table-qualified columns, and time cutoffs
  from `harvey.db.dialect.sql_utc` (no `datetime('now')`). `harvey/db/dialect.py`
  translates it for Postgres and rejects SQLite-only SQL on both backends.
- Mention status changes go through `StateManager.set_mention_status`, which
  enforces the allowed transitions.
