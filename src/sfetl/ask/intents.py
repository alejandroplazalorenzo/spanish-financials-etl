"""The catalogue of intents: fixed, tested, parameterised queries over the curated views.

The language model never writes these queries. It only picks one (``classify.py``) and
extracts the parameter values the user mentioned; the values are checked and coerced here,
passed to PostgreSQL as bound parameters, and parameters the intent does not declare are
dropped. A missing required parameter is asked for, never guessed.

Each intent has a title (what the answer is answering, repeated to the user) and, when the
figures need one, a caveat that is printed with the answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from sfetl.concepts import METRICS

ParamKind = Literal["company", "year", "metric", "int", "enum"]
Area = Literal["financial", "ownership", "quality", "catalogue"]


@dataclass(frozen=True)
class Param:
    name: str
    description: str  # shown to the model and to the user when the value is missing
    kind: ParamKind
    required: bool = False
    choices: tuple[str, ...] = ()  # enum only
    default: str | None = None  # bound when the user gives no (valid) value


@dataclass(frozen=True)
class Intent:
    id: str
    description: str  # shown to the model: when to pick this intent
    params: tuple[Param, ...]
    sql: str  # psycopg named parameters %(name)s; every declared parameter is always bound
    title: str  # "{name}" placeholders; "[...]" segments vanish when a placeholder is empty
    area: Area
    caveat: str | None = None

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)


# ---- parameter coercion ----------------------------------------------------------------------

METRIC_CODES = tuple(spec.code for spec in METRICS)
_METRIC_LABELS = {spec.code: spec.label for spec in METRICS}
# Common ways of naming a metric (English and Spanish) -> catalogue code.
METRIC_SYNONYMS: dict[str, str] = {
    "sales": "revenue",
    "turnover": "revenue",
    "ventas": "revenue",
    "ingresos": "revenue",
    "facturacion": "revenue",
    "cifra de negocio": "revenue",
    "cifra de negocios": "revenue",
    "net income": "net_profit",
    "net profit": "net_profit",
    "profit": "net_profit",
    "beneficio": "net_profit",
    "beneficio neto": "net_profit",
    "resultado del ejercicio": "net_profit",
    "resultado neto": "net_profit",
    "ebit": "operating_profit",
    "operating income": "operating_profit",
    "resultado de explotacion": "operating_profit",
    "beneficio operativo": "operating_profit",
    "assets": "total_assets",
    "activo": "total_assets",
    "activo total": "total_assets",
    "equity": "total_equity",
    "patrimonio": "total_equity",
    "patrimonio neto": "total_equity",
    "fondos propios": "total_equity",
    "liabilities": "total_liabilities",
    "pasivo": "total_liabilities",
    "pasivo total": "total_liabilities",
    "debt": "total_liabilities",
    "deuda": "total_liabilities",
    "caja": "cash",
    "tesoreria": "cash",
    "efectivo": "cash",
    "eps": "basic_eps",
    "beneficio por accion": "basic_eps",
    "capex": "capex_ppe",
    "dividendos": "dividends_paid",
    "dividends": "dividends_paid",
    "gastos de personal": "employee_benefits_expense",
    "staff costs": "employee_benefits_expense",
    "flujo de caja operativo": "operating_cash_flow",
    "operating cash flow": "operating_cash_flow",
    "fondo de comercio": "goodwill",
    "existencias": "inventories",
}
_ENUM_SYNONYMS: dict[str, str] = {
    "highest": "top",
    "largest": "top",
    "best": "top",
    "biggest": "top",
    "mayor": "top",
    "mayores": "top",
    "mejores": "top",
    "lowest": "bottom",
    "smallest": "bottom",
    "worst": "bottom",
    "menor": "bottom",
    "menores": "bottom",
    "peores": "bottom",
    "banks": "financial",
    "financials": "financial",
    "financieras": "financial",
    "non-financial": "non_financial",
    "no financieras": "non_financial",
    "balance": "balance_sheet",
    "balance sheet": "balance_sheet",
    "balance de situacion": "balance_sheet",
    "income": "income_statement",
    "p&l": "income_statement",
    "cuenta de resultados": "income_statement",
    "cash flow": "cash_flow",
    "cash flows": "cash_flow",
    "flujos de efectivo": "cash_flow",
    "cycles": "cycle",
    "self": "self_reference",
}


def fold(text: str) -> str:
    """Lower case, no accents, single spaces (for matching user words, not for display)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"\s+", " ", text).strip()


