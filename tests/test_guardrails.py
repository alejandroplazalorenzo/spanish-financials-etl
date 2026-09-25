import pytest

from sfetl.ask.guardrails import UnsafeSQLError, guard_sql


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO v_company (lei, name) VALUES ('X', 'Y')",
        "UPDATE financial_fact SET value = 0",
        "DELETE FROM company",
        "DROP TABLE company",
        "TRUNCATE financial_fact",
        "ALTER TABLE company ADD COLUMN x int",
        "CREATE TABLE x AS SELECT * FROM v_company",
        "GRANT ALL ON company TO PUBLIC",
        "COPY company TO STDOUT",
        "SET ROLE postgres",
        "SELECT 1; DROP TABLE company",
        "SELECT * FROM v_company; SELECT * FROM v_filing",
        "SELECT * INTO backup FROM v_company",
        "SELECT * FROM v_company FOR UPDATE",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * FROM pg_shadow",
        "SELECT * FROM schema_migrations",
        "WITH d AS (DELETE FROM company RETURNING *) SELECT * FROM d",
        # raw tables are not curated views: refused even though they exist
        "SELECT * FROM financial_fact",
        "SELECT * FROM filing",
        "SELECT c.name FROM company c",
        "SELECT * FROM assistant.query_log",
        "SELECT * FROM v_financial WHERE lei IN (SELECT lei FROM ownership)",
        "",
        "this is not sql",
    ],
)
def test_rejects_anything_but_a_single_select_over_curated_views(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT company_name, value FROM v_financial WHERE fiscal_year = 2024",
        "WITH x AS (SELECT lei FROM v_company) SELECT count(*) FROM x",
        "SELECT name FROM v_company UNION SELECT company_name FROM v_financial",
        "SELECT f.company_name FROM v_filing f JOIN v_company c ON c.lei = f.lei",
        "SELECT company_name FROM v_ownership WHERE contradictory;",
        # a keyword inside a literal is data, not a statement (a text filter would refuse it)
        "SELECT company_name FROM v_financial WHERE company_name = 'DELETE'",
    ],
)
def test_accepts_selects_over_curated_views(sql: str) -> None:
    assert guard_sql(sql).upper().startswith(("SELECT", "WITH"))


def test_limit_is_added_when_missing() -> None:
    assert guard_sql("SELECT name FROM v_company", max_rows=50).endswith("LIMIT 50")


def test_large_limit_is_lowered_and_small_one_kept() -> None:
    assert guard_sql("SELECT name FROM v_company LIMIT 100000").endswith("LIMIT 30")
    assert guard_sql("SELECT name FROM v_company LIMIT 5").endswith("LIMIT 5")


def test_order_by_survives_the_rewrite() -> None:
    out = guard_sql("SELECT company_name FROM v_financial ORDER BY value DESC NULLS LAST")
    assert "ORDER BY value DESC NULLS LAST LIMIT 30" in out
