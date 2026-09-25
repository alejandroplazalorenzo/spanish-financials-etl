"""Extract -> Transform -> Validate -> Load -> resolve ownership -> check, end to end.

The filing is the unit of failure: a download, transform or load error is caught for that
filing, recorded with its stage and error, and the run continues with the rest. The run ends
with a non-zero exit code when any filing failed or any database check is red, and the
validation report lists both. Nothing is dropped without a trace.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from sfetl import extract
from sfetl.config import REPORTS_DIR
from sfetl.db import connect_owner
from sfetl.load import FilingLoad, load_catalog, load_filing, resolve_ownership
from sfetl.ownership import load_gleif
from sfetl.transform import FilingResult, transform_filing
from sfetl.validate import ValidationReport, render_rules_markdown, validate
from sfetl.validate_db import (
    DbValidation,
    FailedFiling,
    RunContext,
    render_db_markdown,
    run_db_checks,
)

log = logging.getLogger(__name__)


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
    nil_facts_read: int
    metric_values: int
    nil_metric_values: int
    derived_metric_values: int
    duplicate_groups_resolved: int
    metric_counts: dict[str, int]
    financial_companies: int
    unmapped_extension_facts: int
    unmapped_revenue_like: int
    validation: dict[str, dict[str, int]]
    issues_by_severity: dict[str, int]
    failed_filings: list[dict[str, str]]
    load: dict[str, Any] | None
    ownership: dict[str, dict[str, int]] | None
    db_checks: dict[str, Any] | None
    exit_code: int
    seconds: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def write_unmapped_csv(results: Sequence[FilingResult], path: Path) -> tuple[int, int]:
    """One row per (filing, extension concept) with a current-year EUR total."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    revenue_like = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            [
                "fxo_id",
                "lei",
                "company",
                "fiscal_year",
                "concept",
                "period_type",
                "value_eur",
                "decimals",
                "looks_like_revenue",
                "company_has_ifrs_revenue",
                "bank_or_insurer",
            ]
        )
        for r in sorted(results, key=lambda x: (x.meta.entity_name, x.meta.fiscal_year)):
            has_revenue = r.value("revenue") is not None
            for u in r.unmapped:
                rows += 1
                revenue_like += int(u.looks_like_revenue)
                writer.writerow(
                    [
                        r.meta.fxo_id,
                        r.meta.lei,
                        r.meta.entity_name,
                        r.meta.fiscal_year,
                        u.concept,
                        u.period_type,
                        f"{u.value:f}",
                        "" if u.decimals is None else u.decimals,
                        str(u.looks_like_revenue).lower(),
                        str(has_revenue).lower(),
                        str(r.is_financial).lower(),
                    ]
                )
    return rows, revenue_like


def revenue_gap(results: Sequence[FilingResult]) -> tuple[int, int]:
    """(non-financial filings with EUR figures but no IFRS revenue, those with a revenue hint)."""
    without = [
        r for r in results if not r.is_financial and r.metrics and r.value("revenue") is None
    ]
    hinted = [r for r in without if any(u.looks_like_revenue for u in r.unmapped)]
    return len(without), len(hinted)


def _render_report(
    started: datetime,
    selected: int,
    report: ValidationReport,
    results: Sequence[FilingResult],
    failed: Sequence[FailedFiling],
    db: DbValidation | None,
    exit_code: int,
) -> str:
    lines = [
        "# Validation report",
        "",
        f"Generated {started:%Y-%m-%d %H:%M} by `sfetl run`. Filings selected: {selected}; "
        f"transformed: {len(results)}; failed: {len(failed)}.",
        "",
        f"**Result: {'ALL GREEN' if exit_code == 0 else 'RED'}** (exit code {exit_code}). "
        "Flags on the filers' data (first section) never change a value and do not make the "
        "run red; failed filings and red checks of the pipeline's own work (second section) do.",
        "",
    ]
    lines += render_rules_markdown(report, results)
    if db is not None:
        lines += render_db_markdown(db)
    else:
        lines += ["## Failed filings", ""]
        lines += [f"- `{f.fxo_id}` ({f.stage}): {f.error}" for f in failed] or ["- none"]
        lines += ["", "(Database checks not run: `--no-load`.)", ""]
    return "\n".join(lines)


