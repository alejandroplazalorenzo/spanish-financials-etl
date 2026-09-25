import jsonschema
import pytest

from sfetl.ask.classify import (
    classification_schema,
    classify,
    parse_classification,
    write_free_sql,
)
from sfetl.ask.llm import LLMError, ScriptedProvider


def test_schema_accepts_null_and_unknown_ids_mean_no_intent() -> None:
    schema = classification_schema()
    jsonschema.validate({"intent_id": None, "params": [], "alternative": None}, schema)
    unknown = {"intent_id": "drop_tables", "params": [], "alternative": None}
    jsonschema.validate(unknown, schema)
    assert parse_classification(unknown).intent_id is None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"intent_id": 3, "params": [], "alternative": None}, schema)


def test_only_declared_params_survive() -> None:
    c = parse_classification(
        {
            "intent_id": "company_metric",
            "params": [
                {"name": "company", "value": " Iberdrola "},
                {"name": "sql", "value": "DELETE FROM company"},
                {"name": "year", "value": ""},
            ],
            "alternative": None,
        }
    )
    assert c.intent_id == "company_metric"
    assert c.params == {"company": "Iberdrola"}


def test_alternative_is_kept_only_when_it_is_a_different_intent() -> None:
    base = {"intent_id": "company_metric", "params": []}
    same = parse_classification(
        {**base, "alternative": {"intent_id": "company_metric", "params": []}}
    )
    other = parse_classification(
        {**base, "alternative": {"intent_id": "company_key_figures", "params": []}}
    )
    assert same.alternative is None
    assert other.alternative is not None and other.alternative.intent_id == "company_key_figures"


def test_classify_sends_the_catalogue_and_the_previous_question() -> None:
    provider = ScriptedProvider([{"intent_id": None, "params": [], "alternative": None}])
    c = classify(provider, "¿y Repsol?", previous="What was Iberdrola's revenue in 2024?")
    system, user = provider.calls[0]
    assert "company_metric" in system and "ranking_by_metric" in system
    assert "Iberdrola" in user and "Repsol" in user
    assert c.intent_id is None


def test_a_reply_that_breaks_the_schema_is_an_error() -> None:
    provider = ScriptedProvider([{"intent": "company_metric"}])
    with pytest.raises(LLMError):
        classify(provider, "anything")


def test_free_sql_empty_means_decline() -> None:
    assert write_free_sql(ScriptedProvider([{"sql": "", "title": "cannot"}]), "q") is None
    done = write_free_sql(ScriptedProvider([{"sql": "SELECT 1", "title": "t"}]), "q")
    assert done is not None and done.sql == "SELECT 1"
