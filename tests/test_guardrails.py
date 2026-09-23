import pytest

from sfetl.ask.guardrails import UnsafeSQLError, guard_sql
from sfetl.ask.llm import extract_sql


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO company (lei, name) VALUES ('X', 'Y')",
        "UPDATE financial_fact SET value_eur = 0",
        "DELETE FROM company",
        "DROP TABLE company",
        "TRUNCATE financial_fact",
        "ALTER TABLE company ADD COLUMN x int",
        "CREATE TABLE x AS SELECT * FROM company",
        "GRANT ALL ON company TO PUBLIC",
        "COPY company TO STDOUT",
        "SET ROLE postgres",
        "SELECT 1; DROP TABLE company",
        "SELECT * FROM company; SELECT * FROM filing",
        "SELECT * INTO backup FROM company",
        "SELECT * FROM company FOR UPDATE",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * FROM pg_shadow",
        "SELECT * FROM schema_migrations",
        "WITH d AS (DELETE FROM company RETURNING *) SELECT * FROM d",
        "",
        "this is not sql",
    ],
)
def test_rejects_anything_but_a_single_read_only_select(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT company_name, revenue FROM company_year WHERE fiscal_year = 2024",
        "WITH x AS (SELECT lei FROM company) SELECT count(*) FROM x",
        "SELECT name FROM company UNION SELECT company_name FROM company_year",
        "SELECT c.name FROM company c JOIN filing f ON f.lei = c.lei WHERE f.fiscal_year = 2024",
        "SELECT sum(net_profit) FILTER (WHERE revenue > 0) / sum(revenue) FROM company_year;",
    ],
)
def test_accepts_selects_over_allowed_relations(sql: str) -> None:
    assert guard_sql(sql).upper().startswith(("SELECT", "WITH"))


def test_limit_is_added_when_missing() -> None:
    assert guard_sql("SELECT name FROM company", max_rows=50).endswith("LIMIT 50")


def test_large_limit_is_lowered_and_small_one_kept() -> None:
    assert guard_sql("SELECT name FROM company LIMIT 100000", max_rows=200).endswith("LIMIT 200")
    assert guard_sql("SELECT name FROM company LIMIT 5", max_rows=200).endswith("LIMIT 5")


def test_order_by_survives_the_rewrite() -> None:
    out = guard_sql("SELECT company_name FROM company_year ORDER BY revenue DESC NULLS LAST")
    assert "ORDER BY revenue DESC NULLS LAST LIMIT 200" in out


def test_sql_is_taken_from_a_fenced_block() -> None:
    reply = "Here you go:\n```sql\nSELECT 1\n```\nThis counts rows."
    assert extract_sql(reply) == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