def run(
    fiscal_years: list[int] | None = None,
    max_companies: int | None = None,
    refresh_index: bool = False,
    load_db: bool = True,
    delay_s: float = extract.DEFAULT_DELAY_S,
    reports_dir: Path = REPORTS_DIR,
    use_gleif: bool = True,
    gleif_offline: bool = False,
    connect: Callable[[], psycopg.Connection] = connect_owner,
) -> RunSummary:
    started = datetime.now()
    timings: dict[str, float] = {}
    failed: list[FailedFiling] = []

    t0 = time.perf_counter()
    index = extract.fetch_index(refresh=refresh_index, delay_s=delay_s)
    years = fiscal_years or extract.latest_fiscal_years(index, n_years=2, min_filings=20)
    selected = extract.select_filings(index, years, max_companies=max_companies)
    extract.save_selection(selected)
    downloaded = 0
    available: list[extract.FilingMeta] = []
    for i, meta in enumerate(selected, 1):
        try:
            _, fresh = extract.download_filing(meta, delay_s=delay_s)
        except Exception as err:
            failed.append(FailedFiling(meta.fxo_id, "download", _short(err)))
            log.error("download failed %s: %s", meta.fxo_id, err)
            continue
        available.append(meta)
        downloaded += int(fresh)
        if fresh:
            log.info("downloaded %d/%d %s", i, len(selected), meta.fxo_id)
    timings["extract"] = round(time.perf_counter() - t0, 1)
    log.info("extract: %d selected, %d downloaded now", len(selected), downloaded)

    t0 = time.perf_counter()
    results: list[FilingResult] = []
    for meta in available:
        try:
            results.append(transform_filing(meta, extract.read_filing(extract.raw_path(meta))))
        except Exception as err:
            failed.append(FailedFiling(meta.fxo_id, "transform", _short(err)))
            log.error("transform failed %s: %s", meta.fxo_id, err)
    timings["transform"] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    report = validate(results)
    unmapped_rows, unmapped_revenue_like = write_unmapped_csv(
        results, reports_dir / "unmapped_concepts.csv"
    )
    timings["validate"] = round(time.perf_counter() - t0, 1)

    load_stats: dict[str, Any] | None = None
    ownership_counts: dict[str, dict[str, int]] | None = None
    db: DbValidation | None = None
    if load_db:
        t0 = time.perf_counter()
        warnings: list[tuple[str, str]] = []
        loads: list[FilingLoad] = []
        loaded_ids: list[str] = []
        with connect() as conn:
            # autocommit: each load_filing opens its own real transaction (not a savepoint)
            conn.autocommit = True
            metric_ids = load_catalog(conn)
            for r in results:
                try:
                    done = load_filing(conn, r, report.issues_for(r.meta.filing_id), metric_ids)
                except Exception as err:
                    failed.append(FailedFiling(r.meta.fxo_id, "load", _short(err)))
                    log.error("load failed %s: %s", r.meta.fxo_id, err)
                    continue
                loads.append(done)
                loaded_ids.append(done.fxo_id)
                warnings += [(done.fxo_id, w) for w in done.warnings]
            timings["load"] = round(time.perf_counter() - t0, 1)

            t0 = time.perf_counter()
            leis = [row[0].strip() for row in conn.execute("SELECT lei FROM company").fetchall()]
            gleif_records = {}
            if use_gleif:
                gleif_records, gleif_failures = load_gleif(
                    leis, offline=gleif_offline, delay_s=delay_s
                )
                warnings += [(f"GLEIF {lei}", err) for lei, err in gleif_failures]
            counts = resolve_ownership(conn, gleif_records)
            ownership_counts = {k: dict(v) for k, v in counts.items()}
            timings["ownership"] = round(time.perf_counter() - t0, 1)

            t0 = time.perf_counter()
            ctx = RunContext(
                loaded_fxo_ids=loaded_ids,
                failed=failed,
                warnings=warnings,
                non_eur_only=[
                    r.meta.fxo_id
                    for r in results
                    if not r.metrics and any(o.rule == "non_eur_unit" for o in r.observations)
                ],
                unmapped_rows=unmapped_rows,
                unmapped_revenue_like=unmapped_revenue_like,
                revenue_gap=revenue_gap(results),
            )
            db = run_db_checks(conn, ctx)
            timings["db_checks"] = round(time.perf_counter() - t0, 1)
        load_stats = {
            "filings_loaded": len(loads),
            "values_inserted": sum(x.facts_inserted for x in loads),
            "values_not_inserted": sum(x.facts_not_inserted for x in loads),
            "warnings": len(warnings),
            "gleif_records": len(gleif_records),
        }

    exit_code = 0 if not failed and (db is None or db.ok) else 1
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "validation_report.md").write_text(
        _render_report(started, len(selected), report, results, failed, db, exit_code),
        encoding="utf-8",
    )
    summary = RunSummary(
        started_at=started.isoformat(timespec="seconds"),
        fiscal_years=list(years),
        index_filings=len(index),
        selected_filings=len(selected),
        downloaded_now=downloaded,
        read_from_cache=len(available) - downloaded,
        companies=len({m.lei for m in selected}),
        numeric_facts_read=sum(r.numeric_facts for r in results),
        nil_facts_read=sum(r.nil_facts for r in results),
        metric_values=sum(len(r.metrics) for r in results),
        nil_metric_values=sum(v.is_nil for r in results for v in r.metrics.values()),
        derived_metric_values=sum(
            v.source_concept.startswith("derived:") for r in results for v in r.metrics.values()
        ),
        duplicate_groups_resolved=sum(r.duplicate_groups for r in results),
        metric_counts=dict(
            Counter(m for r in results for m, v in r.metrics.items() if not v.is_nil).most_common()
        ),
        financial_companies=len({r.meta.lei for r in results if r.is_financial}),
        unmapped_extension_facts=unmapped_rows,
        unmapped_revenue_like=unmapped_revenue_like,
        validation={
            o.rule.name: {
                "pass": o.counts["pass"],
                "flag": o.counts["flag"],
                "n/a": o.counts["n/a"],
            }
            for o in report.outcomes
        },
        issues_by_severity=dict(Counter(i.severity for i in report.issues)),
        failed_filings=[asdict(f) for f in failed],
        load=load_stats,
        ownership=ownership_counts,
        db_checks=None
        if db is None
        else {
            "ok": db.ok,
            "passed": sum(c.ok for c in db.checks),
            "failed": [f"{c.section}: {c.name} ({c.detail})" for c in db.checks if not c.ok],
            "stats": db.stats,
        },
        exit_code=exit_code,
        seconds=timings,
    )
    (reports_dir / "run_summary.json").write_text(
        json.dumps(summary.to_json(), indent=2, default=str), encoding="utf-8"
    )
    return summary


def _short(err: BaseException) -> str:
    text = str(err).strip().splitlines()[0] if str(err).strip() else type(err).__name__
    return f"{type(err).__name__}: {text}"[:300]
