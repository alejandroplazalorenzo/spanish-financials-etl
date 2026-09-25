"""How an answer looks: a title repeating what was asked, then a record or a list.

Plain text, so the same rendering works in a terminal and in a Telegram message. Long answers
are split into pages at line boundaries; above ``CSV_MIN_ROWS`` rows the Telegram adapter sends
a CSV file instead of pages.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

PAGE_LIMIT = 3500  # characters; Telegram's hard limit is 4096
CSV_MIN_ROWS = 60


def _number(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def format_value(column: str, value: Any, unit: str | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime | date):
        return value.isoformat()[:10]
    if isinstance(value, int | float | Decimal):
        number = _number(value)
        if column.endswith("(%)") or unit == "%":
            return f"{number:,.2f} %"
        if unit == "x" or column.endswith("(x)"):
            return f"{number:,.2f}x"
        if unit == "EUR/share":
            return f"{number:,.4f} EUR/share"
        if unit == "EUR" or column.endswith("(EUR)"):
            if abs(number) >= 1_000_000:
                return f"{number / 1_000_000:,.1f} M EUR"
            return f"{number:,.0f} EUR"
        if isinstance(value, int) or number == number.to_integral_value():
            return f"{number:,.0f}" if not column.lower().endswith(("year", "fy")) else str(value)
        return f"{number:,.4f}"
    return str(value)


# Columns whose unit is given by the row's "Unit" column (other numbers carry their own).
UNIT_COLUMNS = frozenset({"Value", "Profit for the year"})


def _row_unit(columns: Sequence[str], row: Sequence[Any]) -> str | None:
    if "Unit" in columns:
        unit = row[list(columns).index("Unit")]
        return None if unit is None else str(unit)
    return None


def render_rows(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """One row -> "label: value" lines; several rows -> numbered entries."""
    formatted: list[list[tuple[str, str]]] = []
    for row in rows:
        unit = _row_unit(columns, row)
        entries = [
            (col, format_value(col, val, unit if col in UNIT_COLUMNS else None))
            for col, val in zip(columns, row, strict=True)
            if col != "Unit"
        ]
        formatted.append([(c, v) for c, v in entries if v != ""])
    if len(formatted) == 1:
        return "\n".join(f"{c}: {v}" for c, v in formatted[0])
    blocks = []
    for n, entries in enumerate(formatted, 1):
        if not entries:
            blocks.append(f"{n}.")
            continue
        head, *rest = entries
        lines = [f"{n}. {head[1]}"] + [f"   {c}: {v}" for c, v in rest]
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def paginate(text: str, limit: int = PAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    pages: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single overlong line is cut hard
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            pages.append(current)
            current = line
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages


def to_csv(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buffer.getvalue()


@dataclass
class Answer:
    text: str
    mode: str  # intent | free_sql | ambiguous | unanswered | error
    query_id: int | None = None
    title: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    pending_id: int | None = None
    intent_id: str | None = None
    sql: str | None = None

    def pages(self, limit: int = PAGE_LIMIT) -> list[str]:
        return paginate(self.text, limit)

    @property
    def csv(self) -> str | None:
        if len(self.rows) < CSV_MIN_ROWS:
            return None
        return to_csv(self.columns, self.rows)
