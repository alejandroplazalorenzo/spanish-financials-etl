"""Execution-accuracy evaluation of the ask module.

A question passes when the generated query returns the same result as the reference query:

* values are normalised (numbers rounded to 4 decimals, text trimmed, dates as ISO strings);
* rows are compared as a multiset, or as a list when the question sets ``ordered: true``;
* ``strict`` requires the same columns in the same order; ``match`` (the headline number) also
  accepts extra columns in the generated result, as long as some choice of its columns
  reproduces the reference result exactly. Asking "which company..." and getting the name plus
  the revenue is a correct answer; getting another company is not.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from sfetl.ask.guardrails import guard_sql
from sfetl.ask.prompt import DEFAULT_PROMPT, build_system_prompt
from sfetl.ask.runner import AskResult, QueryResult, ask, company_names, run_readonly
from sfetl.config import ollama_model

MAX_PERMUTATIONS = 50_000


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    lang: str
    reference_sql: str
    ordered: bool = False


def load_questions(path: Path) -> list[Question]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Question(
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


def _rows(result: QueryResult, columns: Sequence[int] | None = None) -> list[tuple[Any, ...]]:
    idx = list(range(len(result.columns))) if columns is None else list(columns)
    return [tuple(normalize(row[i]) for i in idx) for row in result.rows]


def _same(a: list[tuple[Any, ...]], b: list[tuple[Any, ...]], ordered: bool) -> bool:
    return a == b if ordered else Counter(a) == Counter(b)


def compare(reference: QueryResult, generated: QueryResult, ordered: bool) -> tuple[bool, bool]:
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


def failure_category(res: AskResult, reference: QueryResult, match: bool) -> str | None:
    if match:
        return None
    if res.error_kind:
        return {
            "llm": "llm_error",
            "guardrail": "rejected_by_guardrail",
            "execution": "execution_error",
        }[res.error_kind]
    assert res.result is not None
    if not res.result.rows and reference.rows:
        return "empty_result"
    if len(res.result.rows) != len(reference.rows):
        return "wrong_row_count"
    return "wrong_values"


def _jsonable(result: QueryResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "columns": result.columns,
        "rows": [[str(v) if v is not None else None for v in r] for r in result.rows[:20]],
    }


def run_eval(questions: Sequence[Question], prompt_version: str = DEFAULT_PROMPT) -> dict[str, Any]:
    prompt = build_system_prompt(company_names(), prompt_version)
    records: list[dict[str, Any]] = []
    for q in questions:
        reference = run_readonly(guard_sql(q.reference_sql))
        res = ask(q.question, system_prompt=prompt)
        strict, match = (False, False)
        if res.result is not None:
            strict, match = compare(reference, res.result, q.ordered)
        records.append(
            {
                "id": q.id,
                "lang": q.lang,
                "question": q.question,
                "ordered": q.ordered,
                "match": match,
                "strict": strict,
                "category": failure_category(res, reference, match),
                "error": res.error,
                "generated_sql": res.sql_generated,
                "executed_sql": res.sql_executed,
                "reference_sql": q.reference_sql.strip(),
                "reference_result": _jsonable(reference),
                "generated_result": _jsonable(res.result),
                "llm_seconds": round(res.llm_seconds, 1),
            }
        )
        print(f"{q.id} {'PASS' if match else 'FAIL'} {records[-1]['category'] or ''}", flush=True)
    total = len(records)
    matched = sum(r["match"] for r in records)
    by_lang = {
        lang: {
            "n": sum(r["lang"] == lang for r in records),
            "match": sum(r["match"] for r in records if r["lang"] == lang),
        }
        for lang in sorted({r["lang"] for r in records})
    }
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": ollama_model(),
        "prompt_version": prompt_version,
        "questions": total,
        "match": matched,
        "strict": sum(r["strict"] for r in records),
        "accuracy": round(matched / total, 4) if total else 0.0,
        "by_lang": by_lang,
        "failure_categories": dict(Counter(r["category"] for r in records if r["category"])),
        "records": records,
    }


def write_eval(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
