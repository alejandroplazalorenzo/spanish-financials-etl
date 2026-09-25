from sfetl.ask.bench import RoutingCase, load_cases, params_match, run_bench, run_case
from sfetl.ask.evaluate import load_questions
from sfetl.ask.guardrails import guard_sql
from sfetl.ask.llm import ScriptedProvider
from sfetl.config import PROJECT_ROOT

EVAL = PROJECT_ROOT / "eval"


def test_routing_cases_are_well_formed() -> None:
    cases = load_cases(EVAL / "routing_cases.yaml")  # raises on an unknown intent id
    kinds = {c.expect for c in cases}
    assert kinds == {"intent", "free_sql", "decline"}
    assert len({c.id for c in cases}) == len(cases)
    assert {c.lang for c in cases} == {"en", "es"}


def test_fallback_reference_sql_passes_the_guardrails() -> None:
    for q in load_questions(EVAL / "fallback_questions.yaml"):
        guard_sql(q.reference_sql)


def route(intent_id: str | None, params: dict[str, str] | None = None) -> dict:
    return {
        "intent_id": intent_id,
        "params": [{"name": k, "value": v} for k, v in (params or {}).items()],
        "alternative": None,
    }


def test_intent_case_passes_on_an_acceptable_alternative() -> None:
    case = RoutingCase(
        "t",
        "q",
        "en",
        "intent",
        "company_metric",
        ("company_key_figures",),
        {"company": "Iberdrola"},
    )
    provider = ScriptedProvider([route("company_key_figures", {"company": "iberdrola"})])
    record = run_case(provider, case)
    assert record["passed"] and record["params_ok"]


def test_free_sql_case_needs_null_routing_and_safe_sql() -> None:
    case = RoutingCase("t", "q", "en", "free_sql")
    good = ScriptedProvider([route(None), {"sql": "SELECT count(*) FROM v_filing", "title": "t"}])
    bad = ScriptedProvider([route(None), {"sql": "SELECT * FROM financial_fact", "title": "t"}])
    assert run_case(good, case)["passed"]
    assert not run_case(bad, case)["passed"]


def test_decline_case_fails_when_a_query_is_produced() -> None:
    case = RoutingCase("t", "q", "en", "decline")
    declined = ScriptedProvider([route(None), {"sql": "", "title": "cannot"}])
    refused = ScriptedProvider([route(None), {"sql": "DELETE FROM company", "title": "x"}])
    answered = ScriptedProvider([route(None), {"sql": "SELECT 1 FROM v_company", "title": "x"}])
    assert run_case(declined, case)["passed"]
    assert run_case(refused, case)["passed"]  # the guardrails refuse it: nothing runs
    assert not run_case(answered, case)["passed"]
    assert not run_case(ScriptedProvider([route("list_metrics")]), case)["passed"]


def test_params_match_is_lenient_on_case_and_accents_only() -> None:
    assert params_match("company_metric", {"company": "Telefónica"}, {"company": "telefonica"})
    assert params_match("ranking_by_metric", {"metric": "revenue"}, {"metric": "ventas"})
    assert not params_match("company_metric", {"company": "Repsol"}, {"company": "Iberdrola"})


def test_summary_counts_by_expected_outcome() -> None:
    cases = [
        RoutingCase("a", "q", "en", "intent", "list_metrics"),
        RoutingCase("b", "q", "es", "decline"),
    ]
    provider = ScriptedProvider([route("list_metrics"), route(None), {"sql": "", "title": "no"}])
    summary = run_bench(provider, cases)
    assert summary["passed"] == 2
    assert summary["by_expect"] == {
        "intent": {"n": 1, "passed": 1},
        "decline": {"n": 1, "passed": 1},
    }
