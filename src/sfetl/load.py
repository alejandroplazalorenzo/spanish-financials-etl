"""Load: one transaction per filing, idempotent, nothing lost without a trace.

* **The filing is the unit of atomicity.** ``load_filing`` writes a company, its fiscal period,
  the filing, its facts, its validation flags and its ownership statements inside one
  transaction. If anything fails, that filing rolls back and the run continues with the next;
  the database never holds half a filing.
* **Idempotent.** Masters are upserted on their natural keys (LEI, filings.xbrl.org id,
  company + fiscal year). Everything a filing owns (facts, flags, ownership statements) is
  deleted by ``filing_id`` and inserted again, so a re-run leaves the database unchanged and
  rows that disappeared from a corrected filing disappear here too.
* **Only what the file brings.** The company upsert never blanks a column the filing does not
  carry, and the name only moves forward to a more recent filing.
* **Collisions are counted, not hidden.** Facts are inserted with ``ON CONFLICT DO NOTHING``:
  if another filing already supplied a (company, fiscal year, metric), the existing value is
  kept, and the number of values not inserted comes back as a warning naming that filing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import psycopg

from sfetl.concepts import METRICS
from sfetl.ownership import (
    CompanyIndex,
    CompanyRef,
    GleifRecord,
    Relation,
    StatementStatus,
    resolve_esef_statement,
    resolve_gleif_parent,
)
from sfetl.transform import FilingResult
from sfetl.validate import Issue


class CatalogMismatchError(RuntimeError):
    """The metric table and ``concepts.METRICS`` disagree: apply the migrations first."""


@dataclass
class FilingLoad:
    fxo_id: str
    filing_id: int
    facts_inserted: int
    facts_not_inserted: int
    warnings: list[str] = field(default_factory=list)


UPSERT_COMPANY = """
INSERT INTO company (lei, name, name_period_end, is_financial, updated_at)
VALUES (%(lei)s, %(name)s, %(period_end)s, %(is_financial)s, now())
ON CONFLICT (lei) DO UPDATE SET
    name = CASE
        WHEN EXCLUDED.name <> '' AND EXCLUDED.name_period_end >= company.name_period_end
        THEN EXCLUDED.name ELSE company.name END,
    name_period_end = GREATEST(company.name_period_end, EXCLUDED.name_period_end),
    updated_at = now()
RETURNING company_id
"""

UPSERT_FISCAL_PERIOD = """
INSERT INTO fiscal_period (company_id, fiscal_year, period_start, period_end, months)
VALUES (%(company_id)s, %(fiscal_year)s, %(period_start)s, %(period_end)s, %(months)s)
ON CONFLICT (company_id, fiscal_year) DO UPDATE SET
    period_start = COALESCE(EXCLUDED.period_start, fiscal_period.period_start),
    period_end = EXCLUDED.period_end,
    months = COALESCE(EXCLUDED.months, fiscal_period.months)
RETURNING fiscal_period_id
"""

UPSERT_FILING = """
INSERT INTO filing (source_filing_id, fxo_id, company_id, fiscal_period_id, period_end,
                    document_period_end, date_added, json_url, report_url, viewer_url,
                    error_count, warning_count, inconsistency_count, numeric_facts, nil_facts,
                    is_financial, loaded_at)
VALUES (%(source_filing_id)s, %(fxo_id)s, %(company_id)s, %(fiscal_period_id)s, %(period_end)s,
        %(document_period_end)s, %(date_added)s, %(json_url)s, %(report_url)s, %(viewer_url)s,
        %(error_count)s, %(warning_count)s, %(inconsistency_count)s, %(numeric_facts)s,
        %(nil_facts)s, %(is_financial)s, now())
ON CONFLICT (source_filing_id) DO UPDATE SET
    fxo_id = EXCLUDED.fxo_id, company_id = EXCLUDED.company_id,
    fiscal_period_id = EXCLUDED.fiscal_period_id, period_end = EXCLUDED.period_end,
    document_period_end = EXCLUDED.document_period_end, date_added = EXCLUDED.date_added,
    json_url = EXCLUDED.json_url, report_url = EXCLUDED.report_url,
    viewer_url = EXCLUDED.viewer_url, error_count = EXCLUDED.error_count,
    warning_count = EXCLUDED.warning_count, inconsistency_count = EXCLUDED.inconsistency_count,
    numeric_facts = EXCLUDED.numeric_facts, nil_facts = EXCLUDED.nil_facts,
    is_financial = EXCLUDED.is_financial, loaded_at = now()
RETURNING filing_id
"""

REFRESH_COMPANY_FINANCIAL = """
UPDATE company c
SET is_financial = EXISTS (SELECT 1 FROM filing f
                           WHERE f.company_id = c.company_id AND f.is_financial)
WHERE c.company_id = %s
"""

INSERT_FACTS = """
INSERT INTO financial_fact (fiscal_period_id, metric_id, value, is_nil, decimals, period_start,
                            period_end, source_concept, filing_id)
