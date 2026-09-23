"""Rebuild the trimmed xBRL-JSON fixtures from the local download cache.

Run after `sfetl run` has cached the filings:  python tests/fixtures/build_fixtures.py
Each fixture keeps the documentInfo, every fact of the concepts the pipeline reads (all
periods, a few dimensioned ones) and one text block, so tests exercise real tagging.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sfetl.concepts import FINANCIAL_MARKERS, mapped_concepts
from sfetl.extract import load_selection, raw_path, read_filing
from sfetl.oim import parse_period

HERE = Path(__file__).parent
EXTRA_CONCEPTS = {
    "ifrs-full:EquityAndLiabilities",
    "ifrs-full:LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale",
}
MAX_DIMENSIONED_CURRENT = 3  # per concept, at the filing's period end
MAX_DIMENSIONED_OTHER = 1  # per concept, at any other date
TEXT_LIMIT = 200

WANTED = {
    "549300LHK07F2CHV4X31-2024-12-31-ESEF-ES-0": "endesa_2024.json",
    "VWMYAEQSTOPNV0SUGU82-2024-12-31-ESEF-ES-0": "bankinter_2024.json",
    "213800JX3V4TPO7TCJ08-2024-06-30-ESEF-ES-0": "berkeley_2024.json",
    "549300TTCXZOGZM2EY83-2025-01-31-ESEF-ES-0": "inditex_fy2024.json",
}
BY_NAME = {"AMPER": "amper_2024.json", "REALIA": "realia_2024.json"}


def trim(report: dict[str, Any], period_end: str) -> dict[str, Any]:
    keep_concepts = mapped_concepts() | FINANCIAL_MARKERS | EXTRA_CONCEPTS
    dimensioned: Counter[tuple[str, bool]] = Counter()
    current_end = parse_period(period_end).end
    facts: dict[str, Any] = {}
    text_kept = False
    for fact_id, fact in report["facts"].items():
        dims = fact.get("dimensions", {})
        concept = dims.get("concept", "")
        has_dims = any(k not in {"concept", "entity", "period", "unit", "language"} for k in dims)
        if concept in keep_concepts:
            if has_dims:
                is_current = parse_period(dims["period"]).end == current_end
                cap = MAX_DIMENSIONED_CURRENT if is_current else MAX_DIMENSIONED_OTHER
                if dimensioned[(concept, is_current)] >= cap:
                    continue
                dimensioned[(concept, is_current)] += 1
            facts[fact_id] = fact
        elif not text_kept and "unit" not in dims and isinstance(fact.get("value"), str):
            if len(fact["value"]) > 50:
                facts[fact_id] = {**fact, "value": fact["value"][:TEXT_LIMIT]}
                text_kept = True
    return {"documentInfo": report["documentInfo"], "facts": facts}


def main() -> None:
    selection = load_selection()
    metas = []
    for meta in selection:
        name = WANTED.get(meta.fxo_id)
        if name is None and meta.fiscal_year == 2024:
            name = next(
                (v for k, v in BY_NAME.items() if meta.entity_name.upper().startswith(k)), None
            )
        if name is None:
            continue
        trimmed = trim(read_filing(raw_path(meta)), meta.period_end.isoformat())
        (HERE / name).write_text(
            json.dumps(trimmed, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        metas.append({**meta.to_json(), "fixture": name})
        print(f"{name}: {len(trimmed['facts'])} facts")
    (HERE / "filings.json").write_text(
        json.dumps(metas, indent=1, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
