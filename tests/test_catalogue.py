import re

from sfetl.concepts import METRICS, METRICS_BY_CODE, SUBTOTAL_CHECKS
from sfetl.config import MIGRATIONS_DIR

SEED_ROW = re.compile(
    r"\(\s*'([a-z_]+)',\s*'[^']*',\s*'(balance_sheet|income_statement|cash_flow)'"
)


def test_catalogue_size_and_unique_codes() -> None:
    assert 25 <= len(METRICS) <= 40
    assert len(METRICS_BY_CODE) == len(METRICS)


def test_every_parent_is_a_metric_of_the_same_statement() -> None:
    for spec in METRICS:
        if spec.parent:
            assert spec.parent in METRICS_BY_CODE, spec.code
            assert METRICS_BY_CODE[spec.parent].statement == spec.statement


def test_subtotal_checks_use_known_metrics() -> None:
    for check in SUBTOTAL_CHECKS:
        assert check.total in METRICS_BY_CODE
        assert all(code in METRICS_BY_CODE for code, _ in check.components)


def test_every_metric_maps_to_ifrs_full_concepts_only() -> None:
    for spec in METRICS:
        assert spec.concepts and all(c.startswith("ifrs-full:") for c in spec.concepts)


def test_migration_seed_matches_the_python_catalogue() -> None:
    """Offline twin of the integration test that reads the metric table."""
    sql = (MIGRATIONS_DIR / "005_surrogate_keys_and_fiscal_period.sql").read_text("utf-8")
    seed = sql.split("INSERT INTO metric", 1)[1].split("UPDATE metric", 1)[0]
    seeded = [(code, statement) for code, statement in SEED_ROW.findall(seed)]
    assert seeded == [(s.code, s.statement) for s in METRICS]


def test_golden_figures_are_well_formed() -> None:
    from sfetl.validate_db import load_golden

    golden = load_golden()
    assert 5 <= len(golden) <= 10
    for g in golden:
        assert g.metric in METRICS_BY_CODE
        assert g.source.startswith("https://filings.xbrl.org/") and g.source.endswith(".xhtml")
        printed = int(g.printed.replace(".", "").replace(",", ""))
        assert printed * g.scale == g.expected_eur  # expected = printed x scale, nothing else
