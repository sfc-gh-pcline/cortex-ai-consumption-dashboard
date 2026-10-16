"""Structural tests for the unified CoCo source migration."""

import datetime as dt

import pytest

from tests.fixtures import coco_contract
from utils import queries


def test_coherent_coco_source_contract():
    assert queries.COCO_USAGE_VIEW == coco_contract.EXPECTED_SOURCE
    assert queries.COCO_SUB_SURFACES == coco_contract.EXPECTED_INTERFACES


def test_facts_sql_uses_one_unified_source_and_model_pairing():
    sql, params = queries.coco_facts_sql(dt.date(2026, 8, 1), dt.date(2026, 8, 15))

    assert sql.count(coco_contract.EXPECTED_SOURCE) == 1
    assert "LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR)" in sql
    assert "LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR)" in sql
    assert "token_model.key = credit_model.key" in sql
    assert len(params) == coco_contract.EXPECTED_FACTS_BIND_COUNT
    for legacy_source in coco_contract.LEGACY_SOURCES:
        assert legacy_source not in sql


def test_rates_sql_uses_one_unified_source_and_model_pairing(monkeypatch):
    monkeypatch.setattr(queries, "utc_today", lambda: dt.date(2026, 8, 16))
    sql, params = queries.model_rates_sql()

    assert sql.count(coco_contract.EXPECTED_SOURCE) == 1
    assert "token_model.key = credit_model.key" in sql
    assert len(params) == coco_contract.EXPECTED_RATES_BIND_COUNT
    for legacy_source in coco_contract.LEGACY_SOURCES:
        assert legacy_source not in sql


def test_coco_scheduled_body_is_unified_and_parameterless():
    sql = queries.DT_BODY_REGISTRY[queries.SURFACE_COCO].body_fn()

    assert sql.count(coco_contract.EXPECTED_SOURCE) == 1
    assert "token_model.key = credit_model.key" in sql
    assert "?" not in sql


def test_model_rates_scheduled_body_is_unified_and_parameterless():
    sql = queries.model_rates_body_sql()

    assert sql.count(coco_contract.EXPECTED_SOURCE) == 1
    assert "token_model.key = credit_model.key" in sql
    assert "?" not in sql


@pytest.mark.parametrize(
    ("token_models", "credit_models", "expected_pairs"),
    [
        ({"model-a": {"input": 10}}, {"model-a": {"input": 0.01}}, {"model-a"}),
        (
            {"model-a": {"input": 10}, "model-b": {"output": 20}},
            {"model-a": {"input": 0.01}, "model-b": {"output": 0.02}},
            {"model-a", "model-b"},
        ),
        ({"model-a": {"input": 10}}, {}, set()),
    ],
)
def test_model_key_pairing_only_retains_keys_present_in_both_granular_objects(
    token_models, credit_models, expected_pairs
):
    """Mirror the SQL inner-join contract for single, multi, and mismatched models."""
    assert token_models.keys() & credit_models.keys() == expected_pairs


def test_mismatched_key_fixture_has_a_nonzero_reconciliation_count():
    token_models = {"model-a": {"input": 10}, "model-b": {"output": 20}}
    credit_models = {"model-a": {"input": 0.01}}

    unmatched_token_keys = token_models.keys() - credit_models.keys()

    assert unmatched_token_keys == {"model-b"}
    assert len(unmatched_token_keys) == 1
