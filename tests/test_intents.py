import re

import pytest
import sqlglot
from sqlglot import exp

from sfetl.ask.guardrails import ALLOWED_RELATIONS
from sfetl.ask.intents import (
    INTENTS,
    INTENTS_BY_ID,
    InvalidParam,
    Param,
    bind,
    coerce,
    render_title,
)

PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def test_catalogue_size_and_unique_ids() -> None:
    assert 15 <= len(INTENTS) <= 25
    assert len(INTENTS_BY_ID) == len(INTENTS)


@pytest.mark.parametrize("intent", INTENTS, ids=lambda i: i.id)
def test_sql_uses_exactly_the_declared_parameters(intent) -> None:
    used = set(PLACEHOLDER.findall(intent.sql))
    assert used == {p.name for p in intent.params}


@pytest.mark.parametrize("intent", INTENTS, ids=lambda i: i.id)
def test_intents_read_curated_views_only(intent) -> None:
    sql = PLACEHOLDER.sub("NULL", intent.sql).replace("%%", "%")
    tree = sqlglot.parse_one(sql, read="postgres")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table) if t.name} - ctes
    assert tables and tables <= ALLOWED_RELATIONS, tables - ALLOWED_RELATIONS


@pytest.mark.parametrize("intent", INTENTS, ids=lambda i: i.id)
def test_every_intent_has_a_title_that_renders_without_optional_values(intent) -> None:
    values = {p.name: ("X" if p.required else None) for p in intent.params}
    title = render_title(intent, values)
    assert title and "{" not in title and "[" not in title


def test_title_drops_empty_optional_segments() -> None:
    intent = INTENTS_BY_ID["company_metric"]
    assert render_title(intent, {"company": "Endesa", "metric": "revenue", "year": None}) == (
        "Revenue of Endesa"
    )
    assert render_title(intent, {"company": "Endesa", "metric": "cash", "year": "2024"}) == (
        "Cash and cash equivalents of Endesa in FY2024"
    )


def test_undeclared_parameters_are_dropped_and_missing_required_reported() -> None:
    intent = INTENTS_BY_ID["company_metric"]
    bound, missing = bind(intent, {"metric": "ventas", "limit": "1; DROP TABLE x"})
    assert "limit" not in bound
    assert bound["metric"] == "revenue"
    assert [p.name for p in missing] == ["company"]


def test_invalid_optional_values_fall_back_to_the_default() -> None:
    intent = INTENTS_BY_ID["ranking_by_metric"]
    bound, missing = bind(intent, {"metric": "happiness", "year": "last year", "n": "500"})
    assert bound["metric"] == "revenue" and bound["year"] is None and not missing
    assert bound["n"] == "50"  # clamped


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("year", "FY2024", "2024"),
        ("year", "en 2023", "2023"),
        ("metric", "Beneficio neto", "net_profit"),
        ("metric", "total_assets", "total_assets"),
        ("metric", "Total assets", "total_assets"),
        ("metric", "facturación", "revenue"),
        ("int", "top 5", "5"),
    ],
)
def test_coerce(kind: str, raw: str, expected: str) -> None:
    assert coerce(Param("p", "", kind), raw) == expected  # type: ignore[arg-type]


def test_enum_synonyms_and_rejection() -> None:
    direction = INTENTS_BY_ID["ranking_by_metric"].param("direction")
    assert direction is not None
    assert coerce(direction, "lowest") == "bottom"
    assert coerce(direction, "mayores") == "top"
    with pytest.raises(InvalidParam):
        coerce(direction, "sideways")


def test_every_answer_about_figures_carries_a_caveat() -> None:
    for intent in INTENTS:
        if intent.area in ("financial", "ownership"):
            assert intent.caveat, intent.id
