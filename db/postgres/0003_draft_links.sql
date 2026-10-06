-- ============================================================================
-- WellPeps Pulse: tracked reply links, equivalent to SQLite migration v7
-- (harvey/state.py MIGRATIONS). Apply after 0002; safe to re-run (idempotent).
--
-- * drafts.link_json: the public-registry link a draft carries
--   (harvey/links.link_record: id, label, url, utm_content, utm_term) as JSON
--   text, or NULL. Read by GET /api/analytics/replies.
--
-- No new tables, so RLS, grants and the pulse_app policy from 0001 already
-- cover this column.
-- ============================================================================

alter table pulse.drafts add column if not exists link_json text;

-- ── Version ──────────────────────────────────────────────────────────────

insert into pulse.schema_version (version, description)
values (7, '0003_draft_links: SQLite migration v7')
on conflict (version) do nothing;
