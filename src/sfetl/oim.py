"""Read facts from an xBRL-JSON (OIM) report.

Only what the pipeline needs: concept, value, unit, decimals, period and the
taxonomy-defined dimensions. See https://www.xbrl.org/Specification/xbrl-json/REC-2021-10-13/
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

# Dimensions defined by the OIM itself. Anything else (e.g. ifrs-full:SegmentsAxis) is a
# taxonomy-defined dimension and means the fact is a breakdown, not a consolidated total.
CORE_DIMENSIONS = frozenset({"concept", "entity", "period", "unit", "language", "noteId"})


@dataclass(frozen=True)
class Period:
    """Inclusive calendar dates. ``start`` is None for instants."""

    start: date | None
    end: date

    @property
    def is_instant(self) -> bool:
        return self.start is None

    @property
    def days(self) -> int:
        return 0 if self.start is None else (self.end - self.start).days + 1


@dataclass(frozen=True)
class Fact:
    fact_id: str
    concept: str
    value: Decimal
    unit: str | None
    decimals: int | None  # None means INF (exact) or not reported
    period: Period
    dimensions: dict[str, str] = field(default_factory=dict)  # taxonomy-defined only

    @property
    def has_dimensions(self) -> bool:
        return bool(self.dimensions)


def _parse_point(value: str, *, is_end: bool) -> date:
    """Parse one OIM period boundary into a calendar date.

    OIM writes the xBRL 2.1 instant ``2024-12-31`` as ``2025-01-01T00:00:00`` (midnight at
    the *start* of the next day). An end boundary at midnight therefore belongs to the
    previous calendar day. ``T24:00:00`` and date-only values are already that day.
    """
    text = value.strip()
    if "T" not in text:
        return date.fromisoformat(text)
    day_part, time_part = text.split("T", 1)
    if time_part.startswith("24:00"):
        return date.fromisoformat(day_part)
    moment = datetime.fromisoformat(text.rstrip("Z"))
    if is_end and moment.time() == datetime.min.time():
        return (moment - timedelta(days=1)).date()
    return moment.date()


def parse_period(value: str) -> Period:
    if "/" in value:
        start_raw, end_raw = value.split("/", 1)
        return Period(
            start=_parse_point(start_raw, is_end=False), end=_parse_point(end_raw, is_end=True)
        )
    return Period(start=None, end=_parse_point(value, is_end=True))


def _parse_decimals(raw: Any) -> int | None:
    if raw is None or raw == "INF":
        return None
    return int(raw)


def iter_numeric_facts(report: dict[str, Any]) -> Iterator[Fact]:
    """Yield every numeric, non-nil fact of the report.

    Values in xBRL-JSON are already in base units: any ``ix:scale`` of the inline document
    was applied by the converter, and ``decimals`` only states the precision (``-6`` means
    "accurate to the million"). We therefore never multiply by ``10 ** -decimals``.
    """
    for fact_id, fact in report.get("facts", {}).items():
        dims: dict[str, str] = fact.get("dimensions", {})
        raw_value = fact.get("value")
        if raw_value is None or "unit" not in dims:
            continue  # nil facts and non-numeric facts (text blocks, dates) carry no unit
        try:
            value = Decimal(str(raw_value))
        except InvalidOperation:
            continue
        if "period" not in dims or "concept" not in dims:
            continue
        yield Fact(
            fact_id=fact_id,
            concept=dims["concept"],
            value=value,
            unit=dims.get("unit"),
            decimals=_parse_decimals(fact.get("decimals")),
            period=parse_period(dims["period"]),
            dimensions={k: v for k, v in dims.items() if k not in CORE_DIMENSIONS},
        )
