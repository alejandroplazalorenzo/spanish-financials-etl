"""Mapping from IFRS taxonomy concepts to the canonical metrics of the dataset.

Only ``ifrs-full`` concepts are mapped. Company extension concepts (``rep:Sales``,
``cellnex:...``) are ignored on purpose: their meaning is only defined by anchoring
relationships in each company's taxonomy package, which the xBRL-JSON rendering does not carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PeriodType = Literal["instant", "duration"]


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    period_type: PeriodType
    concepts: tuple[str, ...]  # priority order: the first concept with a value wins
    applies_to_financials: bool = True  # banks/insurers do not report revenue or current items
    core: bool = False  # expected in every filing (checked by validation)
    non_negative: bool = False  # a negative value is a sign error (checked by validation)


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "revenue",
        "duration",
        ("ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"),
        applies_to_financials=False,
        core=True,
        non_negative=True,
    ),
    MetricSpec("operating_profit", "duration", ("ifrs-full:ProfitLossFromOperatingActivities",)),
    MetricSpec("net_profit", "duration", ("ifrs-full:ProfitLoss",), core=True),
    MetricSpec(
        "net_profit_parent", "duration", ("ifrs-full:ProfitLossAttributableToOwnersOfParent",)
    ),
    MetricSpec("total_assets", "instant", ("ifrs-full:Assets",), core=True, non_negative=True),
    MetricSpec("total_equity", "instant", ("ifrs-full:Equity",), core=True),
    MetricSpec(
        "total_liabilities", "instant", ("ifrs-full:Liabilities",), core=True, non_negative=True
    ),
    MetricSpec("cash", "instant", ("ifrs-full:CashAndCashEquivalents",), non_negative=True),
    MetricSpec(
        "current_assets",
        "instant",
        ("ifrs-full:CurrentAssets",),
        applies_to_financials=False,
        non_negative=True,
    ),
    MetricSpec(
        "current_liabilities",
        "instant",
        ("ifrs-full:CurrentLiabilities",),
        applies_to_financials=False,
        non_negative=True,
    ),
)

METRICS_BY_NAME: dict[str, MetricSpec] = {spec.metric: spec for spec in METRICS}

# Total liabilities is often not tagged: the IFRS statement of financial position shows
# non-current and current liabilities, then "total equity and liabilities". When the total is
# missing we derive it from its two components (never from Assets - Equity, which would make
# the balance-identity check meaningless).
LIABILITY_COMPONENTS: tuple[str, str] = (
    "ifrs-full:NoncurrentLiabilities",
    "ifrs-full:CurrentLiabilities",
)
DERIVED_LIABILITIES_CONCEPT = "derived:NoncurrentLiabilities+CurrentLiabilities"

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

EUR_UNIT = "iso4217:EUR"

# A duration counts as "the fiscal year" when it lasts about twelve months.
FISCAL_YEAR_MIN_DAYS = 350
FISCAL_YEAR_MAX_DAYS = 380


def mapped_concepts() -> frozenset[str]:
    concepts = {c for spec in METRICS for c in spec.concepts}
    return frozenset(concepts | set(LIABILITY_COMPONENTS))
