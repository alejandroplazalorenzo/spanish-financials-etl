"""Load: idempotent upserts into PostgreSQL (one transaction per run).

Running the same input twice leaves the database unchanged: rows are keyed on natural keys
(LEI, filing id, company-year-metric) and written with ``INSERT ... ON CONFLICT DO UPDATE``.
Validation issues of a filing are replaced as a block, so they always match the last run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from sfetl.transform import FilingResult
from sfetl.validate import ValidationReport

UPSERT_COMPANY = """
INSERT INTO company (lei, name, is_financial, updated_at)
VALUES (%(lei)s, %(name)s, %(is_financial)s, now())
ON CONFLICT (lei) DO UPDATE
SET name = EXCLUDED.name, is_financial = EXCLUDED.is_financial, updated_at = now()
"""

UPSERT_FILING = """
INSERT INTO filing (filing_id, fxo_id, lei, fiscal_year, period_end, document_period_end,
                    date_added, json_url, viewer_url, error_count, warning_count,
                    inconsistency_count, numeric_facts, loaded_at)
VALUES (%(filing_id)s, %(fxo_id)s, %(lei)s, %(fiscal_year)s, %(period_end)s,
        %(document_period_end)s, %(date_added)s, %(json_url)s, %(viewer_url)s,
        %(error_count)s, %(warning_count)s, %(inconsistency_count)s, %(numeric_facts)s, now())
ON CONFLICT (filing_id) DO UPDATE
SET fxo_id = EXCLUDED.fxo_id, lei = EXCLUDED.lei, fiscal_year = EXCLUDED.fiscal_year,
    period_end = EXCLUDED.period_end, document_period_end = EXCLUDED.document_period_end,
    date_added = EXCLUDED.date_added, json_url = EXCLUDED.json_url,
    viewer_url = EXCLUDED.viewer_url, error_count = EXCLUDED.error_count,
    warning_count = EXCLUDED.warning_count, inconsistency_count = EXCLUDED.inconsistency_count,
    numeric_facts = EXCLUDED.numeric_facts, loaded_at = now()
"""

UPSERT_FACT = """
INSERT INTO financial_fact (lei, fiscal_year, metric, value_eur, decimals, period_start,
                            period_end, source_concept, filing_id, loaded_at)
VALUES (%(lei)s, %(fiscal_year)s, %(metric)s, %(value_eur)s, %(decimals)s, %(period_start)s,
        %(period_end)s, %(source_concept)s, %(filing_id)s, now())
ON CONFLICT (lei, fiscal_year, metric) DO UPDATE
SET value_eur = EXCLUDED.value_eur, decimals = EXCLUDED.decimals,
    period_start = EXCLUDED.period_start, period_end = EXCLUDED.period_end,
    source_concept = EXCLUDED.source_concept, filing_id = EXCLUDED.filing_id, loaded_at = now()
"""

DELETE_STALE_FACTS = """
DELETE FROM financial_fact
WHERE lei = %(lei)s AND fiscal_year = %(fiscal_year)s AND NOT (metric = ANY(%(metrics)s))
"""

INSERT_ISSUE = """
INSERT INTO validation_issue (filing_id, lei, fiscal_year, rule, severity, metric, detail)
VALUES (%(filing_id)s, %(lei)s, %(fiscal_year)s, %(rule)s, %(severity)s, %(metric)s, %(detail)s)
"""


@dataclass(frozen=True)
class LoadStats:
    companies: int
    filings: int
    facts: int
    issues: int


def company_rows(results: Sequence[FilingResult]) -> list[dict[str, object]]:
    """One row per LEI: name from the most recent filing; financial if any filing says so."""
    latest: dict[str, FilingResult] = {}
    financial: dict[str, bool] = {}
    for r in results:
        lei = r.meta.lei
        if lei not in latest or r.meta.fiscal_year > latest[lei].meta.fiscal_year:
            latest[lei] = r
        financial[lei] = financial.get(lei, False) or r.is_financial
    return [
        {"lei": lei, "name": r.meta.entity_name, "is_financial": financial[lei]}
        for lei, r in sorted(latest.items())
    ]


def filing_row(r: FilingResult) -> dict[str, object]:
    m = r.meta
    return {
        "filing_id": m.filing_id,
        "fxo_id": m.fxo_id,
        "lei": m.lei,
        "fiscal_year": m.fiscal_year,
        "period_end": m.period_end,
        "document_period_end": r.document_period_end,
        "date_added": m.date_added,
        "json_url": m.json_url,
        "viewer_url": m.viewer_url,
        "error_count": m.error_count,
        "warning_count": m.warning_count,
        "inconsistency_count": m.inconsistency_count,
        "numeric_facts": r.numeric_facts,
    }


def fact_rows(r: FilingResult) -> list[dict[str, object]]:
    return [
        {
            "lei": r.meta.lei,
            "fiscal_year": r.meta.fiscal_year,
            "metric": v.metric,
            "value_eur": v.value,
            "decimals": v.decimals,
            "period_start": v.period.start,
            "period_end": v.period.end,
            "source_concept": v.source_concept,
            "filing_id": r.meta.filing_id,
        }
        for v in r.metrics.values()
    ]


def load(
    conn: psycopg.Connection, results: Sequence[FilingResult], report: ValidationReport
) -> LoadStats:
    companies = company_rows(results)
    facts = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(UPSERT_COMPANY, companies)
        cur.executemany(UPSERT_FILING, [filing_row(r) for r in results])
        for r in results:
            rows = fact_rows(r)
            cur.execute(
                DELETE_STALE_FACTS,
                {"lei": r.meta.lei, "fiscal_year": r.meta.fiscal_year, "metrics": list(r.metrics)},
            )
            if rows:
                cur.executemany(UPSERT_FACT, rows)
            facts += len(rows)
        filing_ids = [r.meta.filing_id for r in results]
        cur.execute("DELETE FROM validation_issue WHERE filing_id = ANY(%s)", (filing_ids,))
        issue_rows = [
            {
                "filing_id": i.filing_id,
                "lei": i.lei,
                "fiscal_year": i.fiscal_year,
                "rule": i.rule,
                "severity": i.severity,
                "metric": i.metric,
                "detail": i.detail,
            }
            for i in report.issues
        ]
        if issue_rows:
            cur.executemany(INSERT_ISSUE, issue_rows)
    return LoadStats(
        companies=len(companies), filings=len(results), facts=facts, issues=len(report.issues)
    )
