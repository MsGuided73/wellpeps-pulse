# Handoff: build an example context harness (Proposal B)

For a coding agent. Build a working, tested **example** of the context harness from the design
doc "Pulse knowledge design: rules intake and context harness" (Proposal B): each agent gets the
few Knowledge-library *sections* relevant to the post in front of it, instead of the first 6,000
characters of the library. Scope is the drafter and the reviewer, behind a setting that is **off by
default**, so nothing changes for live use until the owner turns it on.

## Read first

1. `CLAUDE.md` (hard rules; working conventions).
2. `docs/HANDOFF.md` and `docs/REVISIONS-LOG.md` R-21 (Knowledge library), R-24/R-25 (edit, remove),
   R-26 (Community Agent Voice).
3. `harvey/knowledge_docs.py` (library storage, text extraction, today's `build_reference_block`).
4. `harvey/agents/drafter.py` (`Drafter._reference_block` ~line 448, used ~line 500),
   `harvey/agents/reviewer.py` (`Reviewer.review` ~line 150, `build_prompt`), `harvey/drafting.py`
   (`_draft_one` ~line 355: draft -> filter -> review -> record).
5. `harvey/agents/prompting.py` (nonce-delimited untrusted blocks; single-pass placeholders).

## Environment

- Windows 11; always use the project venv: `.venv\Scripts\python.exe`.
- Tests: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` (baseline 2456 passed, 20
  skipped; keep it green). Work test-first. Tests never call Claude (use the fakes in
  `tests/pulse_helpers.py`).
- Live data is `data/live.db`. **Never write to it.** For any experiment, copy it with
  `sqlite3`'s backup API to a scratch path and set `PULSE_DB_PATH` to the copy.
- Real Claude calls (only for the comparison step) must bill the owner's subscription:
  `PULSE_CLAUDE_BILLING=subscription`. `.env` holds an API key; never let a local run use it. The
  `claude` CLI must be signed in (`claude` then `/login`); run Claude-calling scripts from PowerShell,
  where `claude.exe` is on PATH. `harvey/brain.py` sends prompts on stdin (Windows caps a command
  line at 32,767 characters; the drafting prompt is ~38,000).
- Ask the owner before pushing. Commit in small steps; run the full suite and scan the diff for
  secrets before each commit.

## How it works today (what you are replacing)

- `knowledge_docs.reference_block(state, programs)` reads every **active** document with
  authority `must_follow` or `reference` whose `programs` match (empty = all), orders them
  must-follow first, newest first, and fills `REFERENCE_BUDGET = 6000` characters **in document
  order**, clipping the last one. With ~310,000 characters in the library, the drafter sees the
  opening of one or two documents.
- Only the drafter gets it (appended to its prompt). The reviewer, triage, safety screen and AI
  rewrite never see library text.
- The block is wrapped by `prompting.untrusted_block("REFERENCE_DOCUMENTS", ...)` under a header
  that says documents guide tone and scope only; facts come only from approved claims. Keep that
  header and wrapper exactly.
- Library rows are immutable versions: an edit inserts a new row and supersedes the old one
  (R-24); remove deletes rows (R-25). So a document id never changes its text.
- Extracted text has **no markdown headings** today (docx/pdf extraction gives plain lines). The
  live library: 13 documents, 4 Word files with text, 2 newer PDFs with text (ids 16 "Community
  Agent Voice", 17 "Community Groups and Forums"), and 7 older PDFs stored before the PDF reader
  existed, with **no text** (they need a re-upload; not your job).

## What to build

### 1. `harvey/harness.py` (new, pure, deterministic, no Claude calls)

- `Section` (frozen dataclass): `id` (stable: `f"{doc_id}:{n}:{slug}"`), `doc_id`, `doc_name`,
  `authority`, `kind` (the document's kind), `heading_path` (tuple of headings), `text`,
  `programs`, `medications`, `situations`, `topics`.
- `split_sections(doc: dict) -> list[Section]` for an active document row (with `text`):
  - Markdown headings (`#` to `###`) when present.
  - Otherwise heuristic headings for extracted text: a numbered heading (`1.`, `1.2`, `§3`,
    `Section 4`), an ALL-CAPS line, or a short line (<= 80 chars, no final period) followed by a
    longer paragraph. Keep the rules in one place and test them on real-shaped text.
  - Split any section over ~1,500 characters at paragraph boundaries; merge sections under ~200
    characters into the previous one. A document with no headings is one section per ~1,500
    characters.
  - Cache per `doc_id` (text is immutable per id).
- `tag_section(section, doc) -> Section`: programs from the document's `programs` plus
  `knowledge.programs_in_text(text)`; medications from `knowledge.drug_lookup()`; situations and
  topics from keyword lists in a new `config/harness.yaml` (see 3). Whole-word, case-insensitive,
  like `harvey/insights.py`'s `_keyword_rx`.
- `TaskContext` (frozen dataclass): `agent`, `programs`, `medications`, `situation_id`,
  `category`, `need` (protocol unmet need), `community_id`, `platform`.
- `select(sections, ctx, budget) -> Selection` (ids in order + rendered character count):
  1. **Pinned:** `must_follow` sections whose topic the agent always needs (for the drafter and
     reviewer: `voice`), then other `must_follow` sections that match the context.
  2. **Scored:** remaining sections by matches (program, medication, situation, need, topic),
     must-follow before reference, then newest document, then section order. Deterministic ties.
  3. Fit to `budget` without clipping mid-section; skip a section that does not fit and try the
     next. Never exceed the budget (header and wrapper included, as today).
  4. `example` authority is never selected (unchanged rule).
- `render(selection, nonce) -> str`: the same header and `untrusted_block` as
  `knowledge_docs.build_reference_block`, each entry headed
  `[must_follow: voice_style] Community Agent Voice > Voice principles`.

### 2. Wiring (drafter and reviewer only)

- `harvey.yaml`: new `harness:` block, `enabled: false`, per-agent budgets (drafter 8000,
  reviewer 8000; triage, safety and rewriter listed but unused in this example). Add the config
  model in `harvey/config.py` (`extra="forbid"`, like the others) with tests.
- `Drafter.draft`: when the harness is on, build the `TaskContext` from the mention, triage and
  guidance (`programs_for`, `triage.drug`, `guidance.situation_id`, `triage.unmet_need`, the
  community from `engagement.context_from`), select, render, and append instead of
  `_reference_block`. Off: today's code path, unchanged.
- `DraftProposal` gains `context_sections: tuple[str, ...] = ()` (the selected ids).
- `drafting._review` / `Reviewer.review`: pass the drafter's `context_sections`; the reviewer
  prompt gets the **same sections** rendered under a heading "Reference sections the drafter used
  (guidance, not facts)". Add a placeholder in `prompts/review.md`; with no sections it renders an
  empty string (single-pass placeholders, `prompting.fill`).
- Fail safe: any harness error logs a warning and falls back to today's block for the drafter and
  to no block for the reviewer. Drafting must never fail because of the harness.

### 3. `config/harness.yaml` (new)

- `topics:` id -> keywords, starting with `voice` (voice, tone, style, sound, avoid), `links`
  (link, URL, resource, guide), `safety` (side effect, adverse, emergency, symptom), `community`
  (Reddit, Facebook group, moderator, forum), `process` (assessment, provider review, follow-up),
  `pricing` (price, cost, membership, cancel).
- `situations:` optional extra keywords per `config/engagement_guide.yaml` situation id.
- Loaded through `harvey/knowledge.py` like the other config files (pydantic model in
  `harvey/models/knowledge.py`, `knowledge.reload()` support, a missing file falls back to the
  bundled one). Public, non-sensitive words only (hard rule 1).

### 4. Record what was used

- New column `drafts.context_json` (JSON list of `{section_id, doc_id, doc_name, heading}`, never
  the text). Schema changes go in **both** backends (hard rule 5): append the SQLite migration to
  `MIGRATIONS` in `harvey/state.py` (it becomes migration 10) **and** add
  `db/postgres/0006_draft_context.sql` (idempotent `add column if not exists`, RLS stays on, bumps
  `pulse.schema_version` to 10). `tests/test_postgres_schema.py` checks parity.
- `Draft` model field, `StateManager.add_draft` / reads, and the detail API
  (`review.py`, `latest_draft`) carry it.
- The `drafted` audit event's verdict JSON gets the section ids (append-only log; no text).
- Pulse queue card (`harvey/web/home.js`): one calm line under "Why this post", e.g.
  "Relied on: Community Agent Voice > Voice principles; Competitor Protocol > 7. Disclosure".
  Gray, no new colors; render through `escHtml`; strict CSP (no inline style or handlers).

### 5. Comparison script (`scripts/harness_compare.py`)

On a **copy** of `data/live.db`: for given mention ids, draft with the harness off and on, run the
filter and reviewer, and print per mention the selected sections, both replies, tiers, verdicts and
VOICE notes. Records nothing (call `Drafter.draft`, `drafting._redraft_if_red`, `drafting._review`
directly, as the scratch script did on 2026-10-21). Never touches `data/live.db`. Claude via
subscription only. The owner runs it; you only build and test it with the fake brain.

## Not in this example

Triage, safety screen and rewrite wiring; a UI to edit section tags; converting library documents
to markdown; the intake workflow (Proposal A); catalog sync (Proposal C); email (Proposal D). Note
any of these you think need changes in a short "Follow-ups" section of your final report.

## Hard rules that apply

- Facts, numbers and prices only from approved claims; library text is guidance (keep the header).
- No supplier costs, internal pricing or partner names in config or prompts.
- All Claude calls go through `harvey/brain.py`, tool-less; Python does all I/O.
- Nothing posts automatically. The audit log stays append-only.
- Schema changes in both backends, never editing a released migration file.

## Acceptance

- Harness off: all existing tests pass unchanged and drafter prompts are byte-identical to
  today's for the same inputs (add a test).
- `split_sections`: markdown, numbered, ALL-CAPS and short-line headings; size caps; stable ids;
  a test on text shaped like the real "Community Agent Voice" and the Competitor Protocol PDF.
- `select`: voice pinned for the drafter; a hair-loss mention gets Hair Restoration sections, not
  Weight Loss; budget never exceeded; never clips mid-section; deterministic order; `example`
  never chosen; empty library -> "".
- Reviewer receives exactly the drafter's section ids; reviewer prompt unchanged when there are
  none.
- Migration 10 on SQLite and Postgres parity; `context_json` stored and returned; audit event has
  ids only; no document text in any audit row (test).
- Review card shows the "Relied on" line; CSP rules hold (`tests/test_web_views.py`).
- Prompt-injection safety: a section containing a fake `END_REFERENCE_DOCUMENTS` marker cannot
  close the block (the existing nonce test pattern in `tests/test_knowledge_docs.py`).
- Full suite green; a short entry in `docs/REVISIONS-LOG.md` (R-27, status "Live, off by
  default") and a line in `CLAUDE.md`.
