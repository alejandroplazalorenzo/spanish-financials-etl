-- 008: the assistant's own role, SELECT on curated views only; analysts keep sfetl_reader.
--
-- Why. Until now `ask` connected as sfetl_reader, which can read the raw tables (financial_fact,
-- filing...). In production the assistant's role could read only curated views and a few
-- plain lookup tables, never the raw facts, because that is where a query goes wrong without
-- failing (the wrong filing, a derived figure read as reported). Same rule here:
--   * sfetl_assistant: SELECT on the v_* views and nothing else in public;
--   * sfetl_reader (analysts): the base tables and the views, read-only.
--
-- Every GRANT is explicit and there is no ALTER DEFAULT PRIVILEGES, on purpose: what the
-- assistant can see must be a decision written in a migration, not a side effect of creating
-- a view.
--
-- Lesson from production: a feature once failed for users because one GRANT was missing on a
-- table the assistant wrote to; it was found by pressing the button, not by a test. Hence
-- `tests/integration/test_smoke_intents.py` (run in CI): it connects AS this role, runs every
-- intent of the catalogue and exercises every write the assistant makes to its own schema.
--
-- Both roles are created without a password; `sfetl migrate` enables LOGIN and sets the
-- passwords from SFETL_READER_PASSWORD / SFETL_ASSISTANT_PASSWORD, so no secret is versioned.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sfetl_assistant') THEN
        CREATE ROLE sfetl_assistant NOLOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO sfetl_assistant', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO sfetl_assistant;
GRANT SELECT ON v_company, v_metric, v_financial, v_company_ratios, v_filing,
                v_validation_issue, v_ownership
    TO sfetl_assistant;

-- A connection-level backstop; free SQL additionally runs with a 5 s SET LOCAL timeout.
ALTER ROLE sfetl_assistant SET statement_timeout = '15s';

-- Analysts: the tables recreated by 005/007 lost the grants of 003; restate them explicitly.
GRANT SELECT ON company, fiscal_period, filing, metric, financial_fact, validation_issue,
                ownership,
                v_company, v_metric, v_financial, v_company_ratios, v_filing,
                v_validation_issue, v_ownership
    TO sfetl_reader;
