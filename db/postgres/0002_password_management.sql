-- ============================================================================
-- WellPeps Pulse: password management, equivalent to SQLite migration v6
-- (harvey/state.py MIGRATIONS). Apply after 0001; safe to re-run (idempotent).
--
-- * users.must_change_password: set by an admin password reset and, by
--   default, on users an admin creates. While it is set the dashboard serves
--   only /api/me, /api/me/password and /api/logout.
-- * users.password_changed_at: naive UTC, like every other timestamp here.
--
-- No new tables, so RLS, grants and the pulse_app policy from 0001 already
-- cover these columns.
-- ============================================================================

alter table pulse.users add column if not exists must_change_password boolean not null default false;
alter table pulse.users add column if not exists password_changed_at timestamp;

-- ── Version ──────────────────────────────────────────────────────────────

insert into pulse.schema_version (version, description)
values (6, '0002_password_management: SQLite migration v6')
on conflict (version) do nothing;
