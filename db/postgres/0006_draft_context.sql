-- ============================================================================
-- WellPeps Pulse: context harness, equivalent to SQLite migration v10
-- (harvey/state.py MIGRATIONS). Apply after 0005; safe to re-run (idempotent).
--
-- * drafts.context_json: the Knowledge-library sections the drafter relied on
--   (harvey/harness.py context_records: section_id, doc_id, doc_name, heading)
--   as JSON text, never the section text; NULL when the harness was off or
--   picked nothing. Shown on the review card as "Relied on: ...".
--
-- No new tables, so RLS, grants and the pulse_app policy from 0001 already
-- cover this column; row level security stays on.
-- ============================================================================

alter table pulse.drafts add column if not exists context_json text;

-- ── Version ──────────────────────────────────────────────────────────────

insert into pulse.schema_version (version, description)
values (10, '0006_draft_context: SQLite migration v10')
on conflict (version) do nothing;
