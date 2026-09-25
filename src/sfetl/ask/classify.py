"""The two things the model is asked to do, with their JSON schemas.

1. ``classify``: route a question to one intent of the catalogue and pull out the parameter
   values the user mentioned. The model sees the catalogue (ids, descriptions, parameters) and,
   for follow-ups, the previous question of the same session. It never sees data.
2. ``write_free_sql``: only when no intent fits, write one SELECT over the curated views. The
   SQL is checked by ``guardrails.py`` and executed by the assistant's role; the model never
   sees the result.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sfetl.ask.intents import INTENTS, METRIC_CODES, Intent
from sfetl.ask.llm import LLMProvider

PARAM_ITEM = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
    "required": ["name", "value"],
}


def classification_schema() -> dict[str, Any]:
    """``intent_id`` is a nullable string, as in production, not an enum of the catalogue.

    ``parse_classification`` reads an id outside the catalogue as "no intent". With the ids as
    an enum, qwen3:4b under grammar-constrained decoding never answered null: every question,
    even the weather, was routed to some intent (see the routing benchmark in the README).
    """
    return {
        "type": "object",
        "properties": {
            "intent_id": {"type": ["string", "null"]},
            "params": {"type": "array", "items": PARAM_ITEM},
            "alternative": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "intent_id": {"type": "string"},
                            "params": {"type": "array", "items": PARAM_ITEM},
                        },
                        "required": ["intent_id", "params"],
                    },
                ]
            },
        },
        "required": ["intent_id", "params", "alternative"],
    }


FREE_SQL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sql": {"type": "string"}, "title": {"type": "string"}},
    "required": ["sql", "title"],
}


@dataclass(frozen=True)
class Candidate:
    intent_id: str
    params: dict[str, str]


@dataclass(frozen=True)
class Classification:
    intent_id: str | None
    params: dict[str, str] = field(default_factory=dict)
    alternative: Candidate | None = None
    seconds: float = 0.0


def _catalogue_text(intents: Sequence[Intent]) -> str:
    lines = []
    for i in intents:
        params = "; ".join(
            f"{p.name}{' (required)' if p.required else ''} = {p.description}" for p in i.params
        )
        lines.append(f"- {i.id}{f' [{params}]' if params else ''}: {i.description}")
    return "\n".join(lines)


CLASSIFY_SYSTEM = """\
You route questions about the annual financial statements of Spanish listed companies (ESEF
reports, fiscal years 2023 and 2024, consolidated IFRS figures in EUR) to ONE prepared query of
the list below. You never write SQL and you never see the data. Answer with JSON only.

Prepared queries:
{catalogue}

Rules:
- intent_id: the query that answers the question. If none of them answers it, use null. Do not
  force a match: a question outside the list is answered another way.
- params: only parameters declared by the chosen query, as a list of {{"name", "value"}}.
  Company names exactly as the user wrote them. Years as 4 digits. metric as one of these codes:
  {metrics}.
- Never invent a parameter the user did not mention. A missing required parameter is fine: the
  user will be asked for it.
- alternative: null, unless you are genuinely torn between two prepared queries after reading
  the descriptions. Then give the second one with its params. Do not use it by default.
- The previous question of the same conversation, when given, is only there to understand
  follow-ups such as "and Repsol?" or "¿y en 2023?". Ignore it if the new question stands alone.
