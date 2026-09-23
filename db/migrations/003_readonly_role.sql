-- 003: read-only role for analysts and for the `ask` module.
-- The role is created without a password; `sfetl migrate` enables LOGIN and sets the
-- password from SFETL_READER_PASSWORD, so no secret is ever versioned.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sfetl_reader') THEN
        CREATE ROLE sfetl_reader NOLOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO sfetl_reader', current_database());
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO sfetl_reader;

-- Explicit, per-object grants: a table added later is NOT readable until a migration says so.
GRANT SELECT ON company, filing, metric, financial_fact, validation_issue,
                company_year, company_year_ratios, year_aggregate_ratios
    TO sfetl_reader;

-- Defence in depth: even if a write slipped through, the session refuses it.
ALTER ROLE sfetl_reader SET default_transaction_read_only = on;
ALTER ROLE sfetl_reader SET statement_timeout = '5s';
