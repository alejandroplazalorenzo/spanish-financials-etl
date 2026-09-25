"""The assistant end to end with a scripted model and the real role (no LLM needed)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
import pytest

from sfetl.ask.llm import ScriptedProvider
from sfetl.ask.service import FREE_SQL_NOTE, Assistant

pytestmark = pytest.mark.integration

Factory = Callable[[str], Callable[[], psycopg.Connection]]


def route(intent_id: str | None, alternative: dict[str, Any] | None = None, **params: str):
    return {
        "intent_id": intent_id,
        "params": [{"name": k, "value": v} for k, v in params.items()],
        "alternative": alternative,
    }


def log_rows(as_role: Factory) -> list[tuple]:
    with as_role("owner")() as conn:
        return conn.execute(
            """SELECT mode, intent_id, params, generated_sql, error, row_count
               FROM assistant.query_log ORDER BY query_id"""
        ).fetchall()


def test_an_intent_answer_has_title_rows_caveat_and_is_logged(as_role: Factory) -> None:
    provider = ScriptedProvider([route("company_metric", company="endesa", year="2024")])
    answer = Assistant(provider, as_role("assistant")).ask("What was Endesa's revenue in 2024?")
    assert answer.mode == "intent" and answer.rows
    assert answer.text.startswith("Revenue of endesa in FY2024")
    assert "20,935.0 M EUR" in answer.text and "Note:" in answer.text
    [(mode, intent_id, params, sql, error, rows)] = log_rows(as_role)
    assert (mode, intent_id, sql, error, rows) == ("intent", "company_metric", None, None, 1)
    assert params == {"company": "endesa", "metric": "revenue", "year": "2024"}  # what ran


def test_a_missing_required_parameter_is_asked_for(as_role: Factory) -> None:
    provider = ScriptedProvider([route("company_parent")])
    answer = Assistant(provider, as_role("assistant")).ask("Who owns it?")
    assert answer.mode == "unanswered" and "one more detail" in answer.text
    assert log_rows(as_role)[0][4] == "missing parameter: company"


def test_ambiguity_is_asked_and_the_letter_resolves_without_the_model(as_role: Factory) -> None:
    alternative = {
        "intent_id": "company_key_figures",
        "params": [{"name": "company", "value": "amper"}],
    }
    provider = ScriptedProvider([route("company_metric", alternative, company="amper")])
    assistant = Assistant(provider, as_role("assistant"))
    first = assistant.ask("Amper?", session_id="s1")
    assert first.mode == "ambiguous" and first.choices == [
        "Revenue of amper",
        "Key figures of amper",
    ]
    second = assistant.ask("B", session_id="s1")  # no scripted reply left: model not called
    assert second.mode == "intent" and second.intent_id == "company_key_figures"


def test_free_sql_is_always_flagged_as_not_verified(as_role: Factory) -> None:
    sql = "SELECT count(*) AS n FROM v_filing WHERE xbrl_warnings >= 0"
    empty_sql = "SELECT company_name FROM v_filing WHERE xbrl_warnings < 0"
    provider = ScriptedProvider(
        [
            route(None),
            {"sql": sql, "title": "Filings"},
            route(None),
            {"sql": empty_sql, "title": "None"},
        ]
    )
    assistant = Assistant(provider, as_role("assistant"))
    with_rows = assistant.ask("How many filings?")
    without_rows = assistant.ask("Filings with negative warnings?")
    for answer in (with_rows, without_rows):
        assert answer.mode == "free_sql"
        assert FREE_SQL_NOTE in answer.text
    assert "No rows." in without_rows.text
    logged = log_rows(as_role)
    assert [r[0] for r in logged] == ["free_sql", "free_sql"]
    assert all(r[3] and "LIMIT 30" in r[3] for r in logged)  # the SQL that ran is kept


def test_free_sql_on_a_raw_table_is_refused_and_logged(as_role: Factory) -> None:
    provider = ScriptedProvider(
        [route(None), {"sql": "SELECT * FROM financial_fact", "title": "t"}]
    )
    answer = Assistant(provider, as_role("assistant")).ask("Show me everything")
    assert answer.mode == "unanswered" and not answer.rows
    mode, _, _, sql, error, _ = log_rows(as_role)[0]
    assert mode == "unanswered" and sql == "SELECT * FROM financial_fact"
    assert error.startswith("guardrail: relation not allowed")


def test_no_rows_explains_what_is_loaded(as_role: Factory) -> None:
    provider = ScriptedProvider([route("company_metric", company="Nonexistent Holdings")])
    answer = Assistant(provider, as_role("assistant")).ask("Revenue of Nonexistent Holdings?")
    assert "No rows. Loaded: fiscal years 2024" in answer.text
    assert "No loaded company matches 'Nonexistent Holdings'" in answer.text


def test_the_previous_question_is_passed_for_follow_ups(as_role: Factory) -> None:
    provider = ScriptedProvider(
        [route("find_company", company="endesa"), route("find_company", company="amper")]
    )
    assistant = Assistant(provider, as_role("assistant"))
    assistant.ask("Is Endesa loaded?", session_id="chat")
    assistant.ask("And Amper?", session_id="chat")
    assert "Is Endesa loaded?" in provider.calls[1][1]


def test_rating_and_stats(as_role: Factory) -> None:
    provider = ScriptedProvider([route("list_metrics"), route(None), {"sql": "", "title": "no"}])
    assistant = Assistant(provider, as_role("assistant"))
    answered = assistant.ask("What metrics are there?")
    assistant.ask("What is the meaning of life?")
    assert answered.query_id is not None and assistant.rate(answered.query_id, 1)
    stats = assistant.stats()
    assert stats["by_mode"] == {"intent": 1, "unanswered": 1}
    assert stats["rated_up"] == 1
    assert stats["top_uncovered"] == [("What is the meaning of life?", 1)]
