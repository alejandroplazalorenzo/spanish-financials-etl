-- 010: row-level security on the base tables, with a policy for analysts only.
--
-- Why. Defence in depth, and a trap worth demonstrating because production hit it:
--   * with RLS enabled and no policy for a role, a GRANT SELECT is not enough: the role sees
--     the table EMPTY, with no error. In production this surfaced while verifying the
--     assistant's queries against the real database: granted tables came back with 0 rows;
--   * views run with the privileges of their owner, and the owner of these tables bypasses RLS
--     (it is not FORCEd), so the curated views keep returning data to the assistant.
--
-- Result here:
--   * sfetl_reader (analysts) has a SELECT policy on every base table: unchanged behaviour;
--   * sfetl_assistant has no grant and no policy on base tables. If a future migration GRANTs
--     it one by mistake, it still reads 0 rows. It reads data only through the v_* views.
-- tests/integration/test_roles.py proves both: 0 rows on a base table even after a GRANT, and
-- rows through the view.
--
-- The loader connects as the owner, which bypasses RLS, so loading is unaffected.

ALTER TABLE company          ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_period    ENABLE ROW LEVEL SECURITY;
ALTER TABLE filing           ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric           ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_fact   ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_issue ENABLE ROW LEVEL SECURITY;
ALTER TABLE ownership        ENABLE ROW LEVEL SECURITY;

CREATE POLICY reader_select_company          ON company          FOR SELECT TO sfetl_reader USING (true);
CREATE POLICY reader_select_fiscal_period    ON fiscal_period    FOR SELECT TO sfetl_reader USING (true);
CREATE POLICY reader_select_filing           ON filing           FOR SELECT TO sfetl_reader USING (true);
CREATE POLICY reader_select_metric           ON metric           FOR SELECT TO sfetl_reader USING (true);
CREATE POLICY reader_select_financial_fact   ON financial_fact   FOR SELECT TO sfetl_reader USING (true);
CREATE POLICY reader_select_validation_issue ON validation_issue FOR SELECT TO sfetl_reader USING (true);
CREATE POLICY reader_select_ownership        ON ownership        FOR SELECT TO sfetl_reader USING (true);
