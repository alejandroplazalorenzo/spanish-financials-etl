"""Smoke test with the assistant's real role: every intent, and every write it makes.

Why it exists: in production a feature broke for users because one column GRANT was missing on
a table the assistant writes to, and it was found by pressing the button. This runs, connected
as ``sfetl_assistant`` (never as the owner):

* the SQL of every intent of the catalogue, with parameters taken from the loaded data;
* each write the assistant performs on its own schema (log a question, rate it, store and
  delete an A/B choice, store, shorten and delete pages), inside a transaction that is rolled
  back, so the smoke test leaves no trace;
* two things that must FAIL: reading a raw table, and rewriting a logged question.

``sfetl smoke`` prints the result; ``tests/integration/test_smoke_intents.py`` runs it in CI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from sfetl.ask.intents import INTENTS, bind
from sfetl.ask.service import INTENT_TIMEOUT_MS, run_read_only
from sfetl.db import connect_assistant


@dataclass(frozen=True)
class SmokeResult:
    name: str
    ok: bool
    rows: int | None = None
    detail: str = ""


def sample_params(conn: psycopg.Connection) -> dict[str, str]:
    """Parameter values that exist in the loaded data (read through the curated views)."""
    companies = [
        r[0]
        for r in run_read_only(
            conn,
            """SELECT company_name FROM v_financial GROUP BY company_name
               ORDER BY count(*) DESC, company_name LIMIT 2""",
            None,
            INTENT_TIMEOUT_MS,
        ).rows
    ]
    year = run_read_only(
        conn, "SELECT max(fiscal_year) FROM v_financial", None, INTENT_TIMEOUT_MS
    ).rows[0][0]
    first = companies[0] if companies else "none"
    second = companies[1] if len(companies) > 1 else first
    return {
        "company": first,
        "company1": first,
        "company2": second,
        "year": str(year or 2024),
        "metric": "revenue",
        "ratio": "net_margin",
        "statement": "balance_sheet",
        "direction": "top",
        "n": "5",
        "kind": "all",
    }


def _expect_denied(conn: psycopg.Connection, name: str, sql: str) -> SmokeResult:
    try:
        with conn.transaction():
            conn.execute(sql)  # type: ignore[arg-type]
    except psycopg.errors.InsufficientPrivilege:
        return SmokeResult(name, True, detail="denied, as it must be")
    return SmokeResult(name, False, detail="NOT denied")


def _writes(conn: psycopg.Connection) -> list[SmokeResult]:
    results: list[SmokeResult] = []

    def step(name: str, sql: str, params: tuple[object, ...] = ()) -> int | None:
        try:
            with conn.transaction():
                cur = conn.execute(sql, params)  # type: ignore[arg-type]
                row = cur.fetchone() if cur.description else None
            results.append(SmokeResult(name, True))
            return None if row is None else int(row[0])
        except psycopg.Error as err:
            results.append(SmokeResult(name, False, detail=str(err).splitlines()[0]))
            return None

    with conn.transaction(force_rollback=True):
        qid = step(
            "log a question (INSERT assistant.query_log)",
            "INSERT INTO assistant.query_log (session_id, question, mode) "
            "VALUES ('smoke', 'smoke test', 'intent') RETURNING query_id",
        )
        if qid is not None:
            step(
                "rate it (UPDATE rating)",
                "UPDATE assistant.query_log SET rating = 1 WHERE query_id = %s",
                (qid,),
            )
            results.append(
                _expect_denied(
                    conn,
                    "rewrite a logged question (UPDATE question) is refused",
                    f"UPDATE assistant.query_log SET question = 'x' WHERE query_id = {qid}",
                )
            )
        pid = step(
            "store an A/B choice (INSERT assistant.pending)",
            "INSERT INTO assistant.pending (session_id, question, options) "
            "VALUES ('smoke', 'q', '[]'::jsonb) RETURNING pending_id",
        )
        if pid is not None:
            step(
                "drop the choice (DELETE assistant.pending)",
                "DELETE FROM assistant.pending WHERE pending_id = %s",
                (pid,),
            )
        page = step(
            "store pages (INSERT assistant.page)",
            "INSERT INTO assistant.page (session_id, pages) "
            "VALUES ('smoke', ARRAY['p2', 'p3']) RETURNING page_id",
        )
        if page is not None:
            step(
                "send one page (UPDATE pages)",
                "UPDATE assistant.page SET pages = pages[2:] WHERE page_id = %s",
                (page,),
            )
            step(
                "drop the pages (DELETE assistant.page)",
                "DELETE FROM assistant.page WHERE page_id = %s",
                (page,),
            )
        step(
            "read the log (SELECT assistant.query_log)", "SELECT count(*) FROM assistant.query_log"
        )
        results.append(
            _expect_denied(
                conn,
                "read a raw table (financial_fact) is refused",
                "SELECT count(*) FROM financial_fact",
            )
        )
    return results


def run_smoke(
    connect: Callable[[], psycopg.Connection] = connect_assistant,
) -> tuple[list[SmokeResult], dict[str, str]]:
    results: list[SmokeResult] = []
    with connect() as conn:
        conn.autocommit = True
        user = conn.execute("SELECT current_user").fetchone()
        results.append(
            SmokeResult(
                "connected as sfetl_assistant",
                bool(user and user[0] == "sfetl_assistant"),
                detail=str(user[0] if user else "?"),
            )
        )
        sample = sample_params(conn)
        for intent in INTENTS:
            values, missing = bind(intent, {p.name: sample[p.name] for p in intent.params})
            if missing:
                results.append(SmokeResult(f"intent {intent.id}", False, detail="no sample"))
                continue
            try:
                outcome = run_read_only(conn, intent.sql, values, INTENT_TIMEOUT_MS)
                results.append(SmokeResult(f"intent {intent.id}", True, len(outcome.rows)))
            except psycopg.Error as err:
                results.append(
                    SmokeResult(f"intent {intent.id}", False, detail=str(err).splitlines()[0])
                )
        results += _writes(conn)
    return results, sample
