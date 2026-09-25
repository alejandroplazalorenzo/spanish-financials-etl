"""Routing benchmark: does the model pick the right intent, and decline when it should?

This is what the production assistant was measured on: the model's job is to route, so the
benchmark checks routing. Each case says what should happen:

* ``intent``: the expected intent id, plus any other intents that are equally acceptable
  (genuine overlaps), and optionally the parameter values that must be extracted;
* ``free_sql``: no intent fits, but the question can be answered from the curated views: the
  classifier must return null and the fallback must produce SQL that passes the guardrails;
* ``decline``: the question cannot be answered from this database (or asks to change it): the
  classifier must return null and no query may be produced (or the guardrails refuse it).

The execution accuracy of the SQL itself is measured only for the fallback, in ``evaluate.py``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from sfetl.ask.classify import classify, write_free_sql
from sfetl.ask.guardrails import UnsafeSQLError, guard_sql
from sfetl.ask.intents import INTENTS_BY_ID, bind, fold
from sfetl.ask.llm import LLMError, LLMProvider

Expect = Literal["intent", "free_sql", "decline"]


@dataclass(frozen=True)
class RoutingCase:
    id: str
    question: str
    lang: str
    expect: Expect
    intent: str | None = None
    also_ok: tuple[str, ...] = ()
    params: dict[str, str] = field(default_factory=dict)
    previous: str | None = None


def load_cases(path: Path) -> list[RoutingCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [
        RoutingCase(
            id=str(c["id"]),
            question=str(c["question"]),
            lang=str(c.get("lang", "en")),
            expect=c["expect"],
            intent=c.get("intent"),
            also_ok=tuple(c.get("also_ok", ())),
            params={str(k): str(v) for k, v in (c.get("params") or {}).items()},
            previous=c.get("previous"),
        )
        for c in raw
    ]
    for case in cases:
        for intent_id in filter(None, (case.intent, *case.also_ok)):
            if intent_id not in INTENTS_BY_ID:
                raise ValueError(f"{case.id}: unknown intent {intent_id}")
    return cases


def params_match(intent_id: str, expected: dict[str, str], got: dict[str, str]) -> bool:
    """Every expected parameter was extracted with an equivalent value (after coercion)."""
    intent = INTENTS_BY_ID[intent_id]
    want, _ = bind(intent, expected)
    have, _ = bind(intent, got)
    for name in expected:
        w, h = want.get(name), have.get(name)
        if w is None or h is None:
            return False
        if fold(w) != fold(h) and fold(w) not in fold(h) and fold(h) not in fold(w):
            return False
    return True


def run_case(provider: LLMProvider, case: RoutingCase) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": case.id,
        "lang": case.lang,
        "question": case.question,
        "expect": case.expect,
        "expected_intent": case.intent,
    }
    try:
        c = classify(provider, case.question, case.previous)
    except LLMError as err:
        record.update(passed=False, error=str(err), got_intent=None)
        return record
    record.update(
        got_intent=c.intent_id,
        got_params=c.params,
        alternative=None if c.alternative is None else c.alternative.intent_id,
        classify_ms=int(c.seconds * 1000),
    )
    if case.expect == "intent":
        accepted = {case.intent, *case.also_ok}
        passed = c.intent_id in accepted
        record["params_ok"] = (
            None
            if not case.params or not passed
            else params_match(c.intent_id or "", case.params, c.params)
        )
        record["passed"] = passed
        return record
    if c.intent_id is not None:
        record["passed"] = False
        return record
    try:
        generated = write_free_sql(provider, case.question, case.previous)
    except LLMError as err:
        record.update(passed=False, error=str(err))
        return record
    sql_ok = False
    if generated is not None:
        record["generated_sql"] = generated.sql
        try:
            record["guarded_sql"] = guard_sql(generated.sql)
            sql_ok = True
        except UnsafeSQLError as err:
            record["guardrail"] = str(err)
    record["passed"] = sql_ok if case.expect == "free_sql" else not sql_ok
    return record


def run_bench(provider: LLMProvider, cases: Sequence[RoutingCase]) -> dict[str, Any]:
    records = []
    for case in cases:
        record = run_case(provider, case)
        records.append(record)
        print(
            f"{case.id} {'PASS' if record['passed'] else 'FAIL'} {case.expect} "
            f"-> {record.get('got_intent')}",
            flush=True,
        )
    by_expect: dict[str, dict[str, int]] = {}
    for r in records:
        slot = by_expect.setdefault(r["expect"], {"n": 0, "passed": 0})
        slot["n"] += 1
        slot["passed"] += int(bool(r["passed"]))
    with_params = [r for r in records if r.get("params_ok") is not None]
    latencies = [r["classify_ms"] for r in records if "classify_ms" in r]
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider.name,
        "model": provider.model,
        "cases": len(records),
        "passed": sum(bool(r["passed"]) for r in records),
        "by_expect": by_expect,
        "by_lang": dict(Counter(r["lang"] for r in records if r["passed"])),
        "alternatives_offered": sum(1 for r in records if r.get("alternative")),
        "params_checked": len(with_params),
        "params_ok": sum(bool(r["params_ok"]) for r in with_params),
        "median_classify_ms": int(statistics.median(latencies)) if latencies else None,
        "records": records,
    }
