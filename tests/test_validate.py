import copy
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sfetl.oim import Period
from sfetl.transform import FilingResult, MetricValue
from sfetl.validate import has_blocking_duplicates, render_markdown, validate


def statuses(result: FilingResult) -> dict[str, str]:
    report = validate([result])
    return {
        o.rule.name: next(k for k in ("flag", "pass", "n/a") if o.counts[k])
        for o in report.outcomes
    }


def with_metric(result: FilingResult, name: str, value: str) -> FilingResult:
    clone = copy.deepcopy(result)
    old = clone.metrics[name]
    clone.metrics[name] = replace(old, value=Decimal(value))
    return clone


def test_clean_filing_passes_every_rule(transformed) -> None:
    assert set(statuses(transformed("endesa_2024.json")).values()) == {"pass"}


def test_balance_identity_flags_liabilities_tagged_with_the_grand_total(transformed) -> None:
    # Realia FY2024 tags ifrs-full:Liabilities with the value of total equity and liabilities
    result = transformed("realia_2024.json")
    assert result.metrics["total_liabilities"].value == result.metrics["total_assets"].value
    report = validate([result])
    [issue] = [i for i in report.issues if i.rule == "balance_identity"]
    assert issue.severity == "error"


def test_balance_identity_flags_held_for_sale_outside_current_liabilities(transformed) -> None:
    assert statuses(transformed("amper_2024.json"))["balance_identity"] == "flag"


def test_balance_identity_tolerates_rounding(transformed) -> None:
    result = transformed("endesa_2024.json")
    nudged = with_metric(
        result, "total_assets", str(result.metrics["total_assets"].value + 1_000_000)
    )
    assert statuses(nudged)["balance_identity"] == "pass"  # 1 M EUR on 37,345 M EUR


def test_bank_does_not_need_revenue(transformed) -> None:
    assert statuses(transformed("bankinter_2024.json"))["missing_core_metric"] == "pass"


def test_missing_core_metrics_are_listed(transformed) -> None:
    report = validate([transformed("berkeley_2024.json")])
    missing = {i.metric for i in report.issues if i.rule == "missing_core_metric"}
    assert missing == {"revenue", "net_profit", "total_assets", "total_equity", "total_liabilities"}
    assert any(i.rule == "non_eur_unit" for i in report.issues)


def test_sign_check(transformed) -> None:
    result = with_metric(transformed("endesa_2024.json"), "cash", "-5")
    report = validate([result])
    assert [i.metric for i in report.issues if i.rule == "sign_check"] == ["cash"]


def test_negative_equity_is_not_a_sign_error(transformed) -> None:
    result = with_metric(transformed("endesa_2024.json"), "total_equity", "-1")
    assert statuses(result)["sign_check"] == "pass"


def test_component_bounds(transformed) -> None:
    result = transformed("endesa_2024.json")
    too_big = with_metric(result, "current_assets", str(result.metrics["total_assets"].value * 2))
    assert statuses(too_big)["component_bounds"] == "flag"


def test_period_consistency(transformed) -> None:
    result = copy.deepcopy(transformed("endesa_2024.json"))
    result.document_period_end = date(2023, 12, 31)
    assert statuses(result)["period_consistency"] == "flag"


def test_one_value_per_company_year_metric(transformed) -> None:
    a = transformed("endesa_2024.json")
    b = copy.deepcopy(a)
    b.meta = replace(b.meta, filing_id=b.meta.filing_id + 1, fxo_id=b.meta.fxo_id + "-copy")
    report = validate([a, b])
    assert has_blocking_duplicates(report)
    assert validate([a]).outcomes[-1].counts["pass"] == 1


def test_validation_never_changes_values(transformed) -> None:
    result = transformed("realia_2024.json")
    before = copy.deepcopy(result.metrics)
    validate([result])
    assert result.metrics == before


def test_report_lists_rules_and_flags(transformed) -> None:
    results = [transformed("realia_2024.json"), transformed("endesa_2024.json")]
    text = render_markdown(validate(results), results)
    assert "| `balance_identity` | error | 1 | 1 | 0 |" in text
    assert "REALIA BUSINESS, S.A. FY2024" in text


def test_metric_value_period_is_kept(transformed) -> None:
    value: MetricValue = transformed("endesa_2024.json").metrics["total_assets"]
    assert value.period == Period(None, date(2024, 12, 31))
