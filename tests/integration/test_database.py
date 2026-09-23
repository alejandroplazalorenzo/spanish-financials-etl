"""Integration tests against a real PostgreSQL. Skipped when the server is not reachable.

Each test creates a throw-away database on the configured server and drops it afterwards.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from sfetl.config import MIGRATIONS_DIR, DbSettings, owner_db
from sfetl.load import load
from sfetl.migrate import MigrationError, apply_migrations, set_reader_password
from sfetl.validate import validate

pytestmark = pytest.mark.integration

FIXTURE_NAMES = [
    "endesa_2024.json",
    "amper_2024.json",
    "realia_2024.json",
    "bankinter_2024.json",
    "inditex_fy2024.json",
]


@pytest.fixture(scope="module")
def admin() -> DbSettings:
    settings = owner_db()
    try:
        with psycopg.connect(**{**settings.connect_kwargs(), "connect_timeout": 3}) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError as err:
        pytest.skip(f"PostgreSQL not available: {str(err).splitlines()[0]}")
    return settings


@pytest.fixture
def fresh_db(admin: DbSettings) -> Iterator[DbSettings]:
    name = f"sfetl_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(**admin.connect_kwargs(), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield replace(admin, dbname=name)
    finally:
        with psycopg.connect(**admin.connect_kwargs(), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def connect(settings: DbSettings) -> psycopg.Connection:
    return psycopg.connect(**settings.connect_kwargs())  # type: ignore[arg-type]


def test_migrations_apply_once_and_are_recorded(fresh_db: DbSettings) -> None:
    with connect(fresh_db) as conn:
        first = apply_migrations(conn)
        second = apply_migrations(conn)
        recorded = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert first == [p.name[:3] for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert second == []
    assert [r[0] for r in recorded] == first


def test_editing_an_applied_migration_is_refused(fresh_db: DbSettings, tmp_path: Path) -> None:
    for path in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(path, tmp_path / path.name)
    with connect(fresh_db) as conn:
        apply_migrations(conn, tmp_path)
        edited = tmp_path / "002_ratio_views.sql"
        edited.write_text(edited.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
        with pytest.raises(MigrationError):
            apply_migrations(conn, tmp_path)


def _snapshot(conn: psycopg.Connection) -> dict[str, list[tuple]]:
    queries = {
        "company": "SELECT lei, name, is_financial FROM company ORDER BY lei",
        "filing": "SELECT filing_id, fiscal_year, numeric_facts FROM filing ORDER BY filing_id",
        "fact": "SELECT lei, fiscal_year, metric, value_eur, source_concept FROM financial_fact "
        "ORDER BY 1, 2, 3",
        "issue": "SELECT filing_id, rule, metric, detail FROM validation_issue ORDER BY 1, 2, 3, 4",
    }
    return {k: conn.execute(q).fetchall() for k, q in queries.items()}  # type: ignore[arg-type]


def test_load_is_idempotent(fresh_db: DbSettings, transformed) -> None:
    results = [transformed(name) for name in FIXTURE_NAMES]
    report = validate(results)
    with connect(fresh_db) as conn:
        apply_migrations(conn)
        stats = load(conn, results, report)
        before = _snapshot(conn)
        load(conn, results, report)
        after = _snapshot(conn)
    assert before == after
    assert stats.facts == len(before["fact"]) == sum(len(r.metrics) for r in results)
    assert len(before["issue"]) == len(report.issues) > 0


def test_ratio_views_use_ratio_of_sums(fresh_db: DbSettings, transformed) -> None:
    results = [transformed(name) for name in FIXTURE_NAMES]
    with connect(fresh_db) as conn:
        apply_migrations(conn)
        load(conn, results, validate(results))
        company = dict(
            conn.execute(
                "SELECT company_name, net_margin FROM company_year_ratios WHERE fiscal_year = 2024"
            ).fetchall()
        )
        aggregate = conn.execute(
            "SELECT net_margin FROM year_aggregate_ratios "
            "WHERE fiscal_year = 2024 AND NOT is_financial"
        ).fetchone()
    non_fin = [r for r in results if not r.is_financial and "revenue" in r.metrics]
    revenue = sum(r.metrics["revenue"].value for r in non_fin)
    profit = sum(r.metrics["net_profit"].value for r in non_fin)
    assert aggregate is not None
    assert aggregate[0] == round(profit / revenue, 4)
    average_of_ratios = sum(company[r.meta.entity_name] for r in non_fin) / len(non_fin)
    assert aggregate[0] != round(average_of_ratios, 4)  # the two methods really differ here
    assert company["BANKINTER SOCIEDAD ANONIMA"] is None  # banks have no revenue


def test_reader_role_can_read_but_not_write(fresh_db: DbSettings) -> None:
    password = os.environ.get("SFETL_READER_PASSWORD")
    if not password:
        pytest.skip("SFETL_READER_PASSWORD not set")
    with connect(fresh_db) as conn:
        apply_migrations(conn)
        set_reader_password(conn, password)
    reader = replace(fresh_db, user="sfetl_reader", password=password)
    with connect(reader) as conn:
        conn.autocommit = True
        assert conn.execute("SELECT count(*) FROM metric").fetchone() == (10,)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("INSERT INTO metric VALUES ('x', 'x', 'balance_sheet', 'instant', 'x')")
        # a session can switch read-only off; the grants still refuse the write
        conn.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM financial_fact")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM schema_migrations")


def test_value_types_survive_the_round_trip(fresh_db: DbSettings, transformed) -> None:
    results = [transformed("endesa_2024.json")]
    with connect(fresh_db) as conn:
        apply_migrations(conn)
        load(conn, results, validate(results))
        row = conn.execute(
            "SELECT value_eur, decimals FROM financial_fact WHERE metric = 'revenue'"
        ).fetchone()
    assert row == (Decimal("20935000000.00"), -6)
