# WellPeps Pulse

Social listening, compliant reply drafting, and market-trend intelligence for
WellPeps (US telehealth: GLP-1s, peptides, hair restoration, sexual wellness).

Pulse collects public mentions of WellPeps, competitors, and product
categories. It triages them and escalates urgent ones (adverse events,
legal/regulatory) to a named clinical owner. For mentions a human decides to
answer, it drafts a reply that must pass a compliance filter, an adversarial
reviewer, and a human reviewer before anyone posts it. Every step is written
to an append-only audit log.

Pulse never posts on its own, and nothing clinical is ever auto-published.

## Status

**Phase 1 skeleton.** Harvey's sales functionality is gone. What's here:

- a data layer with mentions, triage, drafts, escalations, and an append-only audit log
- config
- a heartbeat loop that starts, logs, and idles
- a local dashboard with a mention feed and Claude usage

Collectors, triage, drafting, compliance, escalation paging, auth, and briefs
arrive in later phases. The full roadmap is in [docs/PLAN.md](docs/PLAN.md).

## Quick start

Requires Python 3.11+ and the `claude` CLI, logged in.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                        # every key is optional for now

.venv/Scripts/python -m pytest -q           # run the tests
pulse status                                # mention counts by status
pulse dashboard                             # http://127.0.0.1:5555 (loopback only)
pulse run                                   # heartbeat loop (idles in Phase 1)
```

`harvey` is still installed as an alias for `pulse`. Configuration lives in
`harvey.yaml`, and `harvey.local.yaml` (gitignored) overrides it. State is
stored in `data/pulse.db`, or wherever `PULSE_DB_PATH` points.

## Ground rules

- Collectors read public data only, keep only minimal author info, and store a permalink for every mention.
- Claude calls are tool-less text in and text out, through `harvey/brain.py`.
- No autonomous posting. A human approves every reply.
- The audit log is append-only. SQLite triggers reject UPDATE and DELETE on it.
- Supplier costs never enter this repo or any prompt.

## Credits

Pulse is a fork of [Harvey](https://github.com/ethanplusai/harvey) by Ethan
Rogers, used under the MIT License (see [LICENSE](LICENSE)). The heartbeat
loop, Claude CLI wrapper, usage/quota accounting, and dashboard shell come
from Harvey.
