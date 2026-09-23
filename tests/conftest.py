from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from sfetl.extract import FilingMeta
from sfetl.transform import FilingResult, transform_filing

FIXTURES = Path(__file__).parent / "fixtures"


def _metas() -> dict[str, FilingMeta]:
    rows = json.loads((FIXTURES / "filings.json").read_text(encoding="utf-8"))
    out: dict[str, FilingMeta] = {}
    for row in rows:
        name = row.pop("fixture")
        out[name] = FilingMeta(
            **{
                **row,
                "period_end": date.fromisoformat(row["period_end"]),
                "date_added": datetime.fromisoformat(row["date_added"]),
            }
        )
    return out


@pytest.fixture(scope="session")
def fixture_meta() -> dict[str, FilingMeta]:
    return _metas()


@pytest.fixture(scope="session")
def load_report() -> Callable[[str], dict[str, Any]]:
    def _load(name: str) -> dict[str, Any]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture(scope="session")
def transformed(
    fixture_meta: dict[str, FilingMeta], load_report: Callable[[str], dict[str, Any]]
) -> Callable[[str], FilingResult]:
    def _run(name: str) -> FilingResult:
        return transform_filing(fixture_meta[name], load_report(name))

    return _run