- Questions may be in English or Spanish.
"""


def classify(
    provider: LLMProvider,
    question: str,
    previous: str | None = None,
    intents: Sequence[Intent] = INTENTS,
) -> Classification:
    system = CLASSIFY_SYSTEM.format(
        catalogue=_catalogue_text(intents), metrics=", ".join(METRIC_CODES)
    )
    user = (
        f"Previous question: {json.dumps(previous, ensure_ascii=False)}\n" if previous else ""
    ) + f"Question: {json.dumps(question, ensure_ascii=False)}"
    reply = provider.complete_json(system, user, classification_schema())
    return parse_classification(reply.data, intents, reply.seconds)


def _declared_params(intent: Intent | None, raw: Any) -> dict[str, str]:
    """Keep only non-empty values of parameters the intent declares."""
    out: dict[str, str] = {}
    if intent is None or not isinstance(raw, list):
        return out
    declared = {p.name for p in intent.params}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if isinstance(name, str) and isinstance(value, str) and value.strip() and name in declared:
            out[name] = value.strip()
    return out


def parse_classification(
    data: dict[str, Any], intents: Sequence[Intent] = INTENTS, seconds: float = 0.0
) -> Classification:
    by_id = {i.id: i for i in intents}
    intent = by_id.get(data.get("intent_id") or "")
    alternative = None
    alt = data.get("alternative")
    if intent is not None and isinstance(alt, dict):
        alt_intent = by_id.get(alt.get("intent_id") or "")
        if alt_intent is not None and alt_intent.id != intent.id:
            alternative = Candidate(alt_intent.id, _declared_params(alt_intent, alt.get("params")))
    return Classification(
        intent_id=intent.id if intent else None,
        params=_declared_params(intent, data.get("params")),
        alternative=alternative,
        seconds=seconds,
    )


FREE_SQL_SYSTEM = """\
You write ONE read-only PostgreSQL query for a question about the annual financial statements
of Spanish listed companies. You are called only because none of the prepared queries fits the
question. You never see the result. Answer with JSON only: {{"sql": ..., "title": ...}}.

You may read ONLY these views (anything else is refused before it runs):
{views}

Rules:
- One SELECT (WITH is allowed). No semicolon, no INSERT/UPDATE/DELETE/DDL, no functions that
  read the server (pg_*, set_config...).
- Always end with a LIMIT of at most 30.
- Short English column aliases in double quotes.
- Figures are in EUR (basic_eps in EUR per share). value is NULL when is_nil. Prefer rows with
  is_reported when the question is about reported figures.
- Never compute a single figure over several companies unless the question explicitly asks
  for a count or a list; do not present a derived figure as reported.
- If the question cannot be answered with these views (or asks to change data), return
  sql = "" and a title saying why.
- title: a short heading of what the query answers.
"""

VIEWS_DESCRIPTION = f"""\
- v_financial(lei, company_name, is_financial, fiscal_year, fiscal_year_start, fiscal_year_end,
  statement, category, metric_code, metric_label, unit, sort_order, value, is_nil, is_reported,
  source_concept, decimals, fxo_id, viewer_url): one row per company, fiscal year and metric.
  metric_code is one of: {", ".join(METRIC_CODES)}.
  statement is balance_sheet, income_statement or cash_flow.
- v_company(lei, name, country, is_financial, first_fiscal_year, last_fiscal_year, filings,
  search_text): one row per company; is_financial = bank or insurer.
- v_company_ratios(lei, company_name, is_financial, fiscal_year, net_margin, operating_margin,
  equity_ratio, current_ratio): ratios of one company-year as fractions (0.12 = 12 %).
- v_metric(code, label, statement, category, unit, period_type, parent_code, sort_order,
  description): the metric catalogue.
- v_filing(lei, company_name, fiscal_year, fxo_id, period_end, date_added, viewer_url,
  report_url, xbrl_errors, xbrl_warnings, numeric_facts, nil_facts, metrics_loaded,
  flags_error, flags_warning): one row per annual report loaded.
- v_validation_issue(lei, company_name, fiscal_year, fxo_id, rule, severity, metric_code,
  detail): data-quality flags (severity error, warning or info).
- v_ownership(lei, company_name, relation, source, fiscal_year, declared_text, parent_name,
  parent_lei, parent_company_name, parent_in_dataset, resolution, detail, contradictory,
  contradiction): parent (relation direct) and ultimate parent per company, from ESEF (source
  esef) and the LEI register (source gleif)."""


@dataclass(frozen=True)
class FreeSql:
    sql: str
    title: str
    seconds: float


def write_free_sql(
    provider: LLMProvider, question: str, previous: str | None = None
) -> FreeSql | None:
    system = FREE_SQL_SYSTEM.format(views=VIEWS_DESCRIPTION)
    user = (
        f"Previous question: {json.dumps(previous, ensure_ascii=False)}\n" if previous else ""
    ) + f"Question: {json.dumps(question, ensure_ascii=False)}"
    reply = provider.complete_json(system, user, FREE_SQL_SCHEMA)
    sql = str(reply.data.get("sql", "")).strip()
    if not sql:
        return None
    title = str(reply.data.get("title", "")).strip() or "Answer"
    return FreeSql(sql=sql, title=title, seconds=reply.seconds)