class InvalidParam(ValueError):
    pass


def coerce(param: Param, raw: str) -> str:
    """Validate one value the model extracted; returns the canonical string or raises."""
    value = raw.strip()
    if not value:
        raise InvalidParam("empty")
    if param.kind == "company":
        if len(value) < 2 or len(value) > 80:
            raise InvalidParam("company name must be 2-80 characters")
        return value
    if param.kind == "year":
        match = re.search(r"(?<!\d)(20\d\d)(?!\d)", value)
        if not match:
            raise InvalidParam(f"not a year: {value!r}")
        return match.group(1)
    if param.kind == "int":
        match = re.search(r"\d+", value)
        if not match:
            raise InvalidParam(f"not a number: {value!r}")
        return str(max(1, min(50, int(match.group(0)))))
    if param.kind == "metric":
        folded = fold(value)
        if folded.replace(" ", "_") in METRIC_CODES:
            return folded.replace(" ", "_")
        if folded in METRIC_SYNONYMS:
            return METRIC_SYNONYMS[folded]
        for code, label in _METRIC_LABELS.items():
            if folded == fold(label):
                return code
        raise InvalidParam(f"unknown metric {value!r}")
    folded = fold(value).replace("-", "_") if param.kind == "enum" else value
    folded = _ENUM_SYNONYMS.get(fold(value), folded)
    if folded in param.choices:
        return folded
    raise InvalidParam(f"{value!r} is not one of {', '.join(param.choices)}")


def bind(intent: Intent, raw: dict[str, str]) -> tuple[dict[str, str | None], list[Param]]:
    """Declared parameters only, coerced. Returns (bound values, missing required params).

    Values the intent does not declare are dropped here; invalid optional values are dropped
    too (the query then uses its default), invalid required values count as missing.
    """
    bound: dict[str, str | None] = {p.name: None for p in intent.params}
    missing: list[Param] = []
    for p in intent.params:
        value = raw.get(p.name)
        if value is not None:
            try:
                bound[p.name] = coerce(p, value)
            except InvalidParam:
                bound[p.name] = None
        if bound[p.name] is None:
            if p.required:
                missing.append(p)
            else:
                bound[p.name] = p.default
    return bound, missing


def render_title(intent: Intent, values: dict[str, str | None]) -> str:
    """Fill ``{name}``; drop ``[...]`` segments whose placeholders are empty."""

    def segment(match: re.Match[str]) -> str:
        inner = match.group(1)
        names = re.findall(r"\{(\w+)\}", inner)
        if any(not values.get(n) for n in names):
            return ""
        return inner

    text = re.sub(r"\[([^\]]*)\]", segment, intent.title)
    for name, value in values.items():
        param = intent.param(name)
        if name == "metric":
            shown = _METRIC_LABELS.get(value or "", value)
        elif param is not None and param.kind == "enum" and value:
            shown = value.replace("_", " ")
        else:
            shown = value
        text = text.replace("{" + name + "}", shown or "")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:1].upper() + text[1:]


# ---- SQL building blocks -----------------------------------------------------------------------

_ACCENTS_FROM = "áàâäãéèêëíìîïóòôöõúùûüñç"
_ACCENTS_TO = "aaaaaeeeeiiiiooooouuuunc"


def _folded(expr: str) -> str:
    return f"translate(lower({expr}), '{_ACCENTS_FROM}', '{_ACCENTS_TO}')"


def company_leis(param: str) -> str:
    """LEIs of the companies whose legal name (or self-declared name) contains the text."""
    return (
        f"(SELECT lei FROM v_company WHERE {_folded('search_text')} "
        f"LIKE '%%' || {_folded(f'%({param})s')} || '%%')"
    )


