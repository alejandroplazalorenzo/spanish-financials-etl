"""Schema, loading, idempotence, collisions, ownership and post-load checks on real PostgreSQL."""

from __future__ import annotations

import copy
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from dbhelpers import FIXTURE_NAMES, connect, load_results

from sfetl.concepts import METRICS
from sfetl.config import MIGRATIONS_DIR, DbSettings
from sfetl.load import load_catalog, load_filing, resolve_ownership
from sfetl.migrate import MigrationError, apply_migrations
from sfetl.ownership import GleifParent, GleifRecord
from sfetl.validate import validate
from sfetl.validate_db import FailedFiling, GoldenFigure, RunContext, run_db_checks

pytestmark = pytest.mark.integration


def test_migrations_apply_once_and_are_recorded(fresh_db: DbSettings) -> None:
    with connect(fresh_db, autocommit=False) as conn:
        first = apply_migrations(conn)
        second = apply_migrations(conn)
        recorded = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert first == [p.name[:3] for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert second == []
    assert [r[0] for r in recorded] == first


def test_editing_an_applied_migration_is_refused(fresh_db: DbSettings, tmp_path: Path) -> None:
    for path in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(path, tmp_path / path.name)
    with connect(fresh_db, autocommit=False) as conn:
        apply_migrations(conn, tmp_path)
        edited = tmp_path / "002_ratio_views.sql"
        edited.write_text(edited.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
        with pytest.raises(MigrationError):
            apply_migrations(conn, tmp_path)


def test_upgrade_from_the_first_published_schema(fresh_db: DbSettings, tmp_path: Path) -> None:
    """A database created with 001-003 (and holding their data) migrates forward cleanly."""
    for path in sorted(MIGRATIONS_DIR.glob("00[123]_*.sql")):
        shutil.copy(path, tmp_path / path.name)
    with connect(fresh_db, autocommit=False) as conn:
        assert apply_migrations(conn, tmp_path) == ["001", "002", "003"]
        with conn.transaction():
            conn.execute("INSERT INTO company (lei, name) VALUES ('LEI00000000000000001', 'OLD')")
        assert apply_migrations(conn)[0] == "004"
        views = {r[0] for r in conn.execute("SELECT viewname FROM pg_views").fetchall()}
    assert "year_aggregate_ratios" not in views
    assert {"v_financial", "v_ownership", "v_company"} <= views


def test_metric_table_matches_the_python_catalogue(migrated: DbSettings) -> None:
    with connect(migrated) as conn:
        rows = conn.execute(
            """SELECT m.code, m.label, m.statement, m.category, m.unit, m.period_type, p.code
               FROM metric m LEFT JOIN metric p ON p.metric_id = m.parent_id
               ORDER BY m.sort_order"""
        ).fetchall()
    expected = [
        (s.code, s.label, s.statement, s.category, s.unit, s.period_type, s.parent) for s in METRICS
    ]
    assert rows == expected


def _snapshot(conn: psycopg.Connection) -> dict[str, list[tuple]]:
    queries = {
        "company": "SELECT lei, name, is_financial FROM company ORDER BY lei",
        "period": "SELECT c.lei, fiscal_year, period_start, period_end, months FROM fiscal_period"
        " JOIN company c USING (company_id) ORDER BY 1, 2",
        "filing": "SELECT source_filing_id, fxo_id, numeric_facts FROM filing ORDER BY 1",
        "fact": "SELECT lei, fiscal_year, metric_code, value, is_nil, source_concept"
        " FROM v_financial ORDER BY 1, 2, 3",
        "issue": "SELECT fxo_id, rule, metric_code, detail FROM v_validation_issue"
        " ORDER BY 1, 2, 3, 4",
        "ownership": "SELECT lei, relation, source, parent_name, resolution FROM v_ownership"
        " ORDER BY 1, 2, 3",
    }
    return {k: conn.execute(q).fetchall() for k, q in queries.items()}  # type: ignore[arg-type]


def test_load_is_idempotent(migrated: DbSettings, transformed) -> None:
    results = [transformed(n) for n in FIXTURE_NAMES]
    load_results(migrated, results)
    with connect(migrated) as conn:
        before = _snapshot(conn)
    load_results(migrated, results)
    with connect(migrated) as conn:
        after = _snapshot(conn)
    assert before == after
    assert len(before["fact"]) == sum(len(r.metrics) for r in results)
    assert len(before["issue"]) == len(validate(results).issues) > 0


def test_nil_with_a_value_is_impossible(migrated: DbSettings, transformed) -> None:
    load_results(migrated, [transformed("endesa_2024.json")])
    with connect(migrated) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE financial_fact SET is_nil = true WHERE value IS NOT NULL")


def test_a_second_filing_for_the_same_year_is_counted_not_silently_merged(
    migrated: DbSettings, transformed
) -> None:
    original = transformed("endesa_2024.json")
    other = copy.deepcopy(original)
    other.meta = replace(
        other.meta,
        filing_id=original.meta.filing_id + 100000,
        fxo_id=original.meta.fxo_id + "-copy",
    )
    first_value = original.metrics["revenue"].value
    other.metrics["revenue"] = replace(other.metrics["revenue"], value=Decimal(1))
    with connect(migrated) as conn:
        ids = load_catalog(conn)
        load_filing(conn, original, [], ids)
        second = load_filing(conn, other, [], ids)
        revenue = conn.execute(
            "SELECT value FROM v_financial WHERE metric_code = 'revenue'"
        ).fetchall()
    assert second.facts_inserted == 0
    assert second.facts_not_inserted == len(original.metrics)
    assert original.meta.fxo_id in second.warnings[0]
    assert revenue == [(first_value,)]  # the existing value was kept (DO NOTHING)


def test_a_failing_filing_leaves_nothing_behind(migrated: DbSettings, transformed) -> None:
    result = transformed("endesa_2024.json")
    with connect(migrated) as conn:
        ids = load_catalog(conn)
        broken = {k: v for k, v in ids.items() if k != "cash"}  # fails after the upserts
        with pytest.raises(KeyError):
            load_filing(conn, result, [], broken)
        counts = [
            conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # type: ignore[index]
            for t in ("company", "fiscal_period", "filing", "financial_fact")
        ]
    assert counts == [0, 0, 0, 0]


def test_derived_figures_are_flagged_not_reported(migrated: DbSettings, transformed) -> None:
    load_results(migrated, [transformed("amper_2024.json"), transformed("bankinter_2024.json")])
    with connect(migrated) as conn:
        rows = dict(
            conn.execute(
                """SELECT company_name, is_reported FROM v_financial
                   WHERE metric_code = 'total_liabilities'"""
            ).fetchall()
        )
    # Amper does not tag total liabilities (derived from its components); Bankinter does
    assert rows == {"AMPER, S.A.": False, "BANKINTER SOCIEDAD ANONIMA": True}


def test_ratios_are_per_company_and_null_without_revenue(migrated: DbSettings, transformed) -> None:
    load_results(migrated, [transformed("endesa_2024.json"), transformed("bankinter_2024.json")])
    with connect(migrated) as conn:
        ratios = dict(
            conn.execute("SELECT company_name, net_margin FROM v_company_ratios").fetchall()
        )
    endesa = transformed("endesa_2024.json")
    assert ratios["ENDESA SA"] == round(
        endesa.metrics["net_profit"].value / endesa.metrics["revenue"].value, 4
    )
    assert ratios["BANKINTER SOCIEDAD ANONIMA"] is None  # banks present no revenue


def test_ownership_resolves_by_name_and_flags_self_references(
    migrated: DbSettings, transformed
) -> None:
    load_results(migrated, [transformed(n) for n in FIXTURE_NAMES])
    with connect(migrated) as conn:
        cash = conn.execute(
            """SELECT relation, parent_company_name, resolution, contradictory
               FROM v_ownership WHERE company_name = 'PROSEGUR CASH, S.A.' AND source = 'esef'
               ORDER BY relation"""
        ).fetchall()
        selfs = conn.execute(
            "SELECT count(*) FROM v_ownership WHERE contradiction = 'self_reference'"
        ).fetchone()
        search = conn.execute(
            "SELECT name FROM v_company WHERE search_text ILIKE '%inditex%'"
        ).fetchall()
    assert cash[0][1:] == ("PROSEGUR COMPAÑIA DE SEGURIDAD, S.A.", "name", False)
    assert selfs is not None and selfs[0] > 0
    assert search == [("INDUSTRIA DE DISEÑO TEXTIL, S.A.",)]  # found by its brand


def test_cycles_are_marked_contradictory(migrated: DbSettings, transformed) -> None:
    load_results(
        migrated, [transformed("prosegur_2024.json"), transformed("prosegur_cash_2024.json")]
    )
    parent_lei = transformed("prosegur_2024.json").meta.lei
    cash_lei = transformed("prosegur_cash_2024.json").meta.lei
    # a (synthetic) register entry saying the parent is owned by its subsidiary
    gleif = {parent_lei: GleifRecord(parent_lei, [GleifParent("direct", cash_lei, "X", None)])}
    with connect(migrated) as conn:
        resolve_ownership(conn, gleif)
        rows = conn.execute(
            "SELECT DISTINCT contradiction FROM v_ownership WHERE parent_in_dataset"
        ).fetchall()
    assert rows == [("cycle",)]


def _context(results, failed=()) -> RunContext:
    return RunContext(
        loaded_fxo_ids=[r.meta.fxo_id for r in results],
        failed=list(failed),
        warnings=[],
        non_eur_only=[r.meta.fxo_id for r in results if not r.metrics],
        unmapped_rows=0,
        unmapped_revenue_like=0,
    )


def test_post_load_checks_are_green_on_a_clean_load(migrated: DbSettings, transformed) -> None:
    results = [transformed(n) for n in FIXTURE_NAMES]
    load_results(migrated, results)
    endesa = results[0]
    golden = [
        GoldenFigure(
            endesa.meta.lei,
            "ENDESA SA",
            2024,
            "revenue",
            "20.935",
            1_000_000,
            Decimal("20935000000"),
            "income statement",
            "fixture",
        ),
    ]
    with connect(migrated) as conn:
        checks = run_db_checks(conn, _context(results), golden)
    assert checks.ok, [c for c in checks.checks if not c.ok]
    assert any("ENDESA SA FY2024 revenue" in c.name for c in checks.checks)


def test_post_load_checks_turn_red(migrated: DbSettings, transformed) -> None:
    names = ["endesa_2024.json", "prosegur_2024.json", "prosegur_cash_2024.json"]
    results = [transformed(n) for n in names]
    load_results(migrated, results)
    wrong = [
        GoldenFigure(
            results[0].meta.lei,
            "ENDESA SA",
            2024,
            "revenue",
            "21.000",
            1_000_000,
            Decimal("21000000000"),
            "x",
            "x",
        )
    ]
    failed = [FailedFiling("X-2024-12-31-ESEF-ES-0", "load", "boom")]
    with connect(migrated) as conn:
        checks = run_db_checks(conn, _context(results, failed), wrong)
    red = {c.section for c in checks.checks if not c.ok}
    assert red == {
        "Failed filings",
        "Golden figures (read by hand from the published XHTML reports)",
    }
