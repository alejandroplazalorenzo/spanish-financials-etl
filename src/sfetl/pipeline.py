"""Extract -> Transform -> Validate -> Load, end to end."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sfetl import extract
from sfetl.config import REPORTS_DIR
from sfetl.db import connect_owner
from sfetl.load import LoadStats, load
from sfetl.transform import FilingResult, transform_filing
from sfetl.validate import ValidationReport, has_blocking_duplicates, validate, write_report

log = logging.getLogger(__name__)


class DuplicateValuesError(RuntimeError):
    """Two filings would write the same (company, fiscal year, metric): refuse to guess."""


@dataclass
class RunSummary:
    started_at: str
    fiscal_years: list[int]
    index_filings: int
    selected_filings: int
    downloaded_now: int
    read_from_cache: int
    companies: int
    numeric_facts_read: int
    metric_values: int
    duplicate_groups_resolved: int
    metric_counts: dict[str, int]
    financial_companies: int
    validation: dict[str, dict[str, int]]
    issues_by_severity: dict[str, int]
    load: dict[str, int] | None
    seconds: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__


def run(
    fiscal_years: list[int] | None = None,
    max_companies: int | None = None,
    refresh_index: bool = False,
    load_db: bool = True,
    delay_s: float = extract.DEFAULT_DELAY_S,
    reports_dir: Path = REPORTS_DIR,
) -> RunSummary:
    started = datetime.now()
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    index = extract.fetch_index(refresh=refresh_index, delay_s=delay_s)
    years = fiscal_years or extract.latest_fiscal_years(index, n_years=2, min_filings=20)
    selected = extract.select_filings(index, years, max_companies=max_companies)
    extract.save_selection(selected)
    downloaded = 0
    for i, meta in enumerate(selected, 1):
        _, fresh = extract.download_filing(meta, delay_s=delay_s)
        downloaded += int(fresh)
        if fresh:
            log.info("downloaded %d/%d %s", i, len(selected), meta.fxo_id)
    timings["extract"] = round(time.perf_counter() - t0, 1)
    log.info("extract: %d selected, %d downloaded now", len(selected), downloaded)

    t0 = time.perf_counter()
    results: list[FilingResult] = [
        transform_filing(meta, extract.read_filing(extract.raw_path(meta))) for meta in selected
    ]
    timings["transform"] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    report: ValidationReport = validate(results)
    write_report(report, results, reports_dir / "validation_report.md")
    timings["validate"] = round(time.perf_counter() - t0, 1)

    load_stats: LoadStats | None = None
    if load_db:
        if has_blocking_duplicates(report):
            raise DuplicateValuesError("unique_value flags found; see the validation report")
        t0 = time.perf_counter()
        with connect_owner() as conn:
            load_stats = load(conn, results, report)
        timings["load"] = round(time.perf_counter() - t0, 1)

    summary = RunSummary(
        started_at=started.isoformat(timespec="seconds"),
        fiscal_years=list(years),
        index_filings=len(index),
        selected_filings=len(selected),
        downloaded_now=downloaded,
        read_from_cache=len(selected) - downloaded,
        companies=len({m.lei for m in selected}),
        numeric_facts_read=sum(r.numeric_facts for r in results),
        metric_values=sum(len(r.metrics) for r in results),
        duplicate_groups_resolved=sum(r.duplicate_groups for r in results),
        metric_counts=dict(Counter(m for r in results for m in r.metrics).most_common()),
        financial_companies=len({r.meta.lei for r in results if r.is_financial}),
        validation={
            o.rule.name: {
                "pass": o.counts["pass"],
                "flag": o.counts["flag"],
                "n/a": o.counts["n/a"],
            }
            for o in report.outcomes
        },
        issues_by_severity=dict(Counter(i.severity for i in report.issues)),
        load=load_stats.__dict__ if load_stats else None,
        seconds=timings,
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "run_summary.json").write_text(
        json.dumps(summary.to_json(), indent=2), encoding="utf-8"
    )
    return summary
