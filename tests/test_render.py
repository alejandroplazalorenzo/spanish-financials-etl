from decimal import Decimal

from sfetl.ask.render import CSV_MIN_ROWS, Answer, format_value, paginate, render_rows


def test_money_percent_and_per_share_formatting() -> None:
    assert format_value("Value", Decimal("20935000000"), "EUR") == "20,935.0 M EUR"
    assert format_value("Value", Decimal("512"), "EUR") == "512 EUR"
    assert format_value("Net margin (%)", Decimal("13.29")) == "13.29 %"
    assert format_value("Value", Decimal("1.7532"), "EUR/share") == "1.7532 EUR/share"
    assert format_value("Current ratio (x)", Decimal("1.2")) == "1.20x"
    assert format_value("Fiscal year", 2024) == "2024"
    assert format_value("Bank or insurer", True) == "yes"
    assert format_value("Value", None, "EUR") == ""


def test_one_row_is_a_record_and_several_a_list() -> None:
    columns = ["Company", "Value", "Unit"]
    one = render_rows(columns, [("ENDESA SA", Decimal("1000000"), "EUR")])
    assert one == "Company: ENDESA SA\nValue: 1.0 M EUR"
    many = render_rows(columns, [("A", Decimal(1), "EUR"), ("B", Decimal(2), "EUR")])
    assert many.startswith("1. A\n   Value: 1 EUR\n2. B")


def test_pages_split_at_line_boundaries() -> None:
    text = "\n".join(f"line {i:04d}" for i in range(1000))
    pages = paginate(text, limit=500)
    assert len(pages) > 1
    assert all(len(p) <= 500 for p in pages)
    assert "\n".join(pages) == text


def test_csv_only_for_long_results() -> None:
    short = Answer(text="t", mode="intent", columns=["a"], rows=[(1,)])
    long = Answer(text="t", mode="intent", columns=["a"], rows=[(i,) for i in range(CSV_MIN_ROWS)])
    assert short.csv is None
    assert long.csv is not None and long.csv.splitlines()[0] == "a"
