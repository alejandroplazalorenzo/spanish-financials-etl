"""CI smoke test: every intent and every assistant write, run AS sfetl_assistant.

The lesson it encodes: in production a missing column GRANT broke a feature and was found by a
user. Here a missing grant, a renamed view column or a typo in an intent fails CI instead.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from sfetl.ask.intents import INTENTS
from sfetl.ask.smoke import run_smoke

pytestmark = pytest.mark.integration

# Intents that may legitimately return no rows on the small fixture set (the smoke test checks
# that the SQL runs with the role; production's smoke test also only listed empty results).
MAY_BE_EMPTY = {
    "company_flags",
    "companies_owned_by",
    "loss_making_companies",
    "ownership_contradictions",
    "parents_outside_dataset",
    "derived_values",
}


def test_every_intent_and_every_write_works_as_the_assistant(
    as_role: Callable[[str], Callable[[], psycopg.Connection]],
) -> None:
    results, sample = run_smoke(as_role("assistant"))
    failed = [r for r in results if not r.ok]
    assert not failed, failed
    by_name = {r.name: r for r in results}
    assert len([n for n in by_name if n.startswith("intent ")]) == len(INTENTS)
    empty = {n.removeprefix("intent ") for n, r in by_name.items() if r.rows == 0}
    assert empty <= MAY_BE_EMPTY, empty - MAY_BE_EMPTY
    assert sample["company"]  # parameters were taken from the loaded data


def test_the_smoke_test_leaves_no_trace(
    as_role: Callable[[str], Callable[[], psycopg.Connection]],
) -> None:
    run_smoke(as_role("assistant"))
    with as_role("owner")() as conn:
        counts = [
            conn.execute(f"SELECT count(*) FROM assistant.{t}").fetchone()[0]  # type: ignore[index]
            for t in ("query_log", "pending", "page")
        ]
    assert counts == [0, 0, 0]
