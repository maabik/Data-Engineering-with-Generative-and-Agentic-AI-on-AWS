"""Tests for PolicyParser — uses a stubbed Bedrock client so no AWS creds needed."""

import json

import pytest

from guardrails_from_policy.policy_parser import (
    PolicyParser,
    ParsedPolicy,
    _coerce,
    _extract_json,
    VALID_PII_TYPES,
)
from tests.fixtures import SAMPLE_PARSED_JSON, SAMPLE_POLICY_TEXT


def _stub_client(response_json: dict):
    """Return a mock boto3 bedrock-runtime client that returns a fixed response."""
    class FakeClient:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [{"text": json.dumps(response_json)}]
                    }
                }
            }
    return FakeClient()


class TestExtractJson:
    def test_bare_json(self):
        raw = '{"a": 1}'
        assert json.loads(_extract_json(raw)) == {"a": 1}

    def test_fenced_json(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert json.loads(_extract_json(raw)) == {"a": 1}

    def test_json_with_prefix_text(self):
        raw = "Here is the result:\n{\"a\": 1}"
        assert json.loads(_extract_json(raw)) == {"a": 1}

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON object"):
            _extract_json("no json here at all")

    def test_nested_braces(self):
        raw = '{"outer": {"inner": 42}}'
        assert json.loads(_extract_json(raw)) == {"outer": {"inner": 42}}


class TestCoerce:
    def test_full_sample(self):
        policy = _coerce(SAMPLE_PARSED_JSON)
        assert isinstance(policy, ParsedPolicy)
        assert policy.guardrail_name == "acme-data-classification-policy"
        assert len(policy.classification_tiers) == 4
        assert len(policy.denied_topics) == 4
        assert len(policy.pii_entities) == 10
        assert policy.grounding_threshold == 0.75
        assert policy.relevance_threshold == 0.70

    def test_name_slugified(self):
        data = {**SAMPLE_PARSED_JSON, "guardrail_name": "ACME Data Policy 2026!"}
        policy = _coerce(data)
        assert " " not in policy.guardrail_name
        assert "!" not in policy.guardrail_name

    def test_name_max_50_chars(self):
        data = {**SAMPLE_PARSED_JSON, "guardrail_name": "a" * 100}
        policy = _coerce(data)
        assert len(policy.guardrail_name) <= 50

    def test_invalid_pii_types_dropped(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "pii_entities": [
                {"type": "EMAIL", "action": "ANONYMIZE"},
                {"type": "FAKE_PII_TYPE", "action": "BLOCK"},
            ],
        }
        policy = _coerce(data)
        types = {p.type for p in policy.pii_entities}
        assert "FAKE_PII_TYPE" not in types
        assert "EMAIL" in types

    def test_invalid_pii_action_dropped(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "pii_entities": [
                {"type": "EMAIL", "action": "REDACT"},  # invalid action
                {"type": "PHONE", "action": "ANONYMIZE"},  # valid
            ],
        }
        policy = _coerce(data)
        types = {p.type for p in policy.pii_entities}
        assert "PHONE" in types
        assert "EMAIL" not in types

    def test_empty_topic_definition_dropped(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "denied_topics": [
                {"name": "Valid", "definition": "A real definition", "examples": []},
                {"name": "BadTopic", "definition": ""},  # empty definition → dropped
            ],
        }
        policy = _coerce(data)
        names = [t.name for t in policy.denied_topics]
        assert "BadTopic" not in names
        assert "Valid" in names

    def test_content_filter_invalid_strength_defaults(self):
        data = {**SAMPLE_PARSED_JSON, "content_filters": {"hate": "EXTREME", "sexual": "HIGH"}}
        policy = _coerce(data)
        # EXTREME is invalid → default MEDIUM
        assert policy.content_filters.hate == "MEDIUM"
        assert policy.content_filters.sexual == "HIGH"

    def test_grounding_clamped_to_01(self):
        data = {**SAMPLE_PARSED_JSON, "grounding_threshold": 1.5, "relevance_threshold": -0.3}
        policy = _coerce(data)
        assert policy.grounding_threshold == 1.0
        assert policy.relevance_threshold == 0.0

    def test_grounding_null(self):
        data = {**SAMPLE_PARSED_JSON, "grounding_threshold": None, "relevance_threshold": None}
        policy = _coerce(data)
        assert policy.grounding_threshold is None
        assert policy.relevance_threshold is None

    def test_missing_optional_fields_use_defaults(self):
        minimal = {
            "guardrail_name": "min-policy",
            "description": "Minimal.",
            "classification_tiers": [],
            "denied_topics": [],
            "blocked_input_phrases": [],
            "blocked_output_phrases": [],
            "pii_entities": [],
            "content_filters": {},
        }
        policy = _coerce(minimal)
        assert policy.guardrail_name == "min-policy"
        assert policy.pii_entities == []
        assert policy.grounding_threshold is None

    def test_all_valid_pii_types_accepted(self):
        entities = [{"type": t, "action": "ANONYMIZE"} for t in VALID_PII_TYPES]
        data = {**SAMPLE_PARSED_JSON, "pii_entities": entities}
        policy = _coerce(data)
        assert len(policy.pii_entities) == len(VALID_PII_TYPES)


class TestPolicyParserWithStub:
    def _parser_with(self, response_json: dict) -> PolicyParser:
        p = PolicyParser.__new__(PolicyParser)
        p.model_id = "stub"
        p.region = "us-east-1"
        p.max_policy_chars = 60_000
        p._client = _stub_client(response_json)
        return p

    def test_parse_returns_parsed_policy(self):
        parser = self._parser_with(SAMPLE_PARSED_JSON)
        policy = parser.parse(SAMPLE_POLICY_TEXT)
        assert isinstance(policy, ParsedPolicy)

    def test_parse_name(self):
        parser = self._parser_with(SAMPLE_PARSED_JSON)
        policy = parser.parse(SAMPLE_POLICY_TEXT)
        assert policy.guardrail_name == "acme-data-classification-policy"

    def test_parse_topics(self):
        parser = self._parser_with(SAMPLE_PARSED_JSON)
        policy = parser.parse(SAMPLE_POLICY_TEXT)
        assert len(policy.denied_topics) == 4

    def test_parse_pii(self):
        parser = self._parser_with(SAMPLE_PARSED_JSON)
        policy = parser.parse(SAMPLE_POLICY_TEXT)
        assert any(p.type == "US_SOCIAL_SECURITY_NUMBER" for p in policy.pii_entities)

    def test_invalid_json_response_raises(self):
        class BadClient:
            def converse(self, **kwargs):
                return {
                    "output": {"message": {"content": [{"text": "not json at all"}]}}
                }
        p = PolicyParser.__new__(PolicyParser)
        p.model_id = "stub"
        p.region = "us-east-1"
        p.max_policy_chars = 60_000
        p._client = BadClient()
        with pytest.raises(ValueError, match="No JSON object"):
            p.parse("some policy text")

    def test_policy_truncated_at_max_chars(self):
        seen = []
        class CapturingClient:
            def converse(self, **kwargs):
                seen.append(kwargs["messages"][0]["content"][0]["text"])
                return {
                    "output": {"message": {"content": [{"text": json.dumps(SAMPLE_PARSED_JSON)}]}}
                }
        p = PolicyParser.__new__(PolicyParser)
        p.model_id = "stub"
        p.region = "us-east-1"
        p.max_policy_chars = 50
        p._client = CapturingClient()
        p.parse("A" * 200)
        assert "truncated" in seen[0]
