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
  middleware), `pulse user add|list|disable|reset-password`, `scripts/seed_demo.py`
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

- Feed home + Analytics: Feed is the default tab (no hash -> #feed); tabs and
  Feed/Analytics filters live in the URL hash (app.js routing, harvey/web/feed.js).
  `harvey/analytics.py` + `GET /api/analytics/<chart>` (options, summary, volume,
  share, sentiment, complaints, emerging, drugs, escalations): aggregates only,
  local-day buckets in the quiet-hours timezone, <= 366 days, MIN_COUNT 2
  privacy floor, sentiment points need 3. Charts are hand-rolled SVG
  (`harvey/web/charts.js`, no vendored library); tab UI in `harvey/web/analytics.js`.

- Slack routing + #pulse-query bot: escalation pages use `notify.slack_webhook_env`
  (`SLACK_WEBHOOK_URL`, #pulse-alerts); briefs use `SlackNotifier.for_briefs`
  (`notify.slack_briefs_webhook_env` = `SLACK_BRIEFS_WEBHOOK_URL`, #pulse-briefs, falling
  back to the alerts webhook) from the heartbeat, `pulse brief` and the dashboard
  (`get_briefs_notifier`). `harvey/notify/slack.py` redacts webhook URLs, registered
  secrets and any `xox?-`/`xapp-` token shape (`REDACTOR`, `install_redaction`).
  `harvey/slackbot/` is the read-only bot (`pulse slackbot`, Bolt AsyncApp +
  AsyncSocketModeHandler, compose service `slackbot`): `planner` (haiku,
  `slackbot.plan`, question nonce-delimited -> pydantic `QuerySpec`, invalid -> help;
  action verbs -> "use the dashboard" without a model call), `executor` (existing
  `analytics`/`pulse_store` code only, no model SQL; counts < 2 -> null, sentiment
  needs 3; unknown competitors/drugs dropped), `answer` (sonnet, `slackbot.answer`,
  sees `QueryResult.payload()` only, never the question), `guards` (strip sentences with
  numbers not in the payload -> templated fallback; scrub handles/URLs except the
  dashboard/e-mails/quotes >= 8 words; 120 words, 1500 chars), `audit` (`actions`
  rows `slack_query` / `slack_query_refused`, agent `slackbot:<user>`, limits from
  `slack_query:` in harvey.yaml), `app` (`QueryBot.handle_mention` holds the logic;
  `build_bolt_app` is thin). Only `app_mention` in `SLACK_QUERY_CHANNEL_ID`; elsewhere
  one "I only answer in #pulse-query" reply; DMs ignored. No tokens -> logs "Slack
  query bot disabled (no tokens)" and idles; heartbeat `settings.slackbot_heartbeat_at`
  every minute (`pulse health --slackbot`). `pulse slack-test` sends a [TEST]
  message per webhook and never prints URLs. Channel IDs live in env only.
  Manifest: `docs/slack-app-manifest.yaml`; setup: DEPLOY doc section 9.

- Conversion playbook (2026-10-06): `config/links.yaml` (public links registry,
  `live:` flags flipped by hand), `harvey/links.py` (UTM `tracked_url`,
  registry matching, `pulse links check`), claim `link_id`,
  `config/reply_examples.yaml` (PENDING few-shot style guidance), the
  purchase-intent/question playbook in `prompts/draft.md` (claim selection in
  `drafter.candidate_claims` / `guide_claim`), deterministic disclosure rules
  (`disclosure:` in compliance_rules.yaml: first sentence must disclose),
  link rules in the filter (outside registry / unbacked -> red, not live ->
  yellow + approval blocker), migration v7 (`drafts.link_json`,
  `db/postgres/0003_draft_links.sql`), `harvey/reply_analytics.py` +
  `GET /api/analytics/replies` (CSV export) and `harvey/web/replies.js`.

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
  The #pulse-query bot answers from aggregates only and never takes actions.
- Collectors read public data only, keep only minimal author info, and
  store a permalink on every mention.
- Escalation handling ignores quiet hours.

## Working here

- Tests: `.venv/Scripts/python -m pytest -q`. Work test-first.
- Imports smoke check: `.venv/Scripts/python -c "import harvey.main, harvey.dashboard, harvey.cli, harvey.state"`
- CLI: `pulse run | dashboard [--host H] | status | health [--worker] |
  ingest | usage | escalations | ack | user add|list|disable|reset-password | brief |
  trends | slackbot | slack-test | links check` (`harvey` is an alias); `health --slackbot`.
- Deploy: `docker-compose.yml` (Coolify: `worker` + `dashboard` + `slackbot`, env only,
  no bind mounts; `docker-compose.local.yml` is the laptop override). Env
  `PULSE_SECURE_COOKIES` / `PULSE_DASHBOARD_URL` / `PULSE_TRUSTED_PROXIES`
  override harvey.yaml in `load_config`; `PULSE_REQUIRE_POSTGRES=true` makes
  `StateManager()`/`from_env` refuse SQLite. `GET /healthz` is public and
  returns only `{"ok": ...}`. Use `dashboard.request_ip()` (harvey/netutil.py),
  never `request.client.host`, for anything security-related. The heartbeat
  stamps `settings.heartbeat_at` every cycle (`pulse health --worker`).
- Passwords (migration v6, `db/postgres/0002_password_management.sql`): header
  user menu -> Change password (`POST /api/me/password`; wrong current
  password counts toward the login throttle; other sessions end, CSRF
  rotates); admin Users tab -> Reset password (`POST /api/users/reset-password`,
  not for yourself; ends all their sessions); `pulse user reset-password`.
  Resets and admin-created users set `users.must_change_password` (opt out:
  `--no-force-change` / the Users-tab checkbox); while set, every /api route
  except /api/me, /api/me/password, /api/logout is 403
  `{"error": "password_change_required"}` and the UI shows only the change
  screen (`harvey/web/password.js`). The env bootstrap admin is not flagged
  (its operator chose that password; see harvey/auth.py). Password events go to
  the `actions` table (`password_changed` / `password_reset`), never with
  password material.
- The dashboard binds to 127.0.0.1 by default; `--host` anything else is
  refused until an active admin exists. Every /api route except /api/login
  needs a session; every POST needs X-CSRF-Token. Approval needs a non-red
  filter result, publishable claims (`review.require_publishable_claims`) and
  no registry link that is still `live: false`.
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

## Local dev sign-in bypass

`PULSE_DEV_NO_AUTH=true` (local `.env` only) skips sign-in for loopback
requests as a synthetic `dev@localhost` admin. It is ignored when
`PULSE_REQUIRE_POSTGRES` is on, and `pulse dashboard` refuses a non-loopback
`--host` while it is set. Never add it to docker-compose or Coolify.

## Local DEMO sandbox (client demos only)

`PULSE_DEMO_SANDBOX=true` serves fictional community pages at `/sandbox`
(`harvey/sandbox/`: guard in `__init__`, permalinks in `urls`, own SQLite file
`PULSE_SANDBOX_DB_PATH` / data/sandbox.db in `store` -- deliberately NOT the Pulse
schema, so no migration; routes in `routes`; content in `demo_content` /
`seeding`; UI in `harvey/web/sandbox/`, served only via the guarded
`/sandbox/assets/`). Same guard as dev_no_auth: loopback peers only, ignored with
`PULSE_REQUIRE_POSTGRES`, non-loopback bind refused; otherwise every /sandbox route
404s. Never add it to docker-compose or Coolify. No real platform names, logos or
trade dress in the sandbox (a test greps `harvey/web/sandbox/*`); fictional handles
only. `scripts/seed_demo.py --sandbox` puts every demo mention in a sandbox thread
(comment ids live in the path: url_norm drops fragments) and writes
`data/demo-config/` (claims "DEMO (not signed off)", links live, marker `demo.yaml`
-> `knowledge.is_demo_config()` -> DEMO CONFIG banner). `GET /api/demo` gives the UI
its flags; `POST /api/mentions/{id}/demo-post` (approved only, sandbox on) posts the
approved text as the brand account and marks it posted. `review.mark_posted` refuses
a sandbox link unless the sandbox is on for the request. Launcher: `scripts/run_demo.ps1`.

