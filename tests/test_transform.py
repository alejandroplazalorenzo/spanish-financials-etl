from datetime import date
from decimal import Decimal

import pytest

from sfetl.concepts import DERIVED_LIABILITIES_CONCEPT, METRICS_BY_CODE
from sfetl.oim import Fact, Period, iter_numeric_facts
from sfetl.transform import (
    consistent,
    detect_period_end,
    matches_fiscal_year,
    pick_concept_value,
    resolve_duplicates,
    select_metric,
)

FY2024 = Period(date(2024, 1, 1), date(2024, 12, 31))


def fact(
    fid: str,
    value: str,
    decimals: int | None = -3,
    concept: str = "ifrs-full:Revenue",
    period: Period = FY2024,
    unit: str = "iso4217:EUR",
    dims: dict[str, str] | None = None,
) -> Fact:
    return Fact(fid, concept, Decimal(value), unit, decimals, period, dims or {})


# --- concept mapping -------------------------------------------------------------------------


def test_endesa_metrics_come_from_the_expected_concepts(transformed) -> None:
    result = transformed("endesa_2024.json")
    m = result.metrics
    assert m["revenue"].value == Decimal("20935000000")
    assert m["revenue"].source_concept == "ifrs-full:Revenue"
    assert m["net_profit"].value == Decimal("1893000000")
    assert m["net_profit_parent"].value == Decimal("1888000000")
    assert m["total_assets"].value == Decimal("37345000000")
    assert m["operating_profit"].source_concept == "ifrs-full:ProfitLossFromOperatingActivities"
    assert not result.is_financial


def test_revenue_and_operating_income_is_not_used_as_revenue(transformed) -> None:
    # Endesa also tags RevenueAndOperatingIncome (21,307 M EUR); revenue must stay 20,935 M EUR
    assert transformed("endesa_2024.json").metrics["revenue"].value == Decimal("20935000000")


def test_total_liabilities_derived_from_components_when_total_is_not_tagged(transformed) -> None:
    liabilities = transformed("amper_2024.json").metrics["total_liabilities"]
    assert liabilities.source_concept == DERIVED_LIABILITIES_CONCEPT
    assert liabilities.value == Decimal("133896000") + Decimal("147086000")
    assert len(liabilities.fact_ids) == 2


def test_bank_is_detected_and_gets_no_revenue_or_current_items(transformed) -> None:
    result = transformed("bankinter_2024.json")
    assert result.is_financial
    assert "revenue" not in result.metrics
    assert "current_assets" not in result.metrics
    assert result.metrics["total_liabilities"].source_concept == "ifrs-full:Liabilities"


# --- scaling / decimals ----------------------------------------------------------------------


def test_decimals_are_carried_not_applied(transformed) -> None:
    revenue = transformed("endesa_2024.json").metrics["revenue"]
    assert revenue.decimals == -6
    assert revenue.value == Decimal("20935000000")  # not 20935 * 10**6 applied twice


# --- period selection ------------------------------------------------------------------------


def test_current_year_is_picked_not_the_comparative(transformed, load_report) -> None:
    facts = list(iter_numeric_facts(load_report("endesa_2024.json")))
    years = {f.period.end.year for f in facts if f.concept == "ifrs-full:Revenue"}
    assert {2023, 2024} <= years  # the fixture holds both years
    revenue = transformed("endesa_2024.json").metrics["revenue"]
    assert revenue.period == FY2024


def test_non_calendar_fiscal_year(transformed, fixture_meta) -> None:
    meta = fixture_meta["inditex_fy2024.json"]
    assert meta.period_end == date(2025, 1, 31)
    assert meta.fiscal_year == 2024
    revenue = transformed("inditex_fy2024.json").metrics["revenue"]
    assert revenue.period == Period(date(2024, 2, 1), date(2025, 1, 31))
    assert revenue.value == Decimal("38632000000")


