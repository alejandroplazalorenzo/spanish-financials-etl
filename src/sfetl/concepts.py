"""The governed metric catalogue: IFRS concepts -> canonical metrics.

The catalogue lives in two places on purpose:

* the ``metric`` table (seeded by ``db/migrations/005_surrogate_keys_and_fiscal_period.sql``) is
  what the database, the long view ``v_financial`` and the assistant see: code, label,
  statement, category, unit, period type, parent and order;
* this module says which IFRS concepts feed each code and which checks apply to it.

An integration test compares both, so they cannot drift. Adding a metric is one ``INSERT`` in a
new migration plus one ``MetricSpec`` here: no table or view changes.

Only ``ifrs-full`` concepts are mapped. Company extension concepts (``rep:Sales``, ...) are not:
their meaning is defined by anchoring relationships in each company's taxonomy package, which the
xBRL-JSON rendering does not carry. They are listed in ``reports/unmapped_concepts.csv`` instead
of being dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PeriodType = Literal["instant", "duration"]
Statement = Literal["balance_sheet", "income_statement", "cash_flow"]
Unit = Literal["EUR", "EUR/share"]

# xBRL unit identifiers accepted for each catalogue unit. Anything else is recorded as a flag and
# not loaded (values are never converted between currencies).
XBRL_UNITS: dict[str, str] = {"EUR": "iso4217:EUR", "EUR/share": "iso4217:EUR/xbrli:shares"}
EUR_UNIT = XBRL_UNITS["EUR"]


@dataclass(frozen=True)
class MetricSpec:
    code: str
    label: str
    statement: Statement
    category: str
    period_type: PeriodType
    concepts: tuple[str, ...]  # priority order: the first concept with a value wins
    parent: str | None = None  # display hierarchy (the same parent is seeded in the table)
    unit: Unit = "EUR"
    applies_to_financials: bool = True  # banks/insurers do not present revenue or current items
    core: bool = False  # expected in every filing (checked by validation)
    non_negative: bool = False  # a negative value is a sign error (checked by validation)

    @property
    def xbrl_unit(self) -> str:
        return XBRL_UNITS[self.unit]


def _m(
    code: str,
    label: str,
    statement: Statement,
    category: str,
    period_type: PeriodType,
    concept: str | tuple[str, ...],
    *,
    parent: str | None = None,
    unit: Unit = "EUR",
    applies_to_financials: bool = True,
    core: bool = False,
    non_negative: bool = False,
) -> MetricSpec:
    concepts = (concept,) if isinstance(concept, str) else concept
    return MetricSpec(
        code=code,
        label=label,
        statement=statement,
        category=category,
        period_type=period_type,
        concepts=tuple(c if ":" in c else f"ifrs-full:{c}" for c in concepts),
        parent=parent,
        unit=unit,
        applies_to_financials=applies_to_financials,
        core=core,
        non_negative=non_negative,
    )


BS: Statement = "balance_sheet"
IS: Statement = "income_statement"
CF: Statement = "cash_flow"

# Order = display order (seeded as metric.sort_order). 40 metrics.
# fmt: off
METRICS: tuple[MetricSpec, ...] = (
    # --- statement of financial position (instants) ---
    _m("total_assets", "Total assets", BS, "assets", "instant", "Assets",
       core=True, non_negative=True),
    _m("non_current_assets", "Non-current assets", BS, "assets", "instant", "NoncurrentAssets",
       parent="total_assets", applies_to_financials=False, non_negative=True),
    _m("property_plant_equipment", "Property, plant and equipment", BS, "assets", "instant",
       "PropertyPlantAndEquipment", parent="non_current_assets", non_negative=True),
    _m("right_of_use_assets", "Right-of-use assets", BS, "assets", "instant", "RightofuseAssets",
       parent="non_current_assets", non_negative=True),
    _m("investment_property", "Investment property", BS, "assets", "instant",
       "InvestmentProperty", parent="non_current_assets", non_negative=True),
    _m("goodwill", "Goodwill", BS, "assets", "instant", "Goodwill",
       parent="non_current_assets", non_negative=True),
    _m("intangible_assets", "Intangible assets other than goodwill", BS, "assets", "instant",
       "IntangibleAssetsOtherThanGoodwill", parent="non_current_assets", non_negative=True),
    _m("current_assets", "Current assets", BS, "assets", "instant", "CurrentAssets",
       parent="total_assets", applies_to_financials=False, non_negative=True),
    _m("inventories", "Inventories", BS, "assets", "instant", "Inventories",
       parent="current_assets", non_negative=True),
    _m("trade_receivables", "Trade and other current receivables", BS, "assets", "instant",
       "TradeAndOtherCurrentReceivables", parent="current_assets", non_negative=True),
    _m("cash", "Cash and cash equivalents", BS, "assets", "instant", "CashAndCashEquivalents",
       parent="current_assets", non_negative=True),
    _m("equity_and_liabilities", "Total equity and liabilities", BS, "equity_and_liabilities",
       "instant", "EquityAndLiabilities", non_negative=True),
    _m("total_equity", "Total equity", BS, "equity", "instant", "Equity",
       parent="equity_and_liabilities", core=True),
    _m("equity_parent", "Equity attributable to owners of the parent", BS, "equity", "instant",
       "EquityAttributableToOwnersOfParent", parent="total_equity"),
    _m("issued_capital", "Issued capital", BS, "equity", "instant", "IssuedCapital",
       parent="equity_parent", non_negative=True),
    _m("non_controlling_interests", "Non-controlling interests", BS, "equity", "instant",
       "NoncontrollingInterests", parent="total_equity"),
    _m("total_liabilities", "Total liabilities", BS, "liabilities", "instant", "Liabilities",
       parent="equity_and_liabilities", core=True, non_negative=True),
    _m("non_current_liabilities", "Non-current liabilities", BS, "liabilities", "instant",
       "NoncurrentLiabilities", parent="total_liabilities", applies_to_financials=False,
       non_negative=True),
    _m("current_liabilities", "Current liabilities", BS, "liabilities", "instant",
       "CurrentLiabilities", parent="total_liabilities", applies_to_financials=False,
       non_negative=True),
    _m("trade_payables", "Trade and other current payables", BS, "liabilities", "instant",
       "TradeAndOtherCurrentPayables", parent="current_liabilities", non_negative=True),
    # --- statement of profit or loss (durations) ---
    _m("revenue", "Revenue", IS, "revenue", "duration",
       ("Revenue", "RevenueFromContractsWithCustomers"),
       applies_to_financials=False, core=True, non_negative=True),
    _m("employee_benefits_expense", "Employee benefits expense", IS, "operating", "duration",
       "EmployeeBenefitsExpense"),
    _m("depreciation_amortisation", "Depreciation and amortisation expense", IS, "operating",
       "duration", "DepreciationAndAmortisationExpense"),
    _m("operating_profit", "Operating profit", IS, "operating", "duration",
       "ProfitLossFromOperatingActivities"),
    _m("finance_income", "Finance income", IS, "financial", "duration", "FinanceIncome"),
    _m("finance_costs", "Finance costs", IS, "financial", "duration", "FinanceCosts"),
    _m("profit_before_tax", "Profit before tax", IS, "profit", "duration",
       "ProfitLossBeforeTax"),
    _m("income_tax", "Income tax expense", IS, "tax", "duration",
       "IncomeTaxExpenseContinuingOperations", parent="profit_before_tax"),
    _m("profit_continuing", "Profit from continuing operations", IS, "profit", "duration",
       "ProfitLossFromContinuingOperations", parent="net_profit"),
    _m("profit_discontinued", "Profit from discontinued operations", IS, "profit", "duration",
       "ProfitLossFromDiscontinuedOperations", parent="net_profit"),
    _m("net_profit", "Profit for the year", IS, "profit", "duration", "ProfitLoss", core=True),
    _m("net_profit_parent", "Profit attributable to owners of the parent", IS, "profit",
       "duration", "ProfitLossAttributableToOwnersOfParent", parent="net_profit"),
    _m("net_profit_nci", "Profit attributable to non-controlling interests", IS, "profit",
       "duration", "ProfitLossAttributableToNoncontrollingInterests", parent="net_profit"),
    _m("basic_eps", "Basic earnings per share", IS, "per_share", "duration",
       "BasicEarningsLossPerShare", unit="EUR/share"),
    # --- statement of cash flows (durations) ---
    _m("operating_cash_flow", "Cash flows from operating activities", CF, "cash_flow",
       "duration", "CashFlowsFromUsedInOperatingActivities"),
    _m("investing_cash_flow", "Cash flows from investing activities", CF, "cash_flow",
       "duration", "CashFlowsFromUsedInInvestingActivities"),
    _m("capex_ppe", "Purchase of property, plant and equipment", CF, "cash_flow", "duration",
       "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
       parent="investing_cash_flow", non_negative=True),
    _m("financing_cash_flow", "Cash flows from financing activities", CF, "cash_flow",
       "duration", "CashFlowsFromUsedInFinancingActivities"),
    _m("dividends_paid", "Dividends paid", CF, "cash_flow", "duration",
       "DividendsPaidClassifiedAsFinancingActivities", parent="financing_cash_flow"),
    _m("net_change_in_cash", "Increase (decrease) in cash and cash equivalents", CF,
       "cash_flow", "duration", "IncreaseDecreaseInCashAndCashEquivalents"),
)
# fmt: on

METRICS_BY_CODE: dict[str, MetricSpec] = {spec.code: spec for spec in METRICS}


@dataclass(frozen=True)
class SubtotalCheck:
    """``total`` should equal the weighted sum of ``components`` (checked only when all exist)."""

    name: str
    total: str
    components: tuple[tuple[str, int], ...]


# Warnings, never corrections. Each is an identity of the IFRS statements that holds when the
# filer tags the subtotals it presents; a flag means a line is missing, mis-tagged or presented
# outside the subtotal (held-for-sale items, for instance): worth reading, not fixing.
# fmt: off
SUBTOTAL_CHECKS: tuple[SubtotalCheck, ...] = (
    SubtotalCheck("assets_split", "total_assets",
                  (("non_current_assets", 1), ("current_assets", 1))),
    SubtotalCheck("liabilities_split", "total_liabilities",
                  (("non_current_liabilities", 1), ("current_liabilities", 1))),
    SubtotalCheck("equity_split", "total_equity",
                  (("equity_parent", 1), ("non_controlling_interests", 1))),
    SubtotalCheck("equity_and_liabilities", "equity_and_liabilities",
                  (("total_equity", 1), ("total_liabilities", 1))),
    SubtotalCheck("profit_attribution", "net_profit",
                  (("net_profit_parent", 1), ("net_profit_nci", 1))),
    SubtotalCheck("continuing_plus_discontinued", "net_profit",
                  (("profit_continuing", 1), ("profit_discontinued", 1))),
    SubtotalCheck("tax_bridge", "profit_continuing",
                  (("profit_before_tax", 1), ("income_tax", -1))),
)
# fmt: on

# Total liabilities is often not tagged: the IFRS statement of financial position shows
# non-current and current liabilities, then "total equity and liabilities". When the total is
# missing it is derived from its two components (never from Assets - Equity, which would make the
# balance-identity check pass by construction). A derived figure is marked as such
# (``source_concept`` starts with ``derived:``) and the curated view exposes it as
# ``is_reported = false``: it is never presented as a reported number.
LIABILITY_COMPONENTS: tuple[str, str] = (
    "ifrs-full:NoncurrentLiabilities",
    "ifrs-full:CurrentLiabilities",
)
DERIVED_PREFIX = "derived:"
DERIVED_LIABILITIES_CONCEPT = f"{DERIVED_PREFIX}NoncurrentLiabilities+CurrentLiabilities"

# A filing is treated as a bank or insurer when it reports any of these concepts for the
# current period (consolidated, no dimensions). Checked against the real 2023-2024 filings.
FINANCIAL_MARKERS = frozenset(
    {
        "ifrs-full:DepositsFromCustomers",
        "ifrs-full:LoansAndAdvancesToCustomers",
        "ifrs-full:CashAndBankBalancesAtCentralBanks",
        "ifrs-full:FeeAndCommissionIncome",
        "ifrs-full:InsuranceRevenue",
        "ifrs-full:IncomeArisingFromInsuranceContracts",
        "ifrs-full:InsuranceContractsIssuedThatAreLiabilities",
        "ifrs-full:InsuranceFinanceIncomeExpenses",
    }
)

# Text facts that name the parent and the ultimate parent (IAS 1.138(c)); read by ownership.py.
PARENT_CONCEPTS: dict[str, str] = {
    "ifrs-full:NameOfParentEntity": "direct",
    "ifrs-full:NameOfUltimateParentOfGroup": "ultimate",
}

# Base taxonomies, recognised by the namespace URI declared in the report's documentInfo (the
# prefix is only a local alias). Any other namespace is a company extension.
BASE_NAMESPACE_MARKERS = ("xbrl.ifrs.org/taxonomy", "esma.europa.eu/taxonomy", "xbrl.org/2003")
BASE_TAXONOMY_PREFIXES = frozenset({"ifrs-full"})  # fallback when a report has no namespaces

# A duration counts as "the fiscal year" when it lasts about twelve months.
FISCAL_YEAR_MIN_DAYS = 350
FISCAL_YEAR_MAX_DAYS = 380


def mapped_concepts() -> frozenset[str]:
    concepts = {c for spec in METRICS for c in spec.concepts}
    return frozenset(concepts | set(LIABILITY_COMPONENTS))


def is_extension(concept: str, namespaces: dict[str, str] | None = None) -> bool:
    prefix = concept.split(":", 1)[0]
    uri = (namespaces or {}).get(prefix)
    if uri is None:
        return prefix not in BASE_TAXONOMY_PREFIXES
    return not any(marker in uri for marker in BASE_NAMESPACE_MARKERS)