LATEST_YEAR = "(SELECT max(fiscal_year) FROM v_financial)"
YEAR = f"coalesce(%(year)s::int, {LATEST_YEAR})"
METRIC = "coalesce(%(metric)s, 'revenue')"
STATUS = (
    "CASE WHEN is_nil THEN 'nil (not available)' WHEN NOT is_reported THEN 'derived, not reported'"
    " ELSE 'reported' END"
)

P_COMPANY = Param(
    "company",
    "company name as the user wrote it (part of the legal name or brand)",
    "company",
    required=True,
)
P_COMPANY_OPT = Param("company", "company name as the user wrote it", "company")
P_YEAR = Param("year", "fiscal year, 4 digits (default: the latest loaded)", "year")
P_METRIC = Param(
    "metric",
    f"metric code, one of: {', '.join(METRIC_CODES)} (default: revenue)",
    "metric",
    default="revenue",
)
P_DIRECTION = Param(
    "direction",
    "top (highest first) or bottom (lowest first); default top",
    "enum",
    choices=("top", "bottom"),
    default="top",
)
P_N = Param("n", "how many companies to list (default 10, max 50)", "int", default="10")

CAVEAT_TAGGED = (
    "Figures are consolidated, in EUR, as tagged by each company in its ESEF report. A company "
    "missing from the list may present the item with its own extension concept (see "
    "reports/unmapped_concepts.csv); banks and insurers do not present revenue."
)
CAVEAT_RATIOS = (
    "Ratios of one company-year, computed here from reported figures (they are not reported "
    "by the companies). NULL when an input is missing or the denominator is not positive."
)
CAVEAT_OWNERSHIP = (
    "ESEF parent names are free text as tagged. 'self_reference' means the filing names the "
    "company itself as its parent (it heads its own group); 'cycle' means two companies name "
    "each other. GLEIF rows are as reported to the LEI register. Nothing here is corrected."
)

