from dataclasses import replace
from datetime import date, datetime

from sfetl.extract import (
    FilingMeta,
    fiscal_year_for,
    latest_fiscal_years,
    parse_index_page,
    select_filings,
)


def meta(
    fid: int,
    lei: str = "LEI00000000000000001",
    period_end: date = date(2024, 12, 31),
    added: str = "2025-05-01T10:00:00",
    seq: int = 0,
    json_url: str | None = "/x.json",
) -> FilingMeta:
    return FilingMeta(
        filing_id=fid,
        fxo_id=f"{lei}-{period_end}-ESEF-ES-{seq}",
        lei=lei,
        entity_name="TEST SA",
        period_end=period_end,
        date_added=datetime.fromisoformat(added),
        json_url=json_url,
        package_url=None,
        viewer_url=None,
        error_count=0,
        warning_count=0,
        inconsistency_count=0,
    )


def test_fiscal_year_label() -> None:
    assert fiscal_year_for(date(2024, 12, 31)) == 2024
    assert fiscal_year_for(date(2025, 1, 31)) == 2024  # Inditex
    assert fiscal_year_for(date(2024, 3, 31)) == 2023
    assert fiscal_year_for(date(2024, 6, 30)) == 2024
    assert fiscal_year_for(date(2024, 9, 30)) == 2024


def test_parse_index_page_joins_the_entity() -> None:
    page = {
        "data": [
            {
                "type": "filing",
                "id": "18863",
                "attributes": {
                    "fxo_id": "95980020140005491250-2024-12-31-ESEF-ES-0",
                    "period_end": "2024-12-31",
                    "date_added": "2025-05-08 11:24:58.730825",
                    "json_url": "/a.json",
                    "package_url": "/a.zip",
                    "viewer_url": "/v.html",
                    "error_count": 0,
                    "warning_count": 1,
                    "inconsistency_count": 0,
                },
                "relationships": {"entity": {"data": {"type": "entity", "id": "915"}}},
            }
        ],
        "included": [
            {
                "type": "entity",
                "id": "915",
                "attributes": {"name": "ELECNOR SA", "identifier": "95980020140005491250"},
            }
        ],
    }
    [row] = parse_index_page(page)
    assert row.lei == "95980020140005491250"
    assert row.entity_name == "ELECNOR SA"
    assert row.fiscal_year == 2024
    assert row.warning_count == 1


def test_latest_filing_per_company_and_year_wins() -> None:
    old = meta(1, added="2025-04-01T00:00:00")
    new = meta(2, added="2025-05-01T00:00:00")
    assert select_filings([old, new], [2024]) == [new]


def test_sequence_breaks_ties_then_filing_id() -> None:
    first = meta(1, seq=0)
    resubmitted = meta(2, seq=1)
    assert select_filings([resubmitted, first], [2024]) == [resubmitted]
    a, b = meta(3), meta(4)
    assert select_filings([b, a], [2024]) == [b]


def test_filings_without_json_and_other_years_are_dropped() -> None:
    rows = [meta(1, json_url=None), meta(2, period_end=date(2022, 12, 31))]
    assert select_filings(rows, [2023, 2024]) == []


def test_max_companies_is_deterministic() -> None:
    rows = []
    for i, lei in enumerate(["C" * 20, "A" * 20, "B" * 20]):
        rows.append(meta(10 + i, lei=lei))
        if lei != "C" * 20:
            rows.append(meta(20 + i, lei=lei, period_end=date(2023, 12, 31)))
    kept = select_filings(rows, [2023, 2024], max_companies=2)
    assert {r.lei for r in kept} == {"A" * 20, "B" * 20}  # most years first, then LEI


def test_latest_fiscal_years_needs_enough_filings() -> None:
    rows = [meta(i, lei=f"L{i:019d}") for i in range(3)]
    rows += [replace(meta(100), period_end=date(2025, 12, 31))]  # a single early filer
    rows += [meta(200 + i, lei=f"L{i:019d}", period_end=date(2023, 12, 31)) for i in range(3)]
    assert latest_fiscal_years(rows, n_years=2, min_filings=2) == [2023, 2024]
