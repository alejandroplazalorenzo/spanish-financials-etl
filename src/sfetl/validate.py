"""Validate (before loading): rule checks over transformed filings. Flags only; never edits.

Every rule reports, per filing, one of: pass, flag or not applicable (inputs missing).
Flags become rows of ``validation_issue`` and lines of the written report. These rules look at
the *filer's* data; the checks of the *pipeline's* own work (coverage, orphans, golden figures,
acceptance queries) run after the load, in the database (``validate_db.py``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sfetl.concepts import DERIVED_PREFIX, METRICS, SUBTOTAL_CHECKS
from sfetl.transform import FilingResult

Severity = Literal["error", "warning", "info"]
Status = Literal["pass", "flag", "n/a"]
CheckResult = tuple[Status, list[tuple[str | None, str]]]

BALANCE_REL_TOLERANCE = Decimal("0.001")  # 0.1 % of the total


@dataclass(frozen=True)
class Issue:
    filing_id: int  # the filings.xbrl.org id of the filing (stable across databases)
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
    check: Callable[[FilingResult], CheckResult]


@dataclass
class RuleOutcome:
    rule: Rule
    counts: Counter[str] = field(default_factory=Counter)


@dataclass
class ValidationReport:
    outcomes: list[RuleOutcome]
    issues: list[Issue]
    filings: int

    def issues_for(self, filing_id: int) -> list[Issue]:
        return [i for i in self.issues if i.filing_id == filing_id]


def _rounding_allowance(*decimals: int | None) -> Decimal:
    known = [d for d in decimals if d is not None]
    if not known:
        return Decimal(0)
    return Decimal(2) * Decimal(1).scaleb(-min(known))


def _within(total: Decimal, computed: Decimal, *decimals: int | None) -> bool:
    allowed = max(BALANCE_REL_TOLERANCE * abs(total), _rounding_allowance(*decimals))
    return abs(total - computed) <= allowed


def check_balance_identity(r: FilingResult) -> CheckResult:
    assets, equity, liabilities = (
        r.value("total_assets"),
        r.value("total_equity"),
        r.value("total_liabilities"),
    )
    if assets is None or equity is None or liabilities is None:
        return "n/a", []
    m = r.metrics
    decimals = (m["total_assets"].decimals, m["total_equity"].decimals)
    if _within(assets, equity + liabilities, *decimals, m["total_liabilities"].decimals):
        return "pass", []
    gap = assets - (equity + liabilities)
    rel = gap / assets if assets else Decimal(0)
    return "flag", [
        (
            None,
            f"assets {assets:,.0f} vs equity + liabilities {equity + liabilities:,.0f} "
            f"(gap {gap:,.0f}, {rel:.2%}; liabilities from "
            f"{m['total_liabilities'].source_concept})",
        )
    ]


def check_core_metrics(r: FilingResult) -> CheckResult:
    required = [
        s.code for s in METRICS if s.core and (s.applies_to_financials or not r.is_financial)
    ]
    missing = [name for name in required if r.value(name) is None]
    if not missing:
        return "pass", []
    kind = "financial" if r.is_financial else "non-financial"
    return "flag", [(name, f"{name} not found ({kind} company)") for name in missing]


def check_signs(r: FilingResult) -> CheckResult:
    checked = [s for s in METRICS if s.non_negative and r.value(s.code) is not None]
    if not checked:
        return "n/a", []
    bad = [s.code for s in checked if (r.value(s.code) or 0) < 0]
    if not bad:
        return "pass", []
    return "flag", [(name, f"{name} is negative: {r.value(name):,.0f}") for name in bad]


def check_component_bounds(r: FilingResult) -> CheckResult:
    pairs = [
        ("current_assets", "total_assets"),
        ("cash", "total_assets"),
        ("current_liabilities", "total_liabilities"),
    ]
    applicable = [
        (part, whole)
        for part, whole in pairs
        if r.value(part) is not None and r.value(whole) is not None
    ]
    if not applicable:
        return "n/a", []
    flags: list[tuple[str | None, str]] = []
    for part, whole in applicable:
        p, w = r.metrics[part], r.metrics[whole]
        assert p.value is not None and w.value is not None
        if p.value > w.value + _rounding_allowance(p.decimals, w.decimals):
            flags.append((part, f"{part} {p.value:,.0f} exceeds {whole} {w.value:,.0f}"))
    return ("flag", flags) if flags else ("pass", [])


def check_subtotals(r: FilingResult) -> CheckResult:
    """Catalogue identities (assets = non-current + current, ...). Only when every term exists
    and the total is reported (a derived total equals its components by construction)."""
    flags: list[tuple[str | None, str]] = []
    applicable = 0
    for check in SUBTOTAL_CHECKS:
        total = r.metrics.get(check.total)
        parts = [(r.metrics.get(code), weight) for code, weight in check.components]
        if total is None or total.value is None or total.source_concept.startswith(DERIVED_PREFIX):
            continue
        if any(p is None or p.value is None for p, _ in parts):
            continue
        applicable += 1
        computed = sum(
            (Decimal(weight) * p.value for p, weight in parts if p is not None and p.value),
            Decimal(0),
        )
        decimals = [total.decimals, *(p.decimals for p, _ in parts if p is not None)]
        if not _within(total.value, computed, *decimals):
            terms = " ".join(
                f"{'+' if w > 0 else '-'} {code}" for code, w in check.components
            ).lstrip("+ ")
            flags.append(
                (
                    check.total,
                    f"{check.name}: {check.total} {total.value:,.0f} vs {terms} "
                    f"{computed:,.0f} (gap {total.value - computed:,.0f})",
                )
            )
    if not applicable:
        return "n/a", []
    return ("flag", flags) if flags else ("pass", [])


def check_period(r: FilingResult) -> CheckResult:
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


def check_nil(r: FilingResult) -> CheckResult:
    nil = [code for code, v in r.metrics.items() if v.is_nil]
    if not nil:
        return "pass", []
    return "flag", [(code, f"{code} tagged nil: loaded as NULL + is_nil") for code in nil]


def _observation_rule(name: str) -> Callable[[FilingResult], CheckResult]:
    def check(r: FilingResult) -> CheckResult:
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
        "assets, liabilities, revenue, cash, current items, capex are not negative",
        check_signs,
    ),
    Rule(
        "component_bounds",
        "error",
        "current assets and cash <= total assets; current liabilities <= total liabilities",
        check_component_bounds,
    ),
    Rule(
        "subtotal_check",
        "warning",
        "catalogue identities: assets, liabilities and equity splits, profit attribution, "
        "continuing + discontinued, tax bridge (0.1 % or rounding tolerance)",
        check_subtotals,
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
        "mapped concepts reported in another currency (recorded, not loaded)",
        _observation_rule("non_eur_unit"),
    ),
    Rule(
        "inconsistent_duplicate",
        "warning",
        "duplicate facts that disagree beyond rounding",
        _observation_rule("inconsistent_duplicate"),
    ),
    Rule(
        "nil_fact",
        "info",
        "metrics tagged nil (not available): loaded as NULL + is_nil, never as 0",
        check_nil,
    ),
)


def validate(results: Sequence[FilingResult]) -> ValidationReport:
    outcomes = [RuleOutcome(rule) for rule in RULES]
    issues: list[Issue] = []
    for result in results:
        for outcome in outcomes:
            status, flags = outcome.rule.check(result)
            outcome.counts[status] += 1
            for metric, detail in flags:
                issues.append(
                    Issue(
                        filing_id=result.meta.filing_id,
                        lei=result.meta.lei,
                        fiscal_year=result.meta.fiscal_year,
                        rule=outcome.rule.name,
                        severity=outcome.rule.severity,
                        metric=metric,
                        detail=detail,
                    )
                )
    return ValidationReport(outcomes=outcomes, issues=issues, filings=len(results))


def render_rules_markdown(report: ValidationReport, results: Sequence[FilingResult]) -> list[str]:
    names = {r.meta.filing_id: r.meta.entity_name for r in results}
    lines = [
        "## Rules on the filers' data (before loading)",
        "",
        f"Filings checked: {report.filings}. Validation only flags: no value is changed or "
        "dropped because of a flag.",
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
        lines += [f"### `{o.rule.name}` ({len(flagged)} flags)", ""]
        for i in sorted(
            flagged, key=lambda x: (names.get(x.filing_id, ""), x.fiscal_year, x.metric or "")
        ):
            lines.append(f"- {names.get(i.filing_id, i.lei)} FY{i.fiscal_year}: {i.detail}")
        lines.append("")
    return lines
