"""Least privilege, proved with the real roles: what each one can and cannot do."""

from __future__ import annotations

import psycopg
import pytest
from dbhelpers import connect

from sfetl.config import DbSettings

pytestmark = pytest.mark.integration


def test_reader_reads_tables_and_views_but_cannot_write(loaded: dict[str, DbSettings]) -> None:
    with connect(loaded["reader"]) as conn:
        assert conn.execute("SELECT count(*) FROM financial_fact").fetchone()[0] > 0  # type: ignore[index]
        assert conn.execute("SELECT count(*) FROM v_financial").fetchone()[0] > 0  # type: ignore[index]
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM company")
        conn.execute("SET default_transaction_read_only = off")  # a session can switch it off...
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # ...the grants still refuse
            conn.execute("DELETE FROM financial_fact")


@pytest.mark.parametrize(
    "table", ["financial_fact", "filing", "company", "fiscal_period", "ownership", "metric"]
)
def test_assistant_cannot_read_raw_tables(loaded: dict[str, DbSettings], table: str) -> None:
    with connect(loaded["assistant"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"SELECT count(*) FROM {table}")  # type: ignore[arg-type]


def test_rls_trap_zero_rows_on_a_granted_base_table_data_through_the_view(
    loaded: dict[str, DbSettings],
) -> None:
    """With RLS on and no policy, a GRANT is not enough: the table looks EMPTY, no error.
    The curated view runs as its owner and keeps returning rows."""
    with connect(loaded["owner"]) as owner:
        owner.execute("GRANT SELECT ON financial_fact TO sfetl_assistant")  # a mistaken grant
        base_rows = owner.execute("SELECT count(*) FROM financial_fact").fetchone()[0]  # type: ignore[index]
    with connect(loaded["assistant"]) as conn:
        assert conn.execute("SELECT count(*) FROM financial_fact").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM v_financial").fetchone() == (base_rows,)
    with connect(loaded["reader"]) as conn:  # the analysts' role has a policy
        assert conn.execute("SELECT count(*) FROM financial_fact").fetchone() == (base_rows,)


def test_no_default_privileges_a_new_view_stays_invisible(loaded: dict[str, DbSettings]) -> None:
    with connect(loaded["owner"]) as owner:
        owner.execute("CREATE VIEW v_new AS SELECT 1 AS x")
    with connect(loaded["assistant"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM v_new")


def test_assistant_writes_only_what_it_needs_in_its_schema(loaded: dict[str, DbSettings]) -> None:
    with connect(loaded["assistant"]) as conn:
        query_id = conn.execute(
            "INSERT INTO assistant.query_log (session_id, question, mode) "
            "VALUES ('t', 'q', 'intent') RETURNING query_id"
        ).fetchone()[0]  # type: ignore[index]
        conn.execute("UPDATE assistant.query_log SET rating = -1 WHERE query_id = %s", (query_id,))
        for statement in (
            "UPDATE assistant.query_log SET question = 'rewritten'",
            "UPDATE assistant.query_log SET mode = 'free_sql'",
            "DELETE FROM assistant.query_log",
            "INSERT INTO metric (code, label, statement, category, unit, period_type, sort_order, "
            "description) VALUES ('x', 'x', 'cash_flow', 'x', 'EUR', 'duration', 1, 'x')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO assistant.query_log (session_id, question, mode) "
                "VALUES ('t', 'q', 'guess')"
            )
