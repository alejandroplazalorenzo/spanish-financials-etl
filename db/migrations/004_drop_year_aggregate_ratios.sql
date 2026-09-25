-- 004: remove the cross-company "ratio of sums" view (year_aggregate_ratios).
--
-- Why. Migration 002 created year_aggregate_ratios: one margin per fiscal year computed over
-- all non-financial companies, as a ratio of their sums. That design did not come from the
-- production system this project rebuilds, and the assistant was being taught to answer with
-- it. It is withdrawn.
--
-- The principle that did come from production is narrower and replaces it:
--   * a derived figure is never presented as a reported one. The curated view v_financial
--     (migration 006) carries is_reported = false for any value computed here instead of
--     tagged by the filer (source_concept 'derived:...');
--   * when a figure cannot be taken from a filing it stays NULL; it is not estimated from
--     something else. In production the same rule meant showing NULL rather than an
--     individual company's figure dressed up as a group figure.
--
-- 002 is not edited: its checksum is recorded in schema_migrations and an edited migration is
-- refused by the runner. Fix forward, as here.

DROP VIEW IF EXISTS year_aggregate_ratios;
