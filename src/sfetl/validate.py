"""Validate: rule checks over transformed filings. Flags only; never changes a value.

Every rule reports, per filing, one of: pass, flag or not applicable (inputs missing).
Flags become rows of ``validation_issue`` and lines of the written report.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sfetl.concepts import METRICS
from sfetl.transform import FilingResult

Severity = Literal["error", "warning", "info"]
Status = Literal["pass", "flag", "n/a"]

BALANCE_REL_TOLERANCE = Decimal("0.001")  # 0.1 % of total assets


@dataclass(frozen=True)
class Issue:
    filing_id: int
    lei: str
    fiscal_year: int
    rule: str
    severity: Severity
    metric: str | None
    detail: str


@dataclass(frozen=True)
class Rule:
    name: str
    severity: Severity
    description: str
    check: Callable[[FilingResult], tuple[Status, list[tuple[str | None, str]]]]


@dataclass
class RuleOutcome:
    rule: Rule
    counts: Counter[str] = field(default_factory=Counter)


@dataclass
class ValidationReport:
    outcomes: list[RuleOutcome]
    issues: list[Issue]
    filings: int


def _rounding_allowance(*decimals: int | None) -> Decimal:
    known = [d for d in decimals if d is not None]
    if not known:
        return Decimal(0)
    return Decimal(2) * Decimal(1).scaleb(-min(known))


def check_balance_identity(r: FilingResult) -> tuple[Status, list[tuple[str | None, str]]]:
    m = r.metrics
    if not all(k in m for k in ("total_assets", "total_equity", "total_liabilities")):
        return "n/a", []
    assets, equity, liabilities = m["total_assets"], m["total_equity"], m["total_liabilities"]
    gap = assets.value - (equity.value + liabilities.value)
    allowed = max(
        BALANCE_REL_TOLERANCE * abs(assets.value),
        _rounding_allowance(assets.decimals, equity.decimals, liabilities.decimals),
    )
    if abs(gap) <= allowed:
        return "pass", []
    rel = gap / assets.value if assets.value else Decimal(0)
    return "flag", [
        (
            None,
            f"assets {assets.value:,.0f} vs equity + liabilities "
            f"{equity.value + liabilities.value:,.0f} (gap {gap:,.0f}, {rel:.2%}; "
            f"liabilities from {liabilities.source_concept})",
        )
    ]


def check_core_metrics(r: FilingResult) -> tuple[Status, list[tuple[str | None, str]]]:
    required = [
        s.metric for s in METRICS if s.core and (s.applies_to_financials or not r.is_financial)
    ]
    missing = [name for name in required if name not in r.metrics]
    if not missing:
        return "pass", []
    kind = "financial" if r.is_financial else "non-financial"
    return "flag", [(name, f"{name} not found ({kind} company)") for name in missing]


def check_signs(r: FilingResult) -> tuple[Status, list[tuple[str | None, str]]]:
    checked = [s for s in METRICS if s.non_negative and s.metric in r.metrics]
    if not checked:
        return "n/a", []
    bad = [s.metric for s in checked if r.metrics[s.metric].value < 0]
    if not bad:
        return "pass", []
    return "flag", [(name, f"{name} is negative: {r.metrics[name].value:,.0f}") for name in bad]


def check_component_bounds(r: FilingResult) -> tuple[Status, list[tuple[str | None, str]]]:
    pairs = [
        ("current_assets", "total_assets"),
        ("cash", "total_assets"),
        ("current_liabilities", "total_liabilities"),
    ]
    applicable = [
        (part, whole) for part, whole in pairs if part in r.metrics and whole in r.metrics
    ]
    if not applicable:
        return "n/a", []
    flags: list[tuple[str | None, str]] = []
    for part, whole in applicable:
        p, w = r.metrics[part], r.metrics[whole]
        if p.value > w.value + _rounding_allowance(p.decimals, w.decimals):
            flags.append((part, f"{part} {p.value:,.0f} exceeds {whole} {w.value:,.0f}"))
    return ("flag", flags) if flags else ("pass", [])


def check_period(r: FilingResult) -> tuple[Status, list[tuple[str | None, str]]]:
    if r.document_period_end is None:
        return "n/a", []
    if r.document_period_end == r.meta.period_end:
        return "pass", []
    return "flag", [
        (
            None,
            f"index says period_end {r.meta.period_end}, facts end on {r.document_period_end}",
        )
    ]


def _observation_rule(
    name: str,
) -> Callable[[FilingResult], tuple[Status, list[tuple[str | None, str]]]]:
    def check(r: FilingResult) -> tuple[Status, list[tuple[str | None, str]]]:
        found = [(o.metric, o.detail) for o in r.observations if o.rule == name]
        return ("flag", found) if found else ("pass", [])

    return check


RULES: tuple[Rule, ...] = (
    Rule(
        "balance_identity",
        "error",
        "total assets = total equity + total liabilities (0.1 % or rounding tolerance)",
        check_balance_identity,
    ),
    Rule(
        "missing_core_metric",
        "warning",
        "revenue*, net profit, assets, equity, liabilities present (*not for banks/insurers)",
        check_core_metrics,
    ),
    Rule(
        "sign_check",
        "error",
        "assets, liabilities, revenue, cash and current items are not negative",
        check_signs,
    ),
    Rule(
        "component_bounds",
        "error",
        "current assets and cash <= total assets; current liabilities <= total liabilities",
        check_component_bounds,
    ),
    Rule(
        "period_consistency",
        "warning",
        "reporting period detected from the facts equals the period_end of the index",
        check_period,
    ),
    Rule(
        "non_eur_unit",
        "warning",
        "mapped concepts reported in a currency other than EUR (recorded, not loaded)",
        _observation_rule("non_eur_unit"),
    ),
    Rule(
        "inconsistent_duplicate",
        "warning",
        "duplicate facts that disagree beyond rounding",
        _observation_rule("inconsistent_duplicate"),
    ),
)

UNIQUE_RULE = Rule(
    "unique_value",
    "error",
    "exactly one value per (company, fiscal year, metric)",
    lambda r: ("pass", []),
)


def validate(results: Sequence[FilingResult]) -> ValidationReport:
    outcomes = [RuleOutcome(rule) for rule in (*RULES, UNIQUE_RULE)]
    issues: list[Issue] = []

    def record(result: FilingResult, rule: Rule, metric: str | None, detail: str) -> None:
        issues.append(
            Issue(
                filing_id=result.meta.filing_id,
                lei=result.meta.lei,
                fiscal_year=result.meta.fiscal_year,
                rule=rule.name,
                severity=rule.severity,
                metric=metric,
                detail=detail,
            )
        )

    for result in results:
        for outcome in outcomes[:-1]:
            status, flags = outcome.rule.check(result)
            outcome.counts[status] += 1
            for metric, detail in flags:
                record(result, outcome.rule, metric, detail)

    # unique_value looks across filings: two filings of one company may not feed the same year
    keys = Counter(
        (r.meta.lei, r.meta.fiscal_year, metric) for r in results for metric in r.metrics
    )
    unique_outcome = outcomes[-1]
    for result in results:
        dupes = [
            m for m in result.metrics if keys[(result.meta.lei, result.meta.fiscal_year, m)] > 1
        ]
        if dupes:
            unique_outcome.counts["flag"] += 1
            for metric in dupes:
                record(result, UNIQUE_RULE, metric, f"{metric} supplied by more than one filing")
        else:
            unique_outcome.counts["pass"] += 1
    return ValidationReport(outcomes=outcomes, issues=issues, filings=len(results))


def has_blocking_duplicates(report: ValidationReport) -> bool:
    return any(i.rule == UNIQUE_RULE.name for i in report.issues)


def render_markdown(report: ValidationReport, results: Sequence[FilingResult]) -> str:
    names = {r.meta.filing_id: r.meta.entity_name for r in results}
    lines = [
        "# Validation report",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by `sfetl run`. "
        f"Filings checked: {report.filings}.",
        "",
        "Validation only flags. No value is changed or dropped because of a flag.",
        "",
        "| Rule | Severity | Pass | Flagged | N/A | Checks |",
        "|---|---|---:|---:|---:|---|",
    ]
    for o in report.outcomes:
        lines.append(
            f"| `{o.rule.name}` | {o.rule.severity} | {o.counts['pass']} | {o.counts['flag']} "
            f"| {o.counts['n/a']} | {o.rule.description} |"
        )
    lines.append("")
    by_rule: dict[str, list[Issue]] = {}
    for issue in report.issues:
        by_rule.setdefault(issue.rule, []).append(issue)
    for o in report.outcomes:
        flagged = by_rule.get(o.rule.name, [])
        if not flagged:
            continue
        lines += [f"## `{o.rule.name}` ({len(flagged)} flags)", ""]
        for i in sorted(
            flagged, key=lambda x: (names.get(x.filing_id, ""), x.fiscal_year, x.metric or "")
        ):
            lines.append(f"- {names.get(i.filing_id, i.lei)} FY{i.fiscal_year}: {i.detail}")
        lines.append("")
    return "\n".join(lines)


def write_report(report: ValidationReport, results: Sequence[FilingResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report, results), encoding="utf-8")