SELECT %(fiscal_period_id)s, t.metric_id, t.value, t.is_nil, t.decimals, t.period_start,
       t.period_end, t.source_concept, %(filing_id)s
FROM unnest(%(metric_ids)s::int[], %(values)s::numeric[], %(nils)s::boolean[],
            %(decimals)s::smallint[], %(starts)s::date[], %(ends)s::date[],
            %(concepts)s::text[])
     AS t(metric_id, value, is_nil, decimals, period_start, period_end, source_concept)
ON CONFLICT (fiscal_period_id, metric_id) DO NOTHING
RETURNING metric_id
"""

INSERT_ISSUE = """
INSERT INTO validation_issue (filing_id, rule, severity, metric_id, detail)
VALUES (%(filing_id)s, %(rule)s, %(severity)s, %(metric_id)s, %(detail)s)
"""

INSERT_ESEF_OWNERSHIP = """
INSERT INTO ownership (company_id, source, relation, filing_id, parent_name_raw, parent_name,
                       resolution)
VALUES (%(company_id)s, 'esef', %(relation)s, %(filing_id)s, %(raw)s, %(name)s, %(resolution)s)
"""


def load_catalog(conn: psycopg.Connection) -> dict[str, int]:
    """metric code -> metric_id, after checking the table has every code of the catalogue."""
    rows = conn.execute("SELECT code, metric_id FROM metric").fetchall()
    ids = {code: metric_id for code, metric_id in rows}
    missing = [spec.code for spec in METRICS if spec.code not in ids]
    if missing:
        raise CatalogMismatchError(f"metric table lacks {missing}; run `sfetl migrate`")
    return ids


def _months(result: FilingResult) -> int | None:
    start = result.period_start
    if start is None:
        return None
    return round(((result.meta.period_end - start).days + 1) / 30.4375)


def load_filing(
    conn: psycopg.Connection,
    result: FilingResult,
    issues: Sequence[Issue],
    metric_ids: Mapping[str, int],
) -> FilingLoad:
    """Load one filing in its own transaction (commits on success, rolls back on error)."""
    meta = result.meta
    warnings: list[str] = []
    with conn.transaction(), conn.cursor() as cur:
        company_id = cur.execute(
            UPSERT_COMPANY,
            {
                "lei": meta.lei,
                "name": meta.entity_name,
                "period_end": meta.period_end,
                "is_financial": result.is_financial,
            },
        ).fetchone()[0]  # type: ignore[index]
        fiscal_period_id = cur.execute(
            UPSERT_FISCAL_PERIOD,
            {
                "company_id": company_id,
                "fiscal_year": meta.fiscal_year,
                "period_start": result.period_start,
                "period_end": meta.period_end,
                "months": _months(result),
            },
        ).fetchone()[0]  # type: ignore[index]
        filing_id = cur.execute(
            UPSERT_FILING,
            {
                "source_filing_id": meta.filing_id,
                "fxo_id": meta.fxo_id,
                "company_id": company_id,
                "fiscal_period_id": fiscal_period_id,
                "period_end": meta.period_end,
                "document_period_end": result.document_period_end,
                "date_added": meta.date_added,
                "json_url": meta.json_url,
                "report_url": meta.report_url,
                "viewer_url": meta.viewer_url,
                "error_count": meta.error_count,
                "warning_count": meta.warning_count,
                "inconsistency_count": meta.inconsistency_count,
                "numeric_facts": result.numeric_facts,
                "nil_facts": result.nil_facts,
                "is_financial": result.is_financial,
            },
        ).fetchone()[0]  # type: ignore[index]
        cur.execute(REFRESH_COMPANY_FINANCIAL, (company_id,))

        # Everything this filing owns is replaced as a block.
        for table in ("financial_fact", "validation_issue", "ownership"):
            cur.execute(f"DELETE FROM {table} WHERE filing_id = %s", (filing_id,))  # type: ignore[arg-type]

        values = list(result.metrics.values())
        inserted: list[int] = []
        if values:
            inserted = [
                row[0]
                for row in cur.execute(
                    INSERT_FACTS,
                    {
                        "fiscal_period_id": fiscal_period_id,
                        "filing_id": filing_id,
                        "metric_ids": [metric_ids[v.metric] for v in values],
                        "values": [v.value for v in values],
                        "nils": [v.is_nil for v in values],
                        "decimals": [v.decimals for v in values],
                        "starts": [v.period.start for v in values],
                        "ends": [v.period.end for v in values],
                        "concepts": [v.source_concept for v in values],
                    },
                ).fetchall()
            ]
        lost = len(values) - len(inserted)
        if lost:
            lost_ids = [
                metric_ids[v.metric] for v in values if metric_ids[v.metric] not in inserted
            ]
            holders = cur.execute(
                """SELECT DISTINCT fi.fxo_id FROM financial_fact f
                   JOIN filing fi ON fi.filing_id = f.filing_id
                   WHERE f.fiscal_period_id = %s AND f.metric_id = ANY(%s)""",
                (fiscal_period_id, lost_ids),
            ).fetchall()
            warnings.append(
                f"{lost} value(s) not inserted: FY{meta.fiscal_year} of this company is already "
                f"loaded from {', '.join(h[0] for h in holders)}; the existing values are kept "
                "(ON CONFLICT DO NOTHING)"
            )

        issue_rows = [
            {
                "filing_id": filing_id,
                "rule": i.rule,
                "severity": i.severity,
                "metric_id": metric_ids.get(i.metric) if i.metric else None,
                "detail": i.detail,
            }
            for i in issues
        ]
        if issue_rows:
            cur.executemany(INSERT_ISSUE, issue_rows)

        ownership_rows = [
            {
                "company_id": company_id,
                "relation": p.relation,
                "filing_id": filing_id,
                "raw": p.raw,
                "name": p.name,
                "resolution": "pending" if p.status == "named" else p.status,
            }
            for p in result.parents
        ]
        if ownership_rows:
            cur.executemany(INSERT_ESEF_OWNERSHIP, ownership_rows)

    return FilingLoad(
        fxo_id=meta.fxo_id,
        filing_id=filing_id,
        facts_inserted=len(inserted),
        facts_not_inserted=lost,
        warnings=warnings,
    )


# --------------------------------------------------------------------------------------------
# Second pass: ownership resolution (after every filing is in)
# --------------------------------------------------------------------------------------------

UPSERT_GLEIF_OWNERSHIP = """
INSERT INTO ownership (company_id, source, relation, filing_id, parent_name_raw, parent_name,
                       parent_lei, parent_company_id, resolution, detail)
