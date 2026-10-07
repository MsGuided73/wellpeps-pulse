-- ============================================================================
-- WellPeps Pulse: rules of engagement + competitor / switching protocol,
-- equivalent to SQLite migration v8 (harvey/state.py MIGRATIONS).
-- Apply after 0003; safe to re-run (idempotent).
--
-- * triage.subtype: the situation inside a triage category that decides how
--   Pulse engages under WellPeps' rules of engagement (dose_question,
--   media_inquiry, emergency, ...; harvey/models/mention.py TRIAGE_SUBTYPES,
--   config/engagement_guide.yaml, docs/RULES-OF-ENGAGEMENT.md). '' = none.
-- * triage.protocol_decision / protocol_route / opportunity_score /
--   protocol_json: the classification, escalation route, opportunity score
--   (NULL when gated) and structured record of the Competitor Mentions and Provider Switching Protocol
--   (harvey/protocol.py). '' / NULL / '{}' outside the protocol's scope.
--   protocol_json never holds post text.
--
-- No new tables, so RLS, grants and the pulse_app policy from 0001 already
-- cover these columns.
-- ============================================================================

alter table pulse.triage add column if not exists subtype text not null default '';
alter table pulse.triage add column if not exists protocol_decision text not null default '';
alter table pulse.triage add column if not exists protocol_route text not null default '';
alter table pulse.triage add column if not exists opportunity_score integer;
alter table pulse.triage add column if not exists protocol_json text not null default '{}';

-- ── Version ──────────────────────────────────────────────────────────────

insert into pulse.schema_version (version, description)
values (8, '0004_engagement_protocol: SQLite migration v8')
on conflict (version) do nothing;
