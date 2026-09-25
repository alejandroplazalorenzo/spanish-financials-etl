"""Helpers shared by the integration tests (imported by name: this folder is on sys.path)."""

from __future__ import annotations

import psycopg

from sfetl.config import DbSettings
from sfetl.load import load_catalog, load_filing, resolve_ownership
from sfetl.transform import FilingResult
from sfetl.validate import validate

FIXTURE_NAMES = [
    "endesa_2024.json",
    "amper_2024.json",
    "realia_2024.json",
    "bankinter_2024.json",
    "inditex_fy2024.json",
    "prosegur_2024.json",
    "prosegur_cash_2024.json",
    "berkeley_2024.json",
]


def connect(settings: DbSettings, autocommit: bool = True) -> psycopg.Connection:
    return psycopg.connect(**settings.connect_kwargs(), autocommit=autocommit)  # type: ignore[arg-type]


def load_results(settings: DbSettings, results: list[FilingResult]) -> None:
    report = validate(results)
    with connect(settings) as conn:
        ids = load_catalog(conn)
        for r in results:
            load_filing(conn, r, report.issues_for(r.meta.filing_id), ids)
        resolve_ownership(conn, {})
