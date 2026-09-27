# WellPeps Pulse — notes for Claude

Pulse is WellPeps' social-listening and compliance-review system. It is a
fork of Harvey (MIT, Ethan Rogers), and the Python package is still named
`harvey`. It collects public mentions, triages them, escalates urgent ones,
and drafts replies that humans review. It is **not** a sales or outreach
tool.

The roadmap and data model are in `docs/PLAN.md`. Read it before changing
behavior. Current status: **Phase 5 complete**. The data layer, config, idle
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
   block it. Corrections go in as new events.

Also:

- No PHI. Slack alerts carry a link and a category only, never post text.
- Collectors read public data only, keep only minimal author info, and
  store a permalink on every mention.
- Escalation handling ignores quiet hours.

## Working here

- Tests: `.venv/Scripts/python -m pytest -q`. Work test-first.
- Imports smoke check: `.venv/Scripts/python -c "import harvey.main, harvey.dashboard, harvey.cli, harvey.state"`
- CLI: `pulse run | dashboard | status | ingest | usage | escalations | ack`
  (`harvey` is an alias).
- The dashboard binds to 127.0.0.1 only until auth lands in Phase 7.
- The DB is `data/pulse.db`, and `PULSE_DB_PATH` overrides it. Schema
  changes are appended to `MIGRATIONS` in `harvey/state.py`. Never edit a
  released migration.
- Mention status changes go through `StateManager.set_mention_status`, which
  enforces the allowed transitions.
