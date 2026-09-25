"""Validate (after loading): checks of the pipeline's own work, run in the database.

The rules of ``validate.py`` look at what the filers tagged. These checks look at what this
pipeline did with it, on the loaded database, and answer to the "no silence" contract of the
system this project rebuilds: every filing that failed, every value that was not inserted and
every check that is red appears in ``reports/validation_report.md``, and a red check makes
``sfetl run`` exit with a non-zero code.

* failed filings (download, transform or load) with their error;
* coverage of this run: every filing of the run is in the database, linked to one company,
  with facts (filers reporting only in another currency are listed as explained exceptions);
* referential integrity (orphans), including "the fact's fiscal period belongs to the filing's
  company"; the foreign keys already prevent most of it, the query proves nobody disabled them;
* nil facts: no nil with a value, no NULL without nil;
* ownership: nothing left pending; self-references and cycles listed;
* golden figures: values read by hand from the published XHTML reports, versioned in
  ``golden/golden_figures.yaml``, compared with ``v_financial``;
* acceptance queries: real questions whose answers are printed in the report.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import yaml

from sfetl.config import GOLDEN_FILE


@dataclass(frozen=True)
class Check:
    section: str
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class GoldenFigure:
    lei: str
    company: str
    fiscal_year: int
    metric: str
    printed: str
    scale: int
    expected_eur: Decimal
    statement: str
    source: str

    @property
    def tolerance(self) -> Decimal:
        # xBRL values are the printed number times the scale: half a printed unit is enough
        return Decimal(self.scale) / 2


@dataclass
class DbValidation:
    checks: list[Check] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)  # extra markdown per section
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, section: str, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(section, name, bool(ok), detail))

    def note(self, section: str, line: str) -> None:
        self.sections.setdefault(section, []).append(line)


@dataclass(frozen=True)
class FailedFiling:
    fxo_id: str
    stage: str  # download | transform | load
    error: str


@dataclass(frozen=True)
class RunContext:
    loaded_fxo_ids: Sequence[str]
    failed: Sequence[FailedFiling]
    warnings: Sequence[tuple[str, str]]  # (fxo_id or source, message)
    non_eur_only: Sequence[str]  # fxo_ids whose mapped facts were all in another currency
    unmapped_rows: int
    unmapped_revenue_like: int
    revenue_gap: tuple[int, int] = (0, 0)  # non-financial filings without IFRS revenue, with a hint


def load_golden(path: Path = GOLDEN_FILE) -> list[GoldenFigure]:
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [
        GoldenFigure(
            lei=str(g["lei"]),
            company=str(g["company"]),
            fiscal_year=int(g["fiscal_year"]),
            metric=str(g["metric"]),
            printed=str(g["printed"]),
            scale=int(g["scale"]),
            expected_eur=Decimal(str(g["expected_eur"])),
            statement=str(g["statement"]),
            source=str(g["source"]),
        )
        for g in raw
    ]


ACCEPTANCE_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "Top 5 companies by revenue, latest fiscal year",
        """SELECT company_name, fiscal_year, round(value / 1e6) AS revenue_meur, source_concept
           FROM v_financial
           WHERE metric_code = 'revenue' AND NOT is_nil
             AND fiscal_year = (SELECT max(fiscal_year) FROM v_financial)
           ORDER BY value DESC LIMIT 5""",
    ),
    (
        "Coverage per fiscal year (companies with total assets / with revenue)",
        """SELECT fiscal_year,
                  count(*) FILTER (WHERE metric_code = 'total_assets') AS with_total_assets,
                  count(*) FILTER (WHERE metric_code = 'revenue')      AS with_revenue,
                  count(*) FILTER (WHERE NOT is_reported)              AS derived_values
           FROM v_financial GROUP BY fiscal_year ORDER BY fiscal_year""",
    ),
    (
        "Parents that are themselves loaded companies (latest ESEF statement or GLEIF)",
        """SELECT DISTINCT company_name, relation, source, parent_company_name
           FROM v_ownership WHERE parent_in_dataset
           ORDER BY company_name, relation, source""",
    ),
)


def _one(cur: psycopg.Cursor[Any], query: str, params: Sequence[Any] = ()) -> Any:
    row = cur.execute(query, params).fetchone()  # type: ignore[arg-type]
    return None if row is None else row[0]


ORPHAN_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "financial_fact -> fiscal_period",
        "SELECT count(*) FROM financial_fact f LEFT JOIN fiscal_period p USING (fiscal_period_id)"
        " WHERE p.fiscal_period_id IS NULL",
    ),
    (
        "financial_fact -> metric",
        "SELECT count(*) FROM financial_fact f LEFT JOIN metric m USING (metric_id)"
        " WHERE m.metric_id IS NULL",
    ),
    (
        "financial_fact -> filing",
        "SELECT count(*) FROM financial_fact f LEFT JOIN filing fi USING (filing_id)"
        " WHERE fi.filing_id IS NULL",
    ),
    (
        "fiscal_period -> company",
        "SELECT count(*) FROM fiscal_period p LEFT JOIN company c USING (company_id)"
        " WHERE c.company_id IS NULL",
    ),
    (
        "filing -> company",
        "SELECT count(*) FROM filing fi LEFT JOIN company c USING (company_id)"
        " WHERE c.company_id IS NULL",
    ),
    (
        "validation_issue -> filing",
        "SELECT count(*) FROM validation_issue v LEFT JOIN filing fi USING (filing_id)"
        " WHERE fi.filing_id IS NULL",
    ),
    (
        "ownership -> company",
        "SELECT count(*) FROM ownership o LEFT JOIN company c USING (company_id)"
        " WHERE c.company_id IS NULL",
    ),
    (
        "fact's fiscal period belongs to its filing's company",
        "SELECT count(*) FROM financial_fact f JOIN filing fi USING (filing_id)"
        " JOIN fiscal_period p ON p.fiscal_period_id = f.fiscal_period_id"
        " WHERE p.company_id <> fi.company_id",
    ),
)


def run_db_checks(
    conn: psycopg.Connection, ctx: RunContext, golden: Sequence[GoldenFigure] | None = None
) -> DbValidation:
    v = DbValidation()
    with conn.cursor() as cur:
        _failed_filings(v, ctx)
        _coverage(v, cur, ctx)
        _warnings(v, ctx)
        for name, query in ORPHAN_CHECKS:
            n = _one(cur, query)
            v.add("Referential integrity", name, n == 0, f"orphans: {n}")
        _nil(v, cur)
        _unmapped(v, ctx)
        _ownership(v, cur)
        _golden(v, cur, load_golden() if golden is None else golden)
        _acceptance(v, cur)
        _counts(v, cur)
    return v


def _failed_filings(v: DbValidation, ctx: RunContext) -> None:
    section = "Failed filings"
    v.add(
        section,
        "Every selected filing processed without error",
        not ctx.failed,
        f"{len(ctx.failed)} failed",
    )
    for f in ctx.failed:
        v.note(section, f"- `{f.fxo_id}` ({f.stage}): {f.error}")


def _coverage(v: DbValidation, cur: psycopg.Cursor[Any], ctx: RunContext) -> None:
    section = "Coverage of this run"
    loaded = list(ctx.loaded_fxo_ids)
    n = len(loaded)
    found = _one(cur, "SELECT count(*) FROM filing WHERE fxo_id = ANY(%s)", (loaded,))
    v.add(section, f"Filings of this run in the database = {n}", found == n, f"found: {found}")
    companies = _one(
        cur, "SELECT count(DISTINCT company_id) FROM filing WHERE fxo_id = ANY(%s)", (loaded,)
    )
    v.add(
        section,
        "Every filing links one company",
        n == 0 or 1 <= companies <= n,
        f"{companies} companies for {n} filings",
    )
    empty = [
        r[0]
        for r in cur.execute(
            """SELECT fi.fxo_id FROM filing fi
               WHERE fi.fxo_id = ANY(%s)
                 AND NOT EXISTS (SELECT 1 FROM financial_fact f WHERE f.filing_id = fi.filing_id)
               ORDER BY 1""",
            (loaded,),
        ).fetchall()
    ]
    explained = [f for f in empty if f in set(ctx.non_eur_only)]
    unexplained = [f for f in empty if f not in set(ctx.non_eur_only)]
    listed = f": {'; '.join(unexplained[:5])}" if unexplained else ""
    v.add(
        section,
        "No filing of this run left without facts",
        not unexplained,
        f"{len(unexplained)} unexplained{listed}",
    )
    for fxo in explained:
        v.note(
            section,
            f"- explained: `{fxo}` reports its figures in another currency "
            "(flagged `non_eur_unit`, not converted)",
        )


def _warnings(v: DbValidation, ctx: RunContext) -> None:
    section = "Load warnings (not blocking, read them)"
    if not ctx.warnings:
        v.note(section, "- none: no value was dropped while loading")
    for source, message in ctx.warnings:
        v.note(section, f"- `{source}`: {message}")


def _nil(v: DbValidation, cur: psycopg.Cursor[Any]) -> None:
    section = "Nil facts (not available, never 0)"
    bad = _one(cur, "SELECT count(*) FROM financial_fact WHERE is_nil AND value IS NOT NULL")
    v.add(section, "No nil fact carries a value", bad == 0, f"rows: {bad}")
    bad2 = _one(cur, "SELECT count(*) FROM financial_fact WHERE value IS NULL AND NOT is_nil")
    v.add(section, "No NULL value without is_nil", bad2 == 0, f"rows: {bad2}")
    nil_rows = _one(cur, "SELECT count(*) FROM financial_fact WHERE is_nil")
    derived = _one(cur, "SELECT count(*) FROM v_financial WHERE NOT is_reported")
    v.stats["nil_values_loaded"] = nil_rows
    v.stats["derived_values"] = derived
    v.note(section, f"- values loaded as nil (NULL + is_nil): {nil_rows}")
    v.note(section, f"- derived values (is_reported = false in v_financial): {derived}")


def _unmapped(v: DbValidation, ctx: RunContext) -> None:
    section = "Unmapped extension concepts"
    v.note(
        section,
        f"- {ctx.unmapped_rows} current-year, undimensioned EUR facts of company extension "
        "concepts are not mapped (their meaning lives in each company's taxonomy package); all "
        "listed in `reports/unmapped_concepts.csv`, none dropped silently.",
    )
    without, hinted = ctx.revenue_gap
    v.stats["revenue_gap"] = {
        "without_ifrs_revenue": without,
        "with_revenue_like_extension": hinted,
    }
    v.note(
        section,
        f"- Revenue gap: {without} filings of non-financial companies have no IFRS revenue; "
        f"{hinted} of them tag an extension concept that looks like a revenue line by name "
        f"(`looks_like_revenue`, {ctx.unmapped_revenue_like} rows in the CSV). It is not mapped: "
        "its definition lives in the company's own taxonomy.",
    )


def _ownership(v: DbValidation, cur: psycopg.Cursor[Any]) -> None:
    section = "Ownership"
    pending = _one(cur, "SELECT count(*) FROM ownership WHERE resolution = 'pending'")
    v.add(
        section,
        "No ownership statement left unresolved by the second pass",
        pending == 0,
        f"pending: {pending}",
    )
    rows = cur.execute(
        """SELECT source, resolution, count(*) FROM ownership GROUP BY 1, 2 ORDER BY 1, 2"""
    ).fetchall()
    v.note(section, "\n| source | resolution | statements (all filings) |\n|---|---|---:|")
    for source, resolution, n in rows:
        v.note(section, f"| {source} | {resolution} | {n} |")
    latest = cur.execute(
        """SELECT source, relation,
                  count(*) AS rows,
                  count(*) FILTER (WHERE parent_in_dataset) AS parent_loaded,
                  count(*) FILTER (WHERE resolution = 'unresolved') AS unresolved,
                  count(*) FILTER (WHERE contradiction = 'self_reference') AS self_reference,
                  count(*) FILTER (WHERE contradiction = 'cycle') AS cycle
           FROM v_ownership GROUP BY 1, 2 ORDER BY 1, 2"""
    ).fetchall()
    v.note(
        section,
        "\nCurated view `v_ownership` (latest ESEF statement per company + GLEIF):\n\n"
        "| source | relation | rows | parent is a loaded company | unresolved | "
        "self-reference | cycle |\n|---|---|---:|---:|---:|---:|---:|",
    )
    for row in latest:
        v.note(section, "| " + " | ".join(str(x) for x in row) + " |")
    v.stats["ownership_view"] = [list(r) for r in latest]
    cycles = cur.execute(
        """SELECT DISTINCT company_name, parent_company_name, source, relation
           FROM v_ownership WHERE contradiction = 'cycle' ORDER BY 1, 2"""
    ).fetchall()
    if cycles:
        v.note(section, "\nCycles (each company declares the other as parent):")
        for a, b, source, relation in cycles:
            v.note(section, f"- {a} -> {b} ({source}, {relation})")


def _golden(v: DbValidation, cur: psycopg.Cursor[Any], golden: Sequence[GoldenFigure]) -> None:
    section = "Golden figures (read by hand from the published XHTML reports)"
    if not golden:
        v.note(section, "- no `golden/golden_figures.yaml`: golden checks not run")
        return
    for g in golden:
        row = cur.execute(
            """SELECT value FROM v_financial
               WHERE lei = %s AND fiscal_year = %s AND metric_code = %s""",
            (g.lei, g.fiscal_year, g.metric),
        ).fetchone()
        if row is None:
            loaded = _one(cur, "SELECT count(*) FROM company WHERE lei = %s", (g.lei,))
            if not loaded:
                v.note(section, f"- n/a: {g.company} FY{g.fiscal_year} not loaded")
                continue
        got = None if row is None else row[0]
        ok = got is not None and abs(Decimal(got) - g.expected_eur) <= g.tolerance
        v.add(
            section,
            f"{g.company} FY{g.fiscal_year} {g.metric} = {g.printed} x {g.scale:,}",
            ok,
            f"loaded: {got if got is None else f'{Decimal(got):,.0f}'}",
        )


def _acceptance(v: DbValidation, cur: psycopg.Cursor[Any]) -> None:
    section = "Acceptance queries"
    for title, query in ACCEPTANCE_QUERIES:
        cur.execute(query)  # type: ignore[arg-type]
        rows = cur.fetchall()
        columns = [d.name for d in cur.description or []]
        v.note(section, f"\n### {title} ({len(rows)} rows)\n")
        v.note(section, "| " + " | ".join(columns) + " |")
        v.note(section, "|" + "---|" * len(columns))
        for r in rows[:12]:
            v.note(section, "| " + " | ".join("" if x is None else str(x) for x in r) + " |")
        if len(rows) > 12:
            v.note(section, f"| ... {len(rows) - 12} more rows |")
        v.add(section, f"'{title}' returns rows", len(rows) > 0)


def _counts(v: DbValidation, cur: psycopg.Cursor[Any]) -> None:
    section = "Row counts (whole database)"
    counts = {}
    for table in (
        "company",
        "fiscal_period",
        "filing",
        "metric",
        "financial_fact",
        "validation_issue",
        "ownership",
    ):
        counts[table] = _one(cur, f"SELECT count(*) FROM {table}")
        v.note(section, f"- {table}: {counts[table]}")
    v.stats["row_counts"] = counts
    per_statement = cur.execute(
        """SELECT m.statement, count(*) FROM financial_fact f JOIN metric m USING (metric_id)
           GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    v.note(section, "\n| statement | values |\n|---|---:|")
    for statement, n in per_statement:
        v.note(section, f"| {statement} | {n} |")


def render_db_markdown(v: DbValidation) -> list[str]:
    lines: list[str] = ["## Checks of the pipeline's own work (after loading, in the database)", ""]
    order: list[str] = []
    for c in v.checks:
        if c.section not in order:
            order.append(c.section)
    for s in v.sections:
        if s not in order:
            order.append(s)
    for section in order:
        lines += [f"### {section}", ""]
        for c in v.checks:
            if c.section == section:
                mark = "PASS" if c.ok else "FAIL"
                lines.append(f"- **{mark}** {c.name}" + (f" ({c.detail})" if c.detail else ""))
        lines += v.sections.get(section, [])
        lines.append("")
    return lines