VALUES (%(company_id)s, 'gleif', %(relation)s, NULL, %(name)s, %(name)s, %(parent_lei)s,
        %(parent_company_id)s, %(resolution)s, %(detail)s)
ON CONFLICT ON CONSTRAINT ownership_one_statement DO UPDATE SET
    parent_name_raw = EXCLUDED.parent_name_raw, parent_name = EXCLUDED.parent_name,
    parent_lei = EXCLUDED.parent_lei, parent_company_id = EXCLUDED.parent_company_id,
    resolution = EXCLUDED.resolution, detail = EXCLUDED.detail, loaded_at = now()
"""


def resolve_ownership(
    conn: psycopg.Connection, gleif: Mapping[str, GleifRecord]
) -> dict[str, Counter[str]]:
    """Write GLEIF rows and resolve every ESEF statement. Returns counts by source/resolution."""
    with conn.transaction(), conn.cursor() as cur:
        companies = [
            CompanyRef(company_id=r[0], lei=r[1].strip(), name=r[2])
            for r in cur.execute("SELECT company_id, lei, name FROM company").fetchall()
        ]
        index = CompanyIndex(companies)
        by_id = {c.company_id: c for c in companies}
        for company in companies:
            record = gleif.get(company.lei)
            if record is None:
                continue
            cur.execute(
                "DELETE FROM ownership WHERE source = 'gleif' AND company_id = %s",
                (company.company_id,),
            )
            for parent in record.parents:
                resolved = resolve_gleif_parent(company, parent, index)
                cur.execute(
                    UPSERT_GLEIF_OWNERSHIP,
                    {
                        "company_id": company.company_id,
                        "relation": parent.relation,
                        "name": parent.parent_name,
                        "parent_lei": resolved.parent_lei,
                        "parent_company_id": resolved.parent_company_id,
                        "resolution": resolved.resolution,
                        "detail": parent.exception,
                    },
                )
        rows = cur.execute(
            """SELECT ownership_id, company_id, relation, parent_name, resolution
               FROM ownership WHERE source = 'esef'"""
        ).fetchall()
        for ownership_id, company_id, relation, name, resolution in rows:
            status: StatementStatus = (
                "named"
                if name is not None
                else ("none_declared" if resolution == "none_declared" else "unparsed")
            )
            company = by_id[company_id]
            record = gleif.get(company.lei)
            rel: Relation = "direct" if relation == "direct" else "ultimate"
            resolved = resolve_esef_statement(
                company, name, status, record.for_relation(rel) if record else None, index
            )
            cur.execute(
                """UPDATE ownership SET parent_company_id = %s, parent_lei = %s, resolution = %s
                   WHERE ownership_id = %s""",
                (
                    resolved.parent_company_id,
                    resolved.parent_lei,
                    resolved.resolution,
                    ownership_id,
                ),
            )
        counts: dict[str, Counter[str]] = {}
        for source, resolution, n in cur.execute(
            "SELECT source, resolution, count(*) FROM ownership GROUP BY 1, 2"
        ).fetchall():
            counts.setdefault(source, Counter())[resolution] = n
    return counts
