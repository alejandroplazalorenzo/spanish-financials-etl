"""Transform: from the facts of one xBRL-JSON report to canonical metrics for its fiscal year.

Rules (all documented in the README):

* Period: instants must end on the filing's ``period_end``; durations must end on it *and*
  last about twelve months (350-380 days). Comparative figures of the previous year that every
  report repeats are ignored: each fiscal year comes from its own report.
* Dimensions: only facts without taxonomy-defined dimensions are consolidated totals. A fact
  with e.g. ``ifrs-full:SegmentsAxis`` is a breakdown and is skipped.
* Units: only ``iso4217:EUR`` is loaded. Monetary facts of mapped concepts in another currency
  are recorded as an observation (the validation report lists them) and not converted.
* Decimals: xBRL-JSON values are already in euros; ``decimals`` is precision, not scale.
* Duplicates: the same concept/period/unit tagged several times is resolved by keeping the most
  precise fact (highest ``decimals``; INF beats any number), then the first in document order.
  If the duplicates disagree beyond rounding, the choice is kept but an observation is emitted.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sfetl.concepts import (
    DERIVED_LIABILITIES_CONCEPT,
    EUR_UNIT,
    FINANCIAL_MARKERS,
    FISCAL_YEAR_MAX_DAYS,
    FISCAL_YEAR_MIN_DAYS,
    LIABILITY_COMPONENTS,
    METRICS,
    MetricSpec,
    PeriodType,
    mapped_concepts,
)
from sfetl.extract import FilingMeta
from sfetl.oim import Fact, Period, iter_numeric_facts


@dataclass(frozen=True)
class MetricValue:
    metric: str
    value: Decimal
    decimals: int | None
    period: Period
    source_concept: str
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    """Something the transform noticed; validation turns it into a flagged issue."""

    rule: str
    metric: str | None
    detail: str


@dataclass
class FilingResult:
    meta: FilingMeta
    document_period_end: date | None
    numeric_facts: int
    is_financial: bool
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    duplicate_groups: int = 0  # concept/period/unit tagged more than once (and resolved)


def detect_period_end(facts: Iterable[Fact], min_facts: int = 10) -> date | None:
    """Latest end date carried by at least ``min_facts`` consolidated facts.

    Counting only well-populated dates keeps a stray fact (a maturity date, a post-balance-sheet
    event) from moving the reporting period.
    """
    counts = Counter(f.period.end for f in facts if not f.has_dimensions)
    if not counts:
        return None
    frequent = [d for d, n in counts.items() if n >= min_facts]
    return max(frequent) if frequent else max(counts)


def matches_fiscal_year(period: Period, period_type: PeriodType, period_end: date) -> bool:
    if period.end != period_end:
        return False
    if period_type == "instant":
        return period.is_instant
    return (not period.is_instant) and FISCAL_YEAR_MIN_DAYS <= period.days <= FISCAL_YEAR_MAX_DAYS


def _precision_rank(decimals: int | None) -> float:
    return float("inf") if decimals is None else float(decimals)


def _round_to(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)


def consistent(a: Fact, b: Fact) -> bool:
    """XBRL duplicate consistency: equal once both are rounded to the lower precision."""
    precisions = [d for d in (a.decimals, b.decimals) if d is not None]
    if not precisions:
        return a.value == b.value
    lowest = min(precisions)
    return _round_to(a.value, lowest) == _round_to(b.value, lowest)


def resolve_duplicates(candidates: Sequence[Fact]) -> tuple[Fact, bool]:
    """Pick one fact among duplicates (given in document order). Returns (fact, consistent)."""
    if not candidates:
        raise ValueError("no candidates")
    order = {id(f): i for i, f in enumerate(candidates)}
    chosen = sorted(candidates, key=lambda f: (-_precision_rank(f.decimals), order[id(f)]))[0]
    all_consistent = all(consistent(chosen, other) for other in candidates)
    return chosen, all_consistent


def _group_by_concept(facts: Iterable[Fact]) -> dict[str, list[Fact]]:
    wanted = mapped_concepts()
    grouped: dict[str, list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.concept in wanted:
            grouped[fact.concept].append(fact)
    return grouped


def pick_concept_value(
    concept: str,
    facts: Sequence[Fact],
    period_type: PeriodType,
    period_end: date,
    metric: str,
    observations: list[Observation],
    duplicates: Counter[str] | None = None,
) -> Fact | None:
    """Current-year, undimensioned, EUR value of one concept, or None."""
    in_period = [
        f
        for f in facts
        if not f.has_dimensions and matches_fiscal_year(f.period, period_type, period_end)
    ]
    other_units = sorted({f.unit or "?" for f in in_period if f.unit != EUR_UNIT})
    if other_units:
        observations.append(
            Observation("non_eur_unit", metric, f"{concept} reported in {', '.join(other_units)}")
        )
    eur = [f for f in in_period if f.unit == EUR_UNIT]
    if not eur:
        return None
    if len(eur) > 1 and duplicates is not None:
        duplicates[concept] += 1
    chosen, ok = resolve_duplicates(eur)
    if not ok:
        values = ", ".join(f"{f.value} (decimals={f.decimals})" for f in eur)
        observations.append(
            Observation(
                "inconsistent_duplicate", metric, f"{concept}: kept {chosen.value}; saw {values}"
            )
        )
    return chosen


def select_metric(
    spec: MetricSpec,
    grouped: dict[str, list[Fact]],
    period_end: date,
    observations: list[Observation],
    duplicates: Counter[str] | None = None,
) -> MetricValue | None:
    for concept in spec.concepts:
        fact = pick_concept_value(
            concept,
            grouped.get(concept, []),
            spec.period_type,
            period_end,
            spec.metric,
            observations,
            duplicates,
        )
        if fact is not None:
            return MetricValue(
                metric=spec.metric,
                value=fact.value,
                decimals=fact.decimals,
                period=fact.period,
                source_concept=concept,
                fact_ids=(fact.fact_id,),
            )
    return None


def derive_total_liabilities(
    grouped: dict[str, list[Fact]], period_end: date, observations: list[Observation]
) -> MetricValue | None:
    parts: list[Fact] = []
    for concept in LIABILITY_COMPONENTS:
        fact = pick_concept_value(
            concept,
            grouped.get(concept, []),
            "instant",
            period_end,
            "total_liabilities",
            observations,
        )
        if fact is None:
            return None
        parts.append(fact)
    precisions = [f.decimals for f in parts if f.decimals is not None]
    return MetricValue(
        metric="total_liabilities",
        value=sum((f.value for f in parts), Decimal(0)),
        decimals=min(precisions) if precisions else None,
        period=parts[0].period,
        source_concept=DERIVED_LIABILITIES_CONCEPT,
        fact_ids=tuple(f.fact_id for f in parts),
    )


def is_financial_report(facts: Iterable[Fact], period_end: date) -> bool:
    return any(
        f.concept in FINANCIAL_MARKERS and not f.has_dimensions and f.period.end == period_end
        for f in facts
    )


def transform_filing(meta: FilingMeta, report: dict[str, Any]) -> FilingResult:
    facts = list(iter_numeric_facts(report))
    period_end = meta.period_end
    observations: list[Observation] = []
    duplicates: Counter[str] = Counter()
    grouped = _group_by_concept(facts)
    metrics: dict[str, MetricValue] = {}
    for spec in METRICS:
        value = select_metric(spec, grouped, period_end, observations, duplicates)
        if value is None and spec.metric == "total_liabilities":
            value = derive_total_liabilities(grouped, period_end, observations)
        if value is not None:
            metrics[spec.metric] = value
    # the same foreign-currency note can be raised by several lookups: keep one of each
    unique_observations = list(dict.fromkeys(observations))
    return FilingResult(
        meta=meta,
        document_period_end=detect_period_end(facts),
        numeric_facts=len(facts),
        is_financial=is_financial_report(facts, period_end),
        metrics=metrics,
        observations=unique_observations,
        duplicate_groups=sum(duplicates.values()),
    )
