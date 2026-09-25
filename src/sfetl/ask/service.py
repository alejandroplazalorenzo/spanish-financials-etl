"""The assistant: intents first, free SQL only as a flagged last resort, everything logged.

Flow of one message (the same for the CLI and the Telegram adapter):

1. If the session has a pending A/B choice and the message is "A" or "B", resolve it without
   calling the model. Any other message cancels the pending choice.
2. The model routes the question to an intent of the catalogue (with the previous question of
   the session as context for follow-ups). It returns only ``{intent_id, params, alternative}``.
3. Two different intents -> the user is asked A or B (``assistant.pending``).
4. One intent -> parameters are checked and bound; a missing required one is asked for; the
   fixed SQL runs as ``sfetl_assistant`` in a read-only transaction; the answer carries the
   intent's title and caveat. No rows -> a message with what IS loaded.
5. No intent -> the model writes one SELECT over the curated views; it is checked by the
   guardrails and run the same way. The answer ALWAYS says, in its text, that it was generated
   on the fly and not verified (with or without rows).

Every message ends up as one row of ``assistant.query_log`` (mode, intent, params, generated
SQL, row count, latencies, error), which feeds ``sfetl report uncovered`` and ``/stats``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from sfetl.ask.classify import Classification, classify, write_free_sql
from sfetl.ask.guardrails import UnsafeSQLError, guard_sql
from sfetl.ask.intents import INTENTS, Intent, bind, render_title
from sfetl.ask.llm import LLMError, LLMProvider, provider_from_env
from sfetl.ask.render import Answer, render_rows
from sfetl.db import connect_assistant

FREE_SQL_NOTE = (
    "NOT VERIFIED: generated on the fly by the language model from the schema; it is not one "
    "of the prepared, tested queries. Check it before relying on it."
)
MAX_ROWS = 500
FREE_SQL_MAX_ROWS = 30
INTENT_TIMEOUT_MS = 5000
FREE_SQL_TIMEOUT_MS = 5000
PENDING_TTL = "24 hours"

_CHOICE_RE = re.compile(
    r"^(?:option |opcion |opción |la |el )?(a|b|1|2|first|second|primera|segunda)[).]?$",
    re.IGNORECASE,
)


def parse_choice(text: str) -> int | None:
    match = _CHOICE_RE.match(text.strip().lower())
    if not match:
        return None
    return 0 if match.group(1) in {"a", "1", "first", "primera"} else 1


@dataclass
class QueryOutcome:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    seconds: float


def run_read_only(
    conn: psycopg.Connection,
    sql: str,
    params: dict[str, Any] | None,
    timeout_ms: int,
    max_rows: int = MAX_ROWS,
) -> QueryOutcome:
    """Run one query in a read-only transaction with a local statement timeout."""
    started = time.perf_counter()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")  # type: ignore[arg-type]
        cur.execute(sql, params)  # type: ignore[arg-type]
        columns = [d.name for d in cur.description or []]
        rows = [tuple(r) for r in cur.fetchmany(max_rows)]
    return QueryOutcome(columns, rows, time.perf_counter() - started)


class Assistant:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        connect: Callable[[], psycopg.Connection] = connect_assistant,
        intents: Sequence[Intent] = INTENTS,
    ) -> None:
        self._provider = provider
        self.connect = connect
        self.intents = {i.id: i for i in intents}

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = provider_from_env()
        return self._provider

    # ---- public entry points -------------------------------------------------------------

    def ask(self, question: str, session_id: str = "cli", user_id: str | None = None) -> Answer:
        question = question.strip()
        with self.connect() as conn:
            conn.autocommit = True
            pending = self._pending(conn, session_id)
            if pending is not None:
                self._delete_pending(conn, pending[0])
                choice = parse_choice(question)
                if choice is not None:
                    return self._resolve_choice(conn, pending, choice, session_id, user_id)
            previous = self._previous_question(conn, session_id)
            try:
                classification = classify(
                    self.provider, question, previous, list(self.intents.values())
                )
            except LLMError as err:
                return self._error(conn, session_id, user_id, question, f"LLM: {err}")
            llm_ms = int(classification.seconds * 1000)
            intent = self.intents.get(classification.intent_id or "")
            alternative = classification.alternative
            if intent is not None and alternative is not None:
                return self._ambiguous(conn, session_id, user_id, question, classification, llm_ms)
            if intent is not None:
                return self._run_intent(
                    conn, session_id, user_id, question, intent, classification.params, llm_ms
                )
            return self._free_sql(conn, session_id, user_id, question, previous, llm_ms)

    def choose(
        self, pending_id: int, choice: int, session_id: str, user_id: str | None = None
    ) -> Answer | None:
        """Resolve a pending A/B question by id (the Telegram buttons)."""
        with self.connect() as conn:
            conn.autocommit = True
            row = conn.execute(
                """SELECT pending_id, question, options FROM assistant.pending
                   WHERE pending_id = %s AND session_id = %s""",
                (pending_id, session_id),
            ).fetchone()
            if row is None:
                return None
            self._delete_pending(conn, pending_id)
            return self._resolve_choice(conn, row, choice, session_id, user_id)

    def rate(self, query_id: int, rating: int) -> bool:
        if rating not in (-1, 1):
            raise ValueError("rating must be -1 or 1")
        with self.connect() as conn:
            conn.autocommit = True
            cur = conn.execute(
                "UPDATE assistant.query_log SET rating = %s WHERE query_id = %s",
                (rating, query_id),
            )
            return cur.rowcount == 1

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            conn.autocommit = True
            by_mode = dict(
                conn.execute(
                    "SELECT mode, count(*) FROM assistant.query_log GROUP BY mode ORDER BY 2 DESC"
                ).fetchall()
            )
            ratings = conn.execute(
                """SELECT count(*) FILTER (WHERE rating = 1), count(*) FILTER (WHERE rating = -1)
                   FROM assistant.query_log"""
            ).fetchone()
            latency = conn.execute(
                """SELECT round(avg(llm_ms)), round(avg(query_ms))
                   FROM assistant.query_log WHERE llm_ms IS NOT NULL"""
            ).fetchone()
            uncovered = conn.execute(
                """SELECT question, count(*) FROM assistant.query_log
                   WHERE mode IN ('unanswered', 'free_sql')
                   GROUP BY question ORDER BY 2 DESC, 1 LIMIT 5"""
            ).fetchall()
        return {
            "questions": sum(by_mode.values()),
            "by_mode": by_mode,
            "rated_up": ratings[0] if ratings else 0,
            "rated_down": ratings[1] if ratings else 0,
            "avg_llm_ms": None if not latency or latency[0] is None else int(latency[0]),
            "avg_query_ms": None if not latency or latency[1] is None else int(latency[1]),
            "top_uncovered": [(q, n) for q, n in uncovered],
        }

    # ---- steps ---------------------------------------------------------------------------

    def _run_intent(
        self,
        conn: psycopg.Connection,
        session_id: str,
        user_id: str | None,
        question: str,
        intent: Intent,
        raw_params: dict[str, str],
        llm_ms: int | None,
    ) -> Answer:
        values, missing = bind(intent, raw_params)
        shown = {k: v for k, v in values.items() if v is not None}
        if missing:
            query_id = self._log(
                conn,
                session_id,
                user_id,
                question,
                "unanswered",
                intent_id=intent.id,
                params=shown,
                llm_ms=llm_ms,
                error=f"missing parameter: {missing[0].name}",
            )
            return Answer(
                text=f"I need one more detail to answer that: {missing[0].description}.",
                mode="unanswered",
                query_id=query_id,
                intent_id=intent.id,
            )
        title = render_title(intent, values)
        try:
            outcome = run_read_only(conn, intent.sql, values, INTENT_TIMEOUT_MS)
        except psycopg.Error as err:
            return self._error(
                conn, session_id, user_id, question, _first_line(err), intent.id, shown
            )
        query_id = self._log(
            conn,
            session_id,
            user_id,
            question,
            "intent",
            intent_id=intent.id,
            params=shown,
            row_count=len(outcome.rows),
            llm_ms=llm_ms,
            query_ms=int(outcome.seconds * 1000),
        )
        if not outcome.rows:
            text = f"{title}\n\n{self._coverage_message(conn, intent, values)}"
            return Answer(
                text=text, mode="intent", query_id=query_id, title=title, intent_id=intent.id
            )
        body = render_rows(outcome.columns, outcome.rows)
        text = f"{title}\n\n{body}"
        if intent.caveat:
            text += f"\n\nNote: {intent.caveat}"
        return Answer(
            text=text,
            mode="intent",
            query_id=query_id,
            title=title,
            columns=outcome.columns,
            rows=outcome.rows,
            intent_id=intent.id,
        )

    def _free_sql(
        self,
        conn: psycopg.Connection,
        session_id: str,
        user_id: str | None,
        question: str,
        previous: str | None,
        llm_ms: int | None,
    ) -> Answer:
        try:
            generated = write_free_sql(self.provider, question, previous)
        except LLMError as err:
            return self._error(conn, session_id, user_id, question, f"LLM: {err}")
        total_llm_ms = (llm_ms or 0) + (int(generated.seconds * 1000) if generated else 0)
        if generated is None:
            query_id = self._log(
                conn,
                session_id,
                user_id,
                question,
                "unanswered",
                llm_ms=total_llm_ms,
                error="no intent fits and the model declined to write SQL",
            )
            return Answer(
                text="I cannot answer that from this database yet. Ask about the figures, "
                "ratios, filings, data-quality flags or ownership of the companies loaded "
                "(try 'what is loaded?').",
                mode="unanswered",
                query_id=query_id,
            )
        try:
            safe_sql = guard_sql(generated.sql, FREE_SQL_MAX_ROWS)
        except UnsafeSQLError as err:
            query_id = self._log(
                conn,
                session_id,
                user_id,
                question,
                "unanswered",
                generated_sql=generated.sql,
                llm_ms=total_llm_ms,
                error=f"guardrail: {err}",
            )
            return Answer(
                text="I cannot answer that from this database yet (the query I drafted was "
                "refused by the safety checks).",
                mode="unanswered",
                query_id=query_id,
                sql=generated.sql,
            )
        try:
            outcome = run_read_only(conn, safe_sql, None, FREE_SQL_TIMEOUT_MS, FREE_SQL_MAX_ROWS)
        except psycopg.Error as err:
            return self._error(
                conn, session_id, user_id, question, _first_line(err), generated_sql=safe_sql
            )
        query_id = self._log(
            conn,
            session_id,
            user_id,
            question,
            "free_sql",
            generated_sql=safe_sql,
            row_count=len(outcome.rows),
            llm_ms=total_llm_ms,
            query_ms=int(outcome.seconds * 1000),
        )
        body = render_rows(outcome.columns, outcome.rows) if outcome.rows else "No rows."
        text = f"{generated.title}\n\n{FREE_SQL_NOTE}\n\n{body}\n\nSQL: {safe_sql}"
        return Answer(
            text=text,
            mode="free_sql",
            query_id=query_id,
            title=generated.title,
            columns=outcome.columns,
            rows=outcome.rows,
            sql=safe_sql,
        )

    def _ambiguous(
        self,
        conn: psycopg.Connection,
        session_id: str,
        user_id: str | None,
        question: str,
        c: Classification,
        llm_ms: int,
    ) -> Answer:
        assert c.intent_id is not None and c.alternative is not None
        first, second = self.intents[c.intent_id], self.intents[c.alternative.intent_id]
        options = [
            {"intent_id": first.id, "params": c.params},
            {"intent_id": second.id, "params": c.alternative.params},
        ]
        titles = [
            render_title(first, bind(first, c.params)[0]),
            render_title(second, bind(second, c.alternative.params)[0]),
        ]
        conn.execute(
            f"DELETE FROM assistant.pending WHERE created_at < now() - interval '{PENDING_TTL}'"
        )
        pending_id = conn.execute(
            """INSERT INTO assistant.pending (session_id, question, options)
               VALUES (%s, %s, %s::jsonb) RETURNING pending_id""",
            (session_id, question, json.dumps(options)),
        ).fetchone()[0]  # type: ignore[index]
        query_id = self._log(
            conn,
            session_id,
            user_id,
            question,
            "ambiguous",
            intent_id=first.id,
            params=c.params,
            llm_ms=llm_ms,
        )
        text = (
            "I am not sure which of these you mean:\n"
            f"  A) {titles[0]}\n  B) {titles[1]}\n"
            "Reply A or B."
        )
        return Answer(
            text=text, mode="ambiguous", query_id=query_id, choices=titles, pending_id=pending_id
        )

    def _resolve_choice(
        self,
        conn: psycopg.Connection,
        pending: tuple[Any, ...],
        choice: int,
        session_id: str,
        user_id: str | None,
    ) -> Answer:
        _, question, options = pending
        options = json.loads(options) if isinstance(options, str) else options
        chosen = options[choice] if 0 <= choice < len(options) else None
        intent = self.intents.get(chosen["intent_id"]) if chosen else None
        if intent is None:
            return Answer(text="That choice has expired; please ask again.", mode="unanswered")
        return self._run_intent(
            conn, session_id, user_id, question, intent, dict(chosen["params"]), None
        )

    # ---- helpers -------------------------------------------------------------------------

    def _coverage_message(
        self, conn: psycopg.Connection, intent: Intent, values: dict[str, str | None]
    ) -> str:
        """No rows: say what IS loaded, so "no data" does not read as "does not exist"."""
        try:
            years, companies = run_read_only(
                conn,
                """SELECT string_agg(DISTINCT fiscal_year::text, ', ' ORDER BY fiscal_year::text),
                          count(DISTINCT lei) FROM v_filing""",
                None,
                INTENT_TIMEOUT_MS,
            ).rows[0]
            parts = [f"No rows. Loaded: fiscal years {years or 'none'}, {companies} companies."]
            for name in ("company", "company1", "company2"):
                if values.get(name):
                    found = run_read_only(
                        conn,
                        "SELECT count(*) FROM v_company WHERE translate(lower(search_text), "
                        "'áéíóúñ', 'aeioun') LIKE '%%' || translate(lower(%(q)s), 'áéíóúñ', "
                        "'aeioun') || '%%'",
                        {"q": values[name]},
                        INTENT_TIMEOUT_MS,
                    ).rows[0][0]
                    if not found:
                        parts.append(
                            f"No loaded company matches '{values[name]}': try part of its legal "
                            "name ('list companies' shows them all)."
                        )
            if intent.area == "ownership":
                parts.append("Ownership comes from the parent names tagged in ESEF and GLEIF.")
            if values.get("year"):
                parts.append(f"The year asked was FY{values['year']}.")
            return " ".join(parts)
        except psycopg.Error:
            return "No rows."

    def _pending(self, conn: psycopg.Connection, session_id: str) -> tuple[Any, ...] | None:
        return conn.execute(
            f"""SELECT pending_id, question, options FROM assistant.pending
                WHERE session_id = %s AND created_at > now() - interval '{PENDING_TTL}'
                ORDER BY created_at DESC LIMIT 1""",  # type: ignore[arg-type]
            (session_id,),
        ).fetchone()

    def _delete_pending(self, conn: psycopg.Connection, pending_id: int) -> None:
        conn.execute("DELETE FROM assistant.pending WHERE pending_id = %s", (pending_id,))

    def _previous_question(self, conn: psycopg.Connection, session_id: str) -> str | None:
        row = conn.execute(
            """SELECT question FROM assistant.query_log WHERE session_id = %s
               ORDER BY created_at DESC, query_id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def _log(
        self,
        conn: psycopg.Connection,
        session_id: str,
        user_id: str | None,
        question: str,
        mode: str,
        *,
        intent_id: str | None = None,
        params: dict[str, Any] | None = None,
        generated_sql: str | None = None,
        row_count: int | None = None,
        llm_ms: int | None = None,
        query_ms: int | None = None,
        error: str | None = None,
    ) -> int | None:
        """Never lets a logging failure break the answer."""
        try:
            row = conn.execute(
                """INSERT INTO assistant.query_log (session_id, user_id, question, mode,
                       intent_id, params, generated_sql, row_count, llm_ms, query_ms, error)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                   RETURNING query_id""",
                (
                    session_id,
                    user_id,
                    question,
                    mode,
                    intent_id,
                    None if params is None else json.dumps(params),
                    generated_sql,
                    row_count,
                    llm_ms,
                    query_ms,
                    None if error is None else error[:500],
                ),
            ).fetchone()
            return None if row is None else int(row[0])
        except psycopg.Error:
            return None

    def _error(
        self,
        conn: psycopg.Connection,
        session_id: str,
        user_id: str | None,
        question: str,
        error: str,
        intent_id: str | None = None,
        params: dict[str, Any] | None = None,
        generated_sql: str | None = None,
    ) -> Answer:
        query_id = self._log(
            conn,
            session_id,
            user_id,
            question,
            "error",
            intent_id=intent_id,
            params=params,
            generated_sql=generated_sql,
            error=error,
        )
        return Answer(
            text=f"Something failed while answering ({error[:200]}). Please try again.",
            mode="error",
            query_id=query_id,
            intent_id=intent_id,
        )


def _first_line(err: BaseException) -> str:
    text = str(err).strip()
    return text.splitlines()[0] if text else type(err).__name__
