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

**Phase 8 complete.** Harvey's sales functionality is gone. What's here:

- a data layer with mentions, triage, drafts, escalations, and an append-only audit log
- config, plus WellPeps knowledge in `config/*.yaml` (competitors, products, keywords, compliance rules, a seed claims library pending sign-off)
- a deterministic compliance filter for draft replies (`harvey/compliance.py`)
- a collector framework (`harvey/collectors/`) with a JSONL fixture collector, and `pulse ingest --fixture [DIR]` to store mentions with dedupe, run records, and `collected` audit events
- a triage agent (`harvey/agents/triager.py`, prompt in `prompts/triage.md`) that classifies each new mention on a small model, with a deterministic keyword override that forces adverse-event, legal, privacy, and billing-fraud posts to urgent and routes them to `escalated`
- escalations (`harvey/escalation.py`): each urgent mention gets an owner, a 15-minute SLA, and a Slack page (`harvey/notify/slack.py`) that carries a link and a category only, never post text or handles. Every heartbeat sweeps open escalations, re-pages SLA breaches (with the backup owner), and retries pages that didn't go out. `pulse escalations` lists them; `pulse ack <id> --by NAME` acknowledges one
- reply drafting (`harvey/drafting.py`): a drafter (`harvey/agents/drafter.py`, `prompts/draft.md`) writes one reply per reply-appropriate mention using only approved claims (by id); the deterministic compliance filter checks it; an adversarial reviewer (`harvey/agents/reviewer.py`, `prompts/review.md`) looks for rule violations and never rewrites (skipped when the filter is red). Every draft lands in `in_review` with its tier, verdict, and a `drafted → filtered → reviewed` audit trail. Nothing is approved or posted automatically
- a heartbeat loop that sweeps escalations and triages new mentions (both even in quiet hours), drafts when nothing is waiting for triage (not in quiet hours, within budget), and wakes every 5 minutes while an escalation is open
- triage records the `drug` discussed (semaglutide, tirzepatide, BPC-157, ...) separately from a WellPeps `product`, which is set only when the post is about WellPeps
- a signed-in review dashboard (`harvey/dashboard.py`, `harvey/review.py`, `harvey/auth.py`): **Urgent** (open escalations with SLA countdowns, breached first, ack by role), **Review desk** (original post + permalink, triage tags, draft editor, claim chips, compliance tier and reviewer verdict, save/approve/reject, copy-and-open, mark posted, manual escalation; `j`/`k`/`e`/`a` keys), **Feed** (filters, search, detail drawer with the audit trail), **Usage**, **Users** (admin). Approval is refused while the filter is red, the draft cites no claim, or any cited claim is still PENDING sign-off. Pulse still posts nothing: a human copies the approved reply, posts it, and marks it posted
- auth: argon2id passwords, HttpOnly SameSite=Strict session cookies (only a hash of the token is stored, 12 h sliding expiry), a CSRF header on every change, login throttling (5 failures / 15 min per email and per IP), roles viewer / reviewer / clinical / admin, a strict Content-Security-Policy, and a refusal to bind beyond 127.0.0.1 until an admin exists

- **Pulse** market intelligence (`harvey/trends.py`, `harvey/briefs.py`, `harvey/pulse_store.py`), from triaged relevant mentions only and as aggregates only: emerging terms (unigrams to trigrams, velocity against a 28-day baseline, NEW flags), share of voice for WellPeps and competitors, sentiment shift per brand and drug, category and drug mix, complaint themes per competitor, and a **language bank** of verbatim consumer phrases with counts. A daily brief (previous local day, after 07:00) and a weekly brief (previous Mon–Sun, from Monday) are written by the `pulse` agent (sonnet) from the tables alone: never post text, handles or links. Action cards that cite a number not in the tables are stripped; a bad answer gets one retry, then a "tables only" fallback. Slack gets the headline, the top three card titles and a dashboard link. The dashboard's **Pulse** tab shows the brief, its action cards and tables, live trends, and the searchable language bank with Copy buttons. `pulse brief --period daily|weekly [--force]`, `pulse trends --days 7`

Real collectors arrive in a later phase. The full roadmap is in [docs/PLAN.md](docs/PLAN.md).

## Quick start

Requires Python 3.11+ and the `claude` CLI, logged in.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                        # every key is optional for now

.venv/Scripts/python -m pytest -q           # run the tests
pulse status                                # mention counts by status
pulse ingest --fixture                      # load the sample fixture posts
pulse user add you@wellpeps.com --role admin --name "You"   # prompts for a password (min 12)
pulse dashboard                             # http://127.0.0.1:5555, sign in
pulse user list                             # users and roles; `pulse user disable EMAIL`
pulse escalations                           # open escalations and SLA status
pulse ack 3 --by "Nurse Jo"                 # acknowledge escalation #3
pulse trends --days 7                       # emerging terms and share of voice (no Claude call)
pulse brief --period daily                  # write the Pulse brief now (one Claude call)
pulse run                                   # heartbeat loop (triage, drafts, briefs)
```

### Demo data

`scripts/seed_demo.py` fills a throwaway database with **DEMO DATA** (the
fixture posts plus ~35 days of synthetic posts with a few spiking terms, run
through the real pipeline with a deterministic fake brain, then one daily and
one weekly Pulse brief from a deterministic brief writer; no Claude calls, no
Slack):

```bash
export PULSE_DB_PATH=data/demo.db                 # PowerShell: $env:PULSE_DB_PATH="data/demo.db"
.venv/Scripts/python scripts/seed_demo.py
.venv/Scripts/python -m harvey user add demo@pulse.test --role admin --name Demo
.venv/Scripts/python -m harvey dashboard          # http://127.0.0.1:5555
```

Roles: viewer (read), reviewer (edit, approve, reject, copy, mark posted,
escalate, ack non-clinical escalations), clinical (read, ack any escalation
including adverse events), admin (everything, users, heartbeat start/stop).
Serving beyond loopback (`pulse dashboard --host 0.0.0.0`) needs an admin
and HTTPS in front, with `dashboard.secure_cookies: true` in harvey.yaml.

`harvey` is still installed as an alias for `pulse`. Configuration lives in
`harvey.yaml`, and `harvey.local.yaml` (gitignored) overrides it. State is
stored in `data/pulse.db`, or wherever `PULSE_DB_PATH` points.

## Ground rules

- Collectors read public data only, keep only minimal author info, and store a permalink for every mention.
- Claude calls are tool-less text in and text out, through `harvey/brain.py`.
- No autonomous posting. A human approves every reply, with signed-off claims, and posts it by hand.
- The audit log is append-only. SQLite triggers reject UPDATE and DELETE on it.
- Supplier costs never enter this repo or any prompt.

## Credits

Pulse is a fork of [Harvey](https://github.com/ethanplusai/harvey) by Ethan
Rogers, used under the MIT License (see [LICENSE](LICENSE)). The heartbeat
loop, Claude CLI wrapper, usage/quota accounting, and dashboard shell come
from Harvey.
