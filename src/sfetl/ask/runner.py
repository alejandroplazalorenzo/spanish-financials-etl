"""Question -> LLM -> guarded SQL -> read-only execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
import requests

from sfetl.ask.guardrails import UnsafeSQLError, guard_sql
from sfetl.ask.llm import chat, extract_sql
from sfetl.ask.prompt import build_system_prompt
from sfetl.db import connect_reader

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 5000

ErrorKind = Literal["llm", "guardrail", "execution"]


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


@dataclass
class AskResult:
    question: str
    reply: str = ""
    sql_generated: str | None = None
    sql_executed: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    llm_seconds: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def company_names() -> list[str]:
    with connect_reader() as conn:
        return [row[0] for row in conn.execute("SELECT name FROM company ORDER BY name")]


def run_readonly(
    sql: str, max_rows: int = MAX_ROWS, timeout_ms: int = STATEMENT_TIMEOUT_MS
) -> QueryResult:
    """Run already-guarded SQL as the read-only role inside a read-only transaction."""
    with connect_reader() as conn:
        conn.read_only = True
        with conn.transaction(force_rollback=True), conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            cur.execute(sql)  # type: ignore[arg-type]
            columns = [d.name for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
    return QueryResult(columns=columns, rows=[tuple(r) for r in rows])


def ask(question: str, system_prompt: str | None = None) -> AskResult:
    result = AskResult(question=question)
    prompt = system_prompt or build_system_prompt(company_names())
    try:
        reply = chat(prompt, question)
    except requests.RequestException as err:
        result.error, result.error_kind = f"LLM call failed: {err}", "llm"
        return result
    result.reply, result.llm_seconds = reply.text, reply.seconds
    result.sql_generated = extract_sql(reply.text)
    try:
        result.sql_executed = guard_sql(result.sql_generated, MAX_ROWS)
    except UnsafeSQLError as err:
        result.error, result.error_kind = str(err), "guardrail"
        return result
    try:
        result.result = run_readonly(result.sql_executed)
    except psycopg.Error as err:
        result.error, result.error_kind = str(err).strip().splitlines()[0], "execution"
    return result
