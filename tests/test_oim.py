from datetime import date
from decimal import Decimal

from sfetl.oim import iter_numeric_facts, iter_text_facts, parse_period


def test_instant_at_midnight_belongs_to_previous_day() -> None:
    # OIM writes the xBRL 2.1 instant "2024-12-31" as midnight at the start of 2025-01-01
    period = parse_period("2025-01-01T00:00:00")
    assert period.is_instant
    assert period.end == date(2024, 12, 31)


def test_duration_is_converted_to_inclusive_dates() -> None:
    period = parse_period("2024-01-01T00:00:00/2025-01-01T00:00:00")
    assert period.start == date(2024, 1, 1)
    assert period.end == date(2024, 12, 31)
    assert period.days == 366  # 2024 is a leap year


def test_explicit_end_of_day_and_date_only_values() -> None:
    assert parse_period("2024-12-31T24:00:00").end == date(2024, 12, 31)
    assert parse_period("2024-12-31").end == date(2024, 12, 31)


def test_text_facts_are_read_separately(load_report) -> None:
    report = load_report("prosegur_cash_2024.json")
    parents = list(iter_text_facts(report, frozenset({"ifrs-full:NameOfParentEntity"})))
    assert len(parents) == 1 and "Prosegur" in parents[0].value


def test_numeric_facts_skip_text_blocks(load_report) -> None:
    report = load_report("endesa_2024.json")
    text_facts = [f for f in report["facts"].values() if "unit" not in f["dimensions"]]
    assert text_facts, "fixture should contain at least one non-numeric fact"
    facts = list(iter_numeric_facts(report))
    assert len(facts) == len(report["facts"]) - len(text_facts)
    assert all(f.unit for f in facts)


def test_values_are_already_in_euros_and_decimals_is_precision(load_report) -> None:
    facts = list(iter_numeric_facts(load_report("endesa_2024.json")))
    revenue_2024 = [
        f
        for f in facts
        if f.concept == "ifrs-full:Revenue"
        and not f.has_dimensions
        and f.period.end == date(2024, 12, 31)
    ]
    assert revenue_2024
    fact = revenue_2024[0]
    assert fact.decimals == -6  # rounded to the million in the report...
    assert fact.value == Decimal("20935000000.0")  # ...but the value is in euros, not millions


def test_inf_decimals_and_nil_values() -> None:
    report = {
        "facts": {
            "a": {
                "value": "12.5",
                "decimals": "INF",
                "dimensions": {
                    "concept": "ifrs-full:Revenue",
                    "entity": "scheme:X",
                    "period": "2024-01-01T00:00:00/2025-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                },
            },
            "b": {
                "value": None,
                "dimensions": {
                    "concept": "ifrs-full:Assets",
                    "entity": "scheme:X",
                    "period": "2025-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                },
            },
        }
    }
    facts = {f.fact_id: f for f in iter_numeric_facts(report)}
    assert facts["a"].decimals is None and not facts["a"].is_nil
    # a nil fact is kept as "not available": no value, flagged, never turned into 0
    assert facts["b"].is_nil and facts["b"].value is None


def test_taxonomy_dimensions_are_kept_core_ones_are_not(load_report) -> None:
    facts = list(iter_numeric_facts(load_report("endesa_2024.json")))
    dimensioned = [f for f in facts if f.has_dimensions]
    assert dimensioned
    for f in dimensioned:
        assert not {"concept", "entity", "period", "unit"} & set(f.dimensions)
