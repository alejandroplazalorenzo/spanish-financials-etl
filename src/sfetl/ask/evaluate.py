"""Execution accuracy of the free-SQL fallback (and only of the fallback).

Intents are fixed, tested queries: their correctness is checked by the smoke test and by the
integration tests, not by a model. The only SQL a model writes is the fallback's, so this is
where execution accuracy applies: for questions that no intent covers, does the generated query
return the same rows as a reference query?

* values are normalised (numbers rounded to 4 decimals, text trimmed, dates as ISO strings);
* rows are compared as a multiset, or as a list when the question sets ``ordered: true``;
* ``strict`` requires the same columns; ``match`` also accepts extra columns when some choice of
  them reproduces the reference exactly.

The reference queries in ``eval/fallback_questions.yaml`` are reviewable SQL over the curated
views. They were written for this rebuild and must be reviewed by the author before any
accuracy figure computed with them is quoted.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import yaml

from sfetl.ask.classify import write_free_sql
from sfetl.ask.guardrails import UnsafeSQLError, guard_sql
from sfetl.ask.llm import LLMError, LLMProvider
from sfetl.ask.service import FREE_SQL_MAX_ROWS, FREE_SQL_TIMEOUT_MS, QueryOutcome, run_read_only
from sfetl.db import connect_assistant

MAX_PERMUTATIONS = 50_000


@dataclass(frozen=True)
class FallbackQuestion:
    id: str
    question: str
    lang: str
    reference_sql: str
    ordered: bool = False


def load_questions(path: Path) -> list[FallbackQuestion]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        FallbackQuestion(
            id=str(q["id"]),
            question=q["question"],
            lang=q.get("lang", "en"),
            reference_sql=q["reference_sql"],
            ordered=bool(q.get("ordered", False)),
        )
        for q in raw
    ]


def normalize(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value).strip()


def _rows(result: QueryOutcome, columns: Sequence[int] | None = None) -> list[tuple[Any, ...]]:
    idx = list(range(len(result.columns))) if columns is None else list(columns)
    return [tuple(normalize(row[i]) for i in idx) for row in result.rows]


def _same(a: list[tuple[Any, ...]], b: list[tuple[Any, ...]], ordered: bool) -> bool:
    return a == b if ordered else Counter(a) == Counter(b)


def compare(reference: QueryOutcome, generated: QueryOutcome, ordered: bool) -> tuple[bool, bool]:
    """Return (strict, match)."""
    ref = _rows(reference)
    if len(ref) != len(generated.rows):
        return False, False
    strict = len(reference.columns) == len(generated.columns) and _same(
        ref, _rows(generated), ordered
    )
    if strict:
        return True, True
    n_ref, n_gen = len(reference.columns), len(generated.columns)
    if n_gen < n_ref:
        return False, False
    for tried, columns in enumerate(itertools.permutations(range(n_gen), n_ref)):
        if tried >= MAX_PERMUTATIONS:
            break
        if _same(ref, _rows(generated, columns), ordered):
            return False, True
    return False, False


def run_fallback_eval(
    provider: LLMProvider,
    questions: Sequence[FallbackQuestion],
    connect: Callable[[], psycopg.Connection] = connect_assistant,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    with connect() as conn:
        conn.autocommit = True
        for q in questions:
            reference = run_read_only(
                conn, guard_sql(q.reference_sql, FREE_SQL_MAX_ROWS), None, FREE_SQL_TIMEOUT_MS
            )
            record: dict[str, Any] = {"id": q.id, "lang": q.lang, "question": q.question}
            outcome = None
            try:
                generated = write_free_sql(provider, q.question)
                if generated is None:
                    record["category"] = "declined"
                else:
                    record["generated_sql"] = generated.sql
                    record["seconds"] = round(generated.seconds, 1)
                    safe = guard_sql(generated.sql, FREE_SQL_MAX_ROWS)
                    outcome = run_read_only(conn, safe, None, FREE_SQL_TIMEOUT_MS)
            except LLMError as err:
                record["category"] = "llm_error"
                record["error"] = str(err)
            except UnsafeSQLError as err:
                record["category"] = "rejected_by_guardrail"
                record["error"] = str(err)
            except psycopg.Error as err:
                record["category"] = "execution_error"
                record["error"] = str(err).splitlines()[0]
            strict, match = (False, False)
            if outcome is not None:
                strict, match = compare(reference, outcome, q.ordered)
                if not match:
                    record["category"] = (
                        "wrong_row_count"
                        if len(outcome.rows) != len(reference.rows)
                        else "wrong_values"
                    )
            record.update(match=match, strict=strict, reference_sql=q.reference_sql.strip())
            records.append(record)
            print(f"{q.id} {'PASS' if match else 'FAIL'} {record.get('category', '')}", flush=True)
    total = len(records)
    matched = sum(r["match"] for r in records)
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider.name,
        "model": provider.model,
        "questions": total,
        "match": matched,
        "strict": sum(r["strict"] for r in records),
        "accuracy": round(matched / total, 4) if total else 0.0,
        "failure_categories": dict(Counter(r.get("category") for r in records if not r["match"])),
        "records": records,
    }