INTENTS: tuple[Intent, ...] = (
    Intent(
        id="company_metric",
        description="Value of ONE metric (revenue, net profit, total assets, cash...) for one "
        "company, in one fiscal year or in every loaded year.",
        params=(P_COMPANY, P_METRIC, P_YEAR),
        sql=f"""
SELECT fiscal_year AS "Fiscal year", value AS "Value", unit AS "Unit", {STATUS} AS "Status",
       company_name AS "Company", metric_label AS "Metric"
FROM v_financial
WHERE lei IN {company_leis("company")}
  AND metric_code = {METRIC}
  AND (%(year)s::int IS NULL OR fiscal_year = %(year)s::int)
ORDER BY company_name, fiscal_year""",
        title="{metric} of {company}[ in FY{year}]",
        area="financial",
        caveat=CAVEAT_TAGGED,
    ),
    Intent(
        id="company_key_figures",
        description="Overview / key figures / profile of one company in one fiscal year: "
        "revenue, operating profit, net profit, assets, equity, liabilities and cash together.",
        params=(P_COMPANY, P_YEAR),
        sql=f"""
SELECT metric_label AS "Metric", value AS "Value", unit AS "Unit", {STATUS} AS "Status",
       company_name AS "Company", fiscal_year AS "Fiscal year"
FROM v_financial v
WHERE lei IN {company_leis("company")}
  AND fiscal_year = coalesce(%(year)s::int,
                             (SELECT max(fiscal_year) FROM v_financial w WHERE w.lei = v.lei))
  AND metric_code IN ('revenue', 'operating_profit', 'net_profit', 'net_profit_parent',
                      'total_assets', 'total_equity', 'total_liabilities', 'cash')
ORDER BY company_name, sort_order""",
        title="Key figures of {company}[ in FY{year}]",
        area="financial",
        caveat=CAVEAT_TAGGED,
    ),
    Intent(
        id="financial_statement",
        description="The lines of one statement (balance sheet, income statement or cash flow "
        "statement) of one company in one fiscal year.",
        params=(
            P_COMPANY,
            Param(
                "statement",
                "balance_sheet, income_statement or cash_flow (default balance_sheet)",
                "enum",
                choices=("balance_sheet", "income_statement", "cash_flow"),
                default="balance_sheet",
            ),
            P_YEAR,
        ),
        sql=f"""
SELECT metric_label AS "Line", value AS "Value", unit AS "Unit", {STATUS} AS "Status",
       company_name AS "Company", fiscal_year AS "Fiscal year"
FROM v_financial v
WHERE lei IN {company_leis("company")}
  AND statement = coalesce(%(statement)s, 'balance_sheet')
  AND fiscal_year = coalesce(%(year)s::int,
                             (SELECT max(fiscal_year) FROM v_financial w WHERE w.lei = v.lei))
ORDER BY company_name, sort_order""",
        title="[{statement} of ]{company}[ in FY{year}]",
        area="financial",
        caveat="Only the lines of the 40-metric catalogue are shown, as tagged. " + CAVEAT_TAGGED,
    ),
    Intent(
        id="company_ratios",
        description="Margins and ratios of one company (net margin, operating margin, equity "
        "ratio, current ratio), per fiscal year.",
        params=(P_COMPANY, P_YEAR),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year",
       round(net_margin * 100, 2) AS "Net margin (%%)",
       round(operating_margin * 100, 2) AS "Operating margin (%%)",
       round(equity_ratio * 100, 2) AS "Equity ratio (%%)",
       round(current_ratio, 2) AS "Current ratio (x)"
FROM v_company_ratios
WHERE lei IN {company_leis("company")}
  AND (%(year)s::int IS NULL OR fiscal_year = %(year)s::int)
ORDER BY company_name, fiscal_year""",
        title="Ratios of {company}[ in FY{year}]",
        area="financial",
        caveat=CAVEAT_RATIOS,
    ),
    Intent(
        id="company_growth",
        description="How a metric of one company changed from one fiscal year to the next "
        "(growth, evolution, year-over-year change).",
        params=(P_COMPANY, P_METRIC),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", metric_label AS "Metric",
       value AS "Value", unit AS "Unit",
       round(100 * (value / NULLIF(GREATEST(lag(value) OVER w, 0), 0) - 1), 2)
           AS "Change vs previous year (%%)"
FROM v_financial
WHERE lei IN {company_leis("company")} AND metric_code = {METRIC} AND NOT is_nil
WINDOW w AS (PARTITION BY lei ORDER BY fiscal_year)
ORDER BY company_name, fiscal_year""",
        title="Change in {metric} of {company}",
        area="financial",
        caveat="The change is NULL when the previous value is missing or not positive. "
        "Each year comes from its own report; restated comparatives are not used.",
    ),
    Intent(
        id="ranking_by_metric",
        description="Ranking of ALL companies by one metric in one fiscal year: largest or "
        "smallest revenue, profit, assets, cash, debt...",
        params=(P_METRIC, P_YEAR, P_DIRECTION, P_N),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", metric_label AS "Metric",
       value AS "Value", unit AS "Unit", {STATUS} AS "Status"
FROM v_financial
WHERE metric_code = {METRIC} AND NOT is_nil AND fiscal_year = {YEAR}
ORDER BY CASE WHEN %(direction)s = 'bottom' THEN value END ASC,
         CASE WHEN %(direction)s IS DISTINCT FROM 'bottom' THEN value END DESC
LIMIT coalesce(%(n)s::int, 10)""",
        title="[{direction} ][{n} ]companies by {metric}[ in FY{year}]",
        area="financial",
        caveat=CAVEAT_TAGGED,
    ),
    Intent(
        id="ranking_by_ratio",
        description="Ranking of ALL companies by a ratio in one fiscal year: highest/lowest "
        "net margin, operating margin, equity ratio or current ratio.",
        params=(
            Param(
                "ratio",
                "net_margin, operating_margin, equity_ratio or current_ratio (default net_margin)",
                "enum",
                choices=("net_margin", "operating_margin", "equity_ratio", "current_ratio"),
                default="net_margin",
            ),
            P_YEAR,
            P_DIRECTION,
            P_N,
        ),
        sql=f"""
WITH r AS (
    SELECT company_name, fiscal_year,
           CASE coalesce(%(ratio)s, 'net_margin')
               WHEN 'operating_margin' THEN operating_margin
               WHEN 'equity_ratio' THEN equity_ratio
               WHEN 'current_ratio' THEN current_ratio
               ELSE net_margin END AS ratio
    FROM v_company_ratios
    WHERE fiscal_year = {YEAR}
)
SELECT company_name AS "Company", fiscal_year AS "Fiscal year",
       coalesce(%(ratio)s, 'net_margin') AS "Ratio",
       CASE WHEN coalesce(%(ratio)s, 'net_margin') = 'current_ratio' THEN round(ratio, 2)
            ELSE round(ratio * 100, 2) END AS "Value",
       CASE WHEN coalesce(%(ratio)s, 'net_margin') = 'current_ratio' THEN 'x'
            ELSE '%%' END AS "Unit"
FROM r
WHERE ratio IS NOT NULL
ORDER BY CASE WHEN %(direction)s = 'bottom' THEN ratio END ASC,
         CASE WHEN %(direction)s IS DISTINCT FROM 'bottom' THEN ratio END DESC
LIMIT coalesce(%(n)s::int, 10)""",
        title="[{direction} ][{n} ]companies by [{ratio}][ in FY{year}]",
        area="financial",
        caveat=CAVEAT_RATIOS + " A company with little revenue can top a margin ranking.",
    ),
    Intent(
        id="compare_companies",
        description="Compare TWO companies on one metric in one fiscal year.",
        params=(
            Param("company1", "first company name as written", "company", required=True),
            Param("company2", "second company name as written", "company", required=True),
            P_METRIC,
            P_YEAR,
        ),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", metric_label AS "Metric",
       value AS "Value", unit AS "Unit", {STATUS} AS "Status"
FROM v_financial
WHERE (lei IN {company_leis("company1")} OR lei IN {company_leis("company2")})
  AND metric_code = {METRIC} AND fiscal_year = {YEAR}
ORDER BY value DESC NULLS LAST""",
        title="{metric}: {company1} vs {company2}[ in FY{year}]",
        area="financial",
        caveat=CAVEAT_TAGGED,
    ),
    Intent(
        id="loss_making_companies",
        description="Companies that made a loss (negative profit for the year) in a fiscal year.",
        params=(P_YEAR,),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", value AS "Profit for the year",
       unit AS "Unit"
FROM v_financial
WHERE metric_code = 'net_profit' AND value < 0 AND fiscal_year = {YEAR}
ORDER BY value""",
        title="Companies with a loss[ in FY{year}]",
        area="financial",
        caveat="Loss = negative ifrs-full:ProfitLoss (including non-controlling interests).",
    ),
    Intent(
        id="companies_missing_metric",
        description="Which companies do NOT report a metric (e.g. revenue) in a fiscal year: "
        "coverage gaps.",
        params=(P_METRIC, P_YEAR),
        sql=f"""
SELECT c.name AS "Company", c.is_financial AS "Bank or insurer"
FROM v_company c
WHERE EXISTS (SELECT 1 FROM v_filing f WHERE f.lei = c.lei AND f.fiscal_year = {YEAR})
  AND NOT EXISTS (SELECT 1 FROM v_financial v
                  WHERE v.lei = c.lei AND v.fiscal_year = {YEAR}
                    AND v.metric_code = {METRIC} AND NOT v.is_nil)
ORDER BY c.is_financial, c.name""",
        title="Companies without {metric}[ in FY{year}]",
        area="quality",
        caveat="Missing = not tagged with the mapped IFRS concept. The company may present it "
        "with its own extension concept: see reports/unmapped_concepts.csv.",
    ),
    Intent(
        id="list_companies",
        description="List the companies in the database, optionally only banks/insurers "
        "(financial) or only the rest (non_financial).",
        params=(
            Param(
                "kind",
                "all, financial (banks and insurers) or non_financial; default all",
                "enum",
                choices=("all", "financial", "non_financial"),
                default="all",
            ),
        ),
        sql="""
SELECT name AS "Company", lei AS "LEI", is_financial AS "Bank or insurer",
       first_fiscal_year AS "From FY", last_fiscal_year AS "To FY"
FROM v_company
WHERE coalesce(%(kind)s, 'all') = 'all'
   OR (%(kind)s = 'financial' AND is_financial)
   OR (%(kind)s = 'non_financial' AND NOT is_financial)
ORDER BY name""",
        title="Companies[ ({kind})]",
        area="catalogue",
    ),
    Intent(
        id="find_company",
        description="Find a company by (part of) its name: is it in the database, its legal "
        "name, LEI and which years are loaded.",
        params=(P_COMPANY,),
        sql=f"""
SELECT name AS "Company", lei AS "LEI", is_financial AS "Bank or insurer",
       first_fiscal_year AS "From FY", last_fiscal_year AS "To FY", filings AS "Filings"
FROM v_company
WHERE lei IN {company_leis("company")}
ORDER BY name""",
        title="Companies matching '{company}'",
        area="catalogue",
    ),
    Intent(
        id="list_metrics",
        description="Which metrics / figures / line items are available in the database.",
        params=(),
        sql="""
SELECT code AS "Code", label AS "Metric", statement AS "Statement", unit AS "Unit",
       parent_code AS "Part of"
FROM v_metric ORDER BY sort_order""",
        title="Metrics available",
        area="catalogue",
    ),
    Intent(
        id="company_filings",
        description="The annual reports (ESEF filings) loaded for one company, with links to "
        "the published report and their data-quality flags.",
        params=(P_COMPANY,),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", fxo_id AS "Filing",
       period_end AS "Period end", metrics_loaded AS "Metrics loaded",
       flags_error AS "Error flags", flags_warning AS "Warning flags",
       'https://filings.xbrl.org' || viewer_url AS "Viewer"
FROM v_filing
WHERE lei IN {company_leis("company")}
ORDER BY company_name, fiscal_year""",
        title="Filings of {company}",
        area="quality",
    ),
    Intent(
        id="company_flags",
        description="Data-quality flags / validation issues / inconsistencies found in the "
        "reports of one company.",
        params=(P_COMPANY, P_YEAR),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", rule AS "Rule",
       severity AS "Severity", metric_code AS "Metric", detail AS "Detail"
FROM v_validation_issue
WHERE lei IN {company_leis("company")}
  AND (%(year)s::int IS NULL OR fiscal_year = %(year)s::int)
ORDER BY company_name, fiscal_year, severity, rule""",
        title="Validation flags of {company}[ in FY{year}]",
        area="quality",
        caveat="Flags never change a value: they point at what does not add up in the report.",
    ),
    Intent(
        id="flags_summary",
        description="Summary of data-quality flags across ALL companies: how many of each rule "
        "and severity.",
        params=(P_YEAR,),
        sql="""
SELECT rule AS "Rule", severity AS "Severity", count(*) AS "Flags",
       count(DISTINCT lei) AS "Companies"
FROM v_validation_issue
WHERE %(year)s::int IS NULL OR fiscal_year = %(year)s::int
GROUP BY rule, severity
ORDER BY count(*) DESC""",
        title="Validation flags by rule[ in FY{year}]",
        area="quality",
    ),
    Intent(
        id="derived_values",
        description="Which figures are derived (computed here) rather than reported by the "
        "company, optionally for one company or year.",
        params=(P_COMPANY_OPT, P_YEAR),
        sql=f"""
SELECT company_name AS "Company", fiscal_year AS "Fiscal year", metric_label AS "Metric",
       value AS "Value", unit AS "Unit", source_concept AS "How it was derived"
FROM v_financial
WHERE NOT is_reported
  AND (%(company)s::text IS NULL OR lei IN {company_leis("company")})
  AND (%(year)s::int IS NULL OR fiscal_year = %(year)s::int)
ORDER BY company_name, fiscal_year, sort_order""",
        title="Derived (not reported) figures[ of {company}][ in FY{year}]",
        area="quality",
        caveat="A derived figure is the sum of reported components when the filer did not tag "
        "the total; it is never presented as reported.",
    ),
    Intent(
        id="company_parent",
        description="Who owns / controls one company: its parent and ultimate parent.",
        params=(P_COMPANY,),
        sql=f"""
SELECT company_name AS "Company", relation AS "Relation", source AS "Source",
       fiscal_year AS "FY", coalesce(parent_company_name, parent_name) AS "Parent",
       parent_lei AS "Parent LEI", parent_in_dataset AS "Parent loaded here",
       resolution AS "Resolved by", contradiction AS "Contradiction"
FROM v_ownership
WHERE lei IN {company_leis("company")}
ORDER BY company_name, relation, source""",
        title="Parent and ultimate parent of {company}",
        area="ownership",
        caveat=CAVEAT_OWNERSHIP,
    ),
    Intent(
        id="companies_owned_by",
        description="Which companies in the database declare a given company as their parent "
        "or ultimate parent (its listed subsidiaries).",
        params=(P_COMPANY,),
        sql=f"""
SELECT DISTINCT parent_company_name AS "Parent", company_name AS "Company",
       relation AS "Relation", source AS "Source"
FROM v_ownership
WHERE parent_in_dataset AND parent_lei IN {company_leis("company")}
ORDER BY 1, 2, 3, 4""",
        title="Companies in the database whose parent is {company}",
        area="ownership",
        caveat=CAVEAT_OWNERSHIP,
    ),
    Intent(
        id="ownership_contradictions",
        description="Contradictory ownership declarations: companies naming themselves as "
        "parent, or two companies naming each other.",
        params=(
            Param(
                "kind",
                "cycle, self_reference or all (default all)",
                "enum",
                choices=("all", "cycle", "self_reference"),
                default="all",
            ),
        ),
        sql="""
SELECT company_name AS "Company", relation AS "Relation", source AS "Source",
       fiscal_year AS "FY", coalesce(parent_company_name, parent_name) AS "Declared parent",
       contradiction AS "Contradiction"
FROM v_ownership
WHERE contradictory
  AND (coalesce(%(kind)s, 'all') = 'all' OR contradiction = %(kind)s)
ORDER BY contradiction, company_name, relation, source""",
        title="Contradictory ownership declarations[ ({kind})]",
        area="ownership",
        caveat=CAVEAT_OWNERSHIP,
    ),
    Intent(
        id="parents_outside_dataset",
        description="Companies whose parent or ultimate parent is NOT one of the loaded "
        "companies (foreign or unlisted owners), with the parent's name.",
        params=(),
        sql="""
SELECT company_name AS "Company", relation AS "Relation", source AS "Source",
       parent_name AS "Parent", parent_lei AS "Parent LEI"
FROM v_ownership
WHERE resolution = 'unresolved'
ORDER BY company_name, relation, source""",
        title="Companies whose parent is outside the database",
        area="ownership",
        caveat=CAVEAT_OWNERSHIP,
    ),
    Intent(
        id="coverage_overview",
        description="What data is loaded: fiscal years, number of companies, filings and "
        "values per year.",
        params=(),
        sql="""
SELECT f.fiscal_year AS "Fiscal year", count(DISTINCT f.lei) AS "Companies",
       count(*) AS "Filings", sum(f.metrics_loaded) AS "Values",
       sum(f.flags_error) AS "Error flags"
FROM v_filing f
GROUP BY f.fiscal_year ORDER BY f.fiscal_year""",
        title="What is loaded",
        area="catalogue",
    ),
)

INTENTS_BY_ID: dict[str, Intent] = {i.id: i for i in INTENTS}