@pytest.mark.parametrize(
    ("period", "period_type", "expected"),
    [
        (FY2024, "duration", True),
        (Period(date(2024, 7, 1), date(2024, 12, 31)), "duration", False),  # half year
        (Period(date(2023, 1, 1), date(2024, 12, 31)), "duration", False),  # two years
        (Period(None, date(2024, 12, 31)), "instant", True),
        (Period(None, date(2023, 12, 31)), "instant", False),
        (FY2024, "instant", False),
    ],
)
def test_matches_fiscal_year(period: Period, period_type: str, expected: bool) -> None:
    assert matches_fiscal_year(period, period_type, date(2024, 12, 31)) is expected  # type: ignore[arg-type]


def test_detect_period_end_ignores_stray_dates() -> None:
    facts = [fact(str(i), "1", period=Period(None, date(2024, 12, 31))) for i in range(12)]
    facts += [fact(f"old{i}", "1", period=Period(None, date(2023, 12, 31))) for i in range(12)]
    facts.append(fact("stray", "1", period=Period(None, date(2030, 12, 31))))
    assert detect_period_end(facts) == date(2024, 12, 31)


# --- dimensions ------------------------------------------------------------------------------


def test_dimensioned_facts_are_not_consolidated_totals(transformed, load_report) -> None:
    facts = list(iter_numeric_facts(load_report("endesa_2024.json")))
    total = transformed("endesa_2024.json").metrics["total_equity"]
    breakdown = [
        f
        for f in facts
        if f.concept == "ifrs-full:Equity"
        and f.has_dimensions
        and f.period.end == date(2024, 12, 31)
    ]
    assert breakdown, "fixture should contain equity broken down by component"
    assert all(f.value != total.value for f in breakdown)
    assert total.value == Decimal("9053000000")


def test_dimensioned_fact_is_ignored_even_if_alone_with_a_total() -> None:
    notes: list = []
    facts = [
        fact("seg", "999", dims={"ifrs-full:SegmentsAxis": "x:RetailMember"}),
        fact("tot", "100"),
    ]
    chosen = pick_concept_value(
        "ifrs-full:Revenue", facts, "duration", date(2024, 12, 31), "revenue", notes
    )
    assert chosen is not None and chosen.fact_id == "tot"


# --- units -----------------------------------------------------------------------------------


def test_non_eur_filing_loads_nothing_and_records_the_unit(transformed) -> None:
    result = transformed("berkeley_2024.json")
    assert result.metrics == {}
    units = {o.detail.rsplit(" ", 1)[-1] for o in result.observations if o.rule == "non_eur_unit"}
    assert units == {"iso4217:AUD", "iso4217:AUD/xbrli:shares"}  # amounts and EPS


# --- duplicates ------------------------------------------------------------------------------


def test_real_consistent_duplicates_are_resolved(transformed) -> None:
    result = transformed("endesa_2024.json")
    assert result.duplicate_groups > 0
    assert not [o for o in result.observations if o.rule == "inconsistent_duplicate"]


def test_duplicates_keep_the_most_precise_fact() -> None:
    rounded = fact("a", "20935000000", decimals=-6)
    precise = fact("b", "20935412000", decimals=-3)
    chosen, ok = resolve_duplicates([rounded, precise])
    assert chosen.fact_id == "b"
    assert ok


def test_identical_duplicates_keep_document_order() -> None:
    chosen, ok = resolve_duplicates([fact("first", "5"), fact("second", "5")])
    assert chosen.fact_id == "first" and ok


def test_inf_precision_beats_any_number() -> None:
    chosen, _ = resolve_duplicates([fact("a", "5.00", decimals=2), fact("b", "5", decimals=None)])
    assert chosen.fact_id == "b"


def test_inconsistent_duplicates_are_kept_but_reported() -> None:
    notes: list = []
    facts = [fact("a", "100000", decimals=-3), fact("b", "250000", decimals=-3)]
    chosen = pick_concept_value(
        "ifrs-full:Revenue", facts, "duration", date(2024, 12, 31), "revenue", notes
    )
    assert chosen is not None and chosen.fact_id == "a"
    assert [n.rule for n in notes] == ["inconsistent_duplicate"]


