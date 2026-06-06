"""Contract test against the upstream orders API schema.

Loads recorded sample payloads (good + bad) and asserts the schema
behaves correctly. In a real pipeline you'd recapture samples on a
schedule (e.g., once a day) so schema drift surfaces in CI rather than
in production.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA = Path(__file__).parent / "schemas" / "orders.schema.json"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA.read_text()))


def test_known_good_payload(validator: Draft202012Validator) -> None:
    payload = {
        "orders": [
            {"order_id": 1, "customer_id": "c1", "total": 10.0, "status": "paid", "currency": "USD"},
        ]
    }
    errors = list(validator.iter_errors(payload))
    assert errors == []


def test_rejects_negative_total(validator: Draft202012Validator) -> None:
    payload = {"orders": [{"order_id": 1, "customer_id": "c1", "total": -1.0, "status": "paid"}]}
    assert any("less than" in e.message or "minimum" in e.message for e in validator.iter_errors(payload))


def test_rejects_unknown_status(validator: Draft202012Validator) -> None:
    payload = {"orders": [{"order_id": 1, "customer_id": "c1", "total": 5.0, "status": "frobbed"}]}
    assert any("not one of" in e.message or "enum" in e.message for e in validator.iter_errors(payload))
