from decimal import Decimal

from sfetl.ask.evaluate import compare, normalize
from sfetl.ask.service import QueryOutcome


def qr(columns: list[str], rows: list[tuple]) -> QueryOutcome:
    return QueryOutcome(columns=columns, rows=rows, seconds=0.0)


def test_numbers_are_compared_after_rounding() -> None:
    assert normalize(Decimal("0.07871234")) == normalize(0.0787)
    assert normalize(125) == normalize(Decimal("125.00"))


def test_same_rows_any_order_when_unordered() -> None:
    ref = qr(["name"], [("A",), ("B",)])
    gen = qr(["company_name"], [("B",), ("A",)])
    assert compare(ref, gen, ordered=False) == (True, True)
    assert compare(ref, gen, ordered=True) == (False, False)


def test_extra_columns_are_accepted_but_not_strict() -> None:
    ref = qr(["name"], [("IBERDROLA SA",)])
    gen = qr(["company_name", "revenue"], [("IBERDROLA SA", Decimal("44739000000.00"))])
    assert compare(ref, gen, ordered=False) == (False, True)


def test_wrong_answer_or_missing_column_fails() -> None:
    ref = qr(["name", "revenue"], [("A", 1)])
    assert compare(ref, qr(["name"], [("A",)]), ordered=False) == (False, False)
    assert compare(ref, qr(["name", "revenue"], [("B", 1)]), ordered=False) == (False, False)
    assert compare(ref, qr(["name", "revenue"], []), ordered=False) == (False, False)
