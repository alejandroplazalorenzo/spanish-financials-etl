"""The prompt: a hand-written schema description plus the list of company names in the data."""

from __future__ import annotations

from collections.abc import Sequence

SCHEMA_DESCRIPTION = """\
PostgreSQL 16 database with the audited annual accounts (consolidated IFRS figures) of Spanish
listed companies, taken from their ESEF annual financial reports. All money values are in
euros (EUR), not thousands or millions. fiscal_year is an integer such as 2023 or 2024.

Views (prefer them):
- company_year(lei, company_name, is_financial, fiscal_year, revenue, operating_profit,
  net_profit, net_profit_parent, total_assets, total_equity, total_liabilities, cash,
  current_assets, current_liabilities)
  One row per company and fiscal year. A column is NULL when the company did not report it.
  net_profit = profit for the year (including minority interests);
  net_profit_parent = profit attributable to owners of the parent.
- company_year_ratios(lei, company_name, is_financial, fiscal_year, net_margin,
  operating_margin, equity_ratio, current_ratio)
  Ratios as fractions (0.12 means 12 %). net_margin = net_profit / revenue,
  operating_margin = operating_profit / revenue, equity_ratio = total_equity / total_assets,
  current_ratio = current_assets / current_liabilities.
- year_aggregate_ratios(fiscal_year, is_financial, companies, companies_net_margin, net_margin,
  companies_operating_margin, operating_margin, companies_equity_ratio, equity_ratio)
  Cross-company ratios per fiscal year, already computed as a ratio of sums.

Tables:
- company(lei, name, country, is_financial, updated_at)
  is_financial = true for banks and insurers (they do not report revenue or current assets).
- filing(filing_id, fxo_id, lei, fiscal_year, period_end, document_period_end, date_added,
  json_url, viewer_url, error_count, warning_count, inconsistency_count, numeric_facts,
  loaded_at)  -- one annual report per company and fiscal year
- financial_fact(lei, fiscal_year, metric, value_eur, decimals, period_start, period_end,
  source_concept, filing_id)  -- long format of company_year; metric is one of: revenue,
  operating_profit, net_profit, net_profit_parent, total_assets, total_equity,
  total_liabilities, cash, current_assets, current_liabilities
- metric(metric, label, statement, period_type, description)
- validation_issue(issue_id, filing_id, lei, fiscal_year, rule, severity, metric, detail)
  Data-quality flags. rule is one of: balance_identity, missing_core_metric, sign_check,
  component_bounds, period_consistency, non_eur_unit, inconsistent_duplicate, unique_value.
  severity is one of: error, warning, info.
"""

# v1: the prompt of the first evaluation run (kept so that run can be reproduced).
RULES_V1 = """\
Rules:
1. Answer with exactly ONE PostgreSQL SELECT statement (a WITH ... SELECT is fine) inside a
   ```sql code block. No other text. Never modify data.
2. Find companies by name with ILIKE and wildcards, e.g. company_name ILIKE '%repsol%'.
   Use the legal names listed below (for example BBVA is
   'BANCO BILBAO VIZCAYA ARGENTARIA SOCIEDAD ANONIMA').
3. When ranking, skip NULLs (ORDER BY x DESC NULLS LAST or WHERE x IS NOT NULL).
4. A ratio across several companies is a ratio of sums over the companies that report both
   values (sum(net_profit) / sum(revenue)), never the average of each company's ratio.
5. "Latest year" means the highest fiscal_year present in the data.
6. Return only the columns needed to answer, and include the company name when listing
   companies.
"""


# v2: written after reading the v1 failures on eval/questions.yaml (see README). Each added
# rule targets a failure class seen there, not a single question.
RULES_V2 = (
    RULES_V1
    + """\
7. Use only the columns listed above for each view or table. Ratios (net_margin,
   operating_margin, equity_ratio, current_ratio) exist only in company_year_ratios and
   year_aggregate_ratios, not in company_year. financial_fact has no company_name and no
   metric columns: its value is value_eur for one metric. The company table has name, not
   company_name. Period dates (period_end) are in filing, not in company_year.
8. ALWAYS put % on both sides of a company search term: ILIKE '%vidrala%', never
   ILIKE 'vidrala'. For brands or acronyms, search a distinctive word of the legal name.
9. For a margin or ratio across companies, read year_aggregate_ratios. If you must compute
   one, keep only companies where both values are NOT NULL; never COALESCE them to 0.
10. A loss is a negative net_profit: the largest loss is the lowest net_profit
    (ORDER BY net_profit ASC). "Net profit" / "beneficio neto" means net_profit unless the
    question asks for the part attributable to the parent (net_profit_parent).
11. Questions about reports, filings or which companies filed accounts are answered from
    filing (it also lists companies whose figures could not be loaded); questions about
    figures are answered from company_year and the ratio views.
"""
)

PROMPTS: dict[str, str] = {"v1": RULES_V1, "v2": RULES_V2}
DEFAULT_PROMPT = "v2"


def build_system_prompt(company_names: Sequence[str], version: str = DEFAULT_PROMPT) -> str:
    names = "\n".join(f"- {name}" for name in company_names)
    return f"{SCHEMA_DESCRIPTION}\n{PROMPTS[version]}\nCompanies in the database:\n{names}\n"