def test_consistency_uses_the_lower_precision() -> None:
    assert consistent(fact("a", "1234000", decimals=-3), fact("b", "1235000", decimals=-3)) is False
    assert consistent(fact("a", "1234000", decimals=-3), fact("b", "1234400", decimals=-2)) is True
    assert consistent(fact("a", "1000000", decimals=-6), fact("b", "1234400", decimals=-3)) is True


# --- nil facts: "not available" is neither a value nor zero -----------------------------------


def nil_fact(fid: str, concept: str = "ifrs-full:Revenue") -> Fact:
    return Fact(fid, concept, None, "iso4217:EUR", None, FY2024, {}, is_nil=True)


def test_a_value_beats_a_nil_duplicate() -> None:
    chosen, ok = resolve_duplicates([nil_fact("n"), fact("v", "100")])
    assert chosen.fact_id == "v" and ok


def test_only_nil_gives_a_nil_metric_not_zero() -> None:
    spec = METRICS_BY_CODE["revenue"]
    value = select_metric(spec, {"ifrs-full:Revenue": [nil_fact("n")]}, date(2024, 12, 31), [])
    assert value is not None and value.is_nil and value.value is None


def test_second_concept_with_a_value_beats_a_nil_first_concept() -> None:
    spec = METRICS_BY_CODE["revenue"]
    grouped = {
        "ifrs-full:Revenue": [nil_fact("n")],
        "ifrs-full:RevenueFromContractsWithCustomers": [
            fact("v", "7", concept="ifrs-full:RevenueFromContractsWithCustomers")
        ],
    }
    value = select_metric(spec, grouped, date(2024, 12, 31), [])
    assert value is not None and not value.is_nil and value.value == Decimal("7")


# --- catalogue units ---------------------------------------------------------------------------


def test_earnings_per_share_is_read_in_eur_per_share(transformed) -> None:
    eps = transformed("endesa_2024.json").metrics["basic_eps"]
    assert Decimal("1.5") < eps.value < Decimal("2.5")  # EUR per share, not EUR


def test_forty_metrics_are_read_from_a_full_filing(transformed) -> None:
    assert len(transformed("endesa_2024.json").metrics) >= 30


# --- extensions and ownership ------------------------------------------------------------------


def test_extension_concepts_are_reported_not_mapped(transformed) -> None:
    result = transformed("endesa_2024.json")
    assert result.unmapped, "fixture keeps a few current-year EUR extension facts"
    assert all(not u.concept.startswith("ifrs-full:") for u in result.unmapped)
    assert all(u.value is not None for u in result.unmapped)


def test_revenue_hint_is_only_a_hint() -> None:
    from sfetl.transform import UnmappedFact

    assert UnmappedFact("rep:Sales", "duration", Decimal(1), -6).looks_like_revenue
    assert not UnmappedFact("rep:Sales", "instant", Decimal(1), -6).looks_like_revenue
    assert not UnmappedFact("x:OtherReserves", "duration", Decimal(1), -6).looks_like_revenue


def test_parent_statements_are_extracted(transformed) -> None:
    parents = {p.relation: p for p in transformed("prosegur_cash_2024.json").parents}
    assert set(parents) == {"direct", "ultimate"}
    assert parents["direct"].status == "named"
    assert parents["direct"].name is not None and "Prosegur" in parents["direct"].name


def test_revenue_hint_is_narrow() -> None:
    from sfetl.transform import UnmappedFact

    def hint(concept: str) -> bool:
        return UnmappedFact(concept, "duration", Decimal(1), -3).looks_like_revenue

    assert hint("x:RevenuesNotIncludingFinancialIncome")
    assert hint("x:ImporteNetoDeLaCifraDeNegocios")
    assert hint("x:Ingresos")
    assert not hint("x:ActivosNoCorrientesMantenidosParaLaVenta")  # held for sale
    assert not hint("x:ProceedsFromSalesOfInvestmentProperty")
    assert not hint("x:InterestRevenueForInsuranceAssets")
