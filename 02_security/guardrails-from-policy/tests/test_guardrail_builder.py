"""Tests for guardrail_builder — converts ParsedPolicy → GuardrailConfig."""

import pytest

from guardrails_from_policy.guardrail_builder import build_guardrail_config
from guardrails_from_policy.policy_parser import _coerce
from tests.fixtures import SAMPLE_PARSED_JSON


def _config():
    return build_guardrail_config(_coerce(SAMPLE_PARSED_JSON))


class TestGuardrailBuilder:
    def test_returns_guardrail_config(self):
        config = _config()
        from guardrails_from_policy.guardrail_builder import GuardrailConfig
        assert isinstance(config, GuardrailConfig)

    def test_name_set(self):
        assert _config().name == "acme-data-classification-policy"

    def test_description_set(self):
        assert len(_config().description) > 0

    def test_topic_policy_present(self):
        config = _config()
        assert config.topic_policy_config is not None
        topics = config.topic_policy_config["topicsConfig"]
        assert len(topics) == 4

    def test_topic_type_is_deny(self):
        config = _config()
        for t in config.topic_policy_config["topicsConfig"]:
            assert t["type"] == "DENY"

    def test_topic_name_truncated_to_100(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "denied_topics": [
                {"name": "X" * 200, "definition": "some definition", "examples": []}
            ],
        }
        config = build_guardrail_config(_coerce(data))
        for t in config.topic_policy_config["topicsConfig"]:
            assert len(t["name"]) <= 100

    def test_content_policy_present(self):
        config = _config()
        assert config.content_policy_config is not None
        filters = config.content_policy_config["filtersConfig"]
        assert len(filters) > 0

    def test_prompt_attack_output_is_none(self):
        config = _config()
        pa = next(
            f for f in config.content_policy_config["filtersConfig"]
            if f["type"] == "PROMPT_ATTACK"
        )
        assert pa["outputStrength"] == "NONE"
        assert pa["inputStrength"] == "HIGH"

    def test_word_policy_contains_phrases(self):
        config = _config()
        assert config.word_policy_config is not None
        texts = {w["text"] for w in config.word_policy_config["wordsConfig"]}
        assert "Project Nighthawk" in texts
        assert "Operation Stardust" in texts
        assert "Codename Polaris" in texts

    def test_word_policy_deduplicates(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "blocked_input_phrases": ["foo", "bar"],
            "blocked_output_phrases": ["bar", "baz"],
        }
        config = build_guardrail_config(_coerce(data))
        texts = [w["text"] for w in config.word_policy_config["wordsConfig"]]
        assert len(texts) == len(set(texts))

    def test_pii_policy_present(self):
        config = _config()
        assert config.sensitive_information_policy_config is not None
        entities = config.sensitive_information_policy_config["piiEntitiesConfig"]
        types = {e["type"] for e in entities}
        assert "US_SOCIAL_SECURITY_NUMBER" in types

    def test_grounding_policy_present(self):
        config = _config()
        assert config.contextual_grounding_policy_config is not None
        filters = {
            f["type"]: f["threshold"]
            for f in config.contextual_grounding_policy_config["filtersConfig"]
        }
        assert filters["GROUNDING"] == 0.75
        assert filters["RELEVANCE"] == 0.70

    def test_grounding_policy_absent_when_null(self):
        data = {**SAMPLE_PARSED_JSON, "grounding_threshold": None, "relevance_threshold": None}
        config = build_guardrail_config(_coerce(data))
        assert config.contextual_grounding_policy_config is None

    def test_restricted_messaging(self):
        config = _config()
        assert "restricted" in config.blocked_input_messaging.lower()
        assert "restricted" in config.blocked_outputs_messaging.lower()

    def test_as_boto3_kwargs_structure(self):
        kwargs = _config().as_boto3_kwargs()
        assert "name" in kwargs
        assert "description" in kwargs
        assert "blockedInputMessaging" in kwargs
        assert "blockedOutputsMessaging" in kwargs
        # Optional fields only present when not None
        assert "topicPolicyConfig" in kwargs
        assert "contentPolicyConfig" in kwargs

    def test_as_boto3_kwargs_omits_none_fields(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "denied_topics": [],
            "pii_entities": [],
            "blocked_input_phrases": [],
            "blocked_output_phrases": [],
            "grounding_threshold": None,
            "relevance_threshold": None,
        }
        config = build_guardrail_config(_coerce(data))
        kwargs = config.as_boto3_kwargs()
        assert "topicPolicyConfig" not in kwargs
        assert "wordPolicyConfig" not in kwargs
        assert "sensitiveInformationPolicyConfig" not in kwargs
        assert "contextualGroundingPolicyConfig" not in kwargs

    def test_no_topics_produces_no_topic_policy(self):
        data = {**SAMPLE_PARSED_JSON, "denied_topics": []}
        config = build_guardrail_config(_coerce(data))
        assert config.topic_policy_config is None

    def test_all_none_content_filters_omits_policy(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "content_filters": {
                "hate": "NONE",
                "insults": "NONE",
                "sexual": "NONE",
                "violence": "NONE",
                "misconduct": "NONE",
                "prompt_attack": "NONE",
            },
        }
        config = build_guardrail_config(_coerce(data))
        assert config.content_policy_config is None

    def test_confidential_tier_messaging(self):
        data = {
            **SAMPLE_PARSED_JSON,
            "classification_tiers": [
                {"name": "Confidential", "sensitivity": "confidential", "description": "sensitive"}
            ],
        }
        config = build_guardrail_config(_coerce(data))
        assert "confidential" in config.blocked_input_messaging.lower()

    def test_as_dict_json_serialisable(self):
        import json
        config = _config()
        json.dumps(config.as_dict())  # must not raise
