"""Tests for lambda/transform/parser.py"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda", "transform"))

import json
import pytest
from parser import parse_record, _extract_text_from_body, _guardrail_summary


# ── Fixtures ──────────────────────────────────────────────────────────────────

ANTHROPIC_MESSAGES_LOG = {
    "schemaType": "ModelInvocationLog",
    "schemaVersion": "1.0",
    "timestamp": "2026-01-15T10:30:00Z",
    "accountId": "123456789012",
    "region": "us-east-1",
    "requestId": "req-abc123",
    "operation": "InvokeModel",
    "modelId": "anthropic.claude-3-sonnet-20240229-v1:0",
    "input": {
        "inputBodyJson": {
            "system": "You are a helpful assistant.",
            "messages": [{"role": "user", "content": "Hello, world!"}],
        },
        "inputTokenCount": 25,
        "inputContentType": "application/json",
    },
    "output": {
        "outputBodyJson": {
            "content": [{"type": "text", "text": "Hello! How can I help you?"}],
        },
        "outputTokenCount": 12,
        "outputContentType": "application/json",
    },
    "requestMetadata": {
        "user_id": "user-1",
        "tenant_id": "acme",
        "session_id": "sess-001",
        "trace_id": "trace-xyz",
    },
    "performance": {"latency": 980},
}

TITAN_LOG = {
    "schemaType": "ModelInvocationLog",
    "timestamp": "2026-01-15T11:00:00Z",
    "accountId": "123456789012",
    "region": "us-west-2",
    "requestId": "req-titan1",
    "modelId": "amazon.titan-text-express-v1",
    "input": {
        "inputBodyJson": {"inputText": "Summarize this document."},
        "inputTokenCount": 10,
    },
    "output": {
        "outputBodyJson": {"results": [{"outputText": "The document discusses..."}]},
        "outputTokenCount": 8,
    },
    "requestMetadata": {},
    "performance": {"latency": 450},
}

GUARDRAIL_BLOCKED_LOG = {
    **ANTHROPIC_MESSAGES_LOG,
    "requestId": "req-blocked",
    "guardrailsTrace": {
        "guardrailId": "grd-001",
        "action": "BLOCKED",
        "outputAssessments": {
            "grd-001": [
                {
                    "topicPolicy": {
                        "topics": [
                            {"name": "Violence", "action": "BLOCKED"},
                            {"name": "HateSpeech", "action": "BLOCKED"},
                        ]
                    },
                    "contentPolicy": {
                        "filters": [
                            {"type": "PROMPT_ATTACK", "action": "BLOCKED"},
                        ]
                    },
                }
            ]
        },
    },
}

ERROR_LOG = {
    "schemaType": "ModelInvocationLog",
    "timestamp": "2026-01-15T12:00:00Z",
    "accountId": "123456789012",
    "region": "us-east-1",
    "requestId": "req-err1",
    "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
    "errorCode": "ThrottlingException",
    "errorMessage": "Rate limit exceeded",
    "input": {"inputTokenCount": 0},
    "output": {"outputTokenCount": 0},
    "requestMetadata": {},
    "performance": {},
}


# ── Tests: _extract_text_from_body ────────────────────────────────────────────

class TestExtractTextFromBody:
    def test_anthropic_messages(self):
        body = {
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        text = _extract_text_from_body(body, "anthropic.claude-3-sonnet")
        assert "You are helpful." in text
        assert "Hello" in text

    def test_anthropic_content_list(self):
        body = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Test prompt"}]}
            ]
        }
        text = _extract_text_from_body(body, "anthropic.claude-3-sonnet")
        assert "Test prompt" in text

    def test_titan_input_text(self):
        text = _extract_text_from_body({"inputText": "Titan prompt"}, "amazon.titan")
        assert text == "Titan prompt"

    def test_titan_output_results(self):
        body = {"results": [{"outputText": "result1"}, {"outputText": "result2"}]}
        text = _extract_text_from_body(body, "amazon.titan")
        assert "result1" in text
        assert "result2" in text

    def test_empty_body(self):
        assert _extract_text_from_body(None, "any") == ""
        assert _extract_text_from_body({}, "any") == ""

    def test_prompt_field(self):
        text = _extract_text_from_body({"prompt": "Hello Llama"}, "meta.llama")
        assert text == "Hello Llama"

    def test_unknown_body_falls_back_to_json(self):
        body = {"some_custom_field": "value"}
        text = _extract_text_from_body(body, "unknown.model")
        assert "some_custom_field" in text


# ── Tests: _guardrail_summary ─────────────────────────────────────────────────

class TestGuardrailSummary:
    def test_none_trace(self):
        action, topics, filters, injection = _guardrail_summary(None)
        assert action == "NONE"
        assert topics == []
        assert injection is False

    def test_blocked_with_topics(self):
        trace = GUARDRAIL_BLOCKED_LOG["guardrailsTrace"]
        action, topics, filters, injection = _guardrail_summary(trace)
        assert action == "BLOCKED"
        assert "Violence" in topics
        assert "HateSpeech" in topics
        assert injection is True  # PROMPT_ATTACK filter

    def test_empty_trace(self):
        action, topics, filters, injection = _guardrail_summary({})
        assert action == "NONE"


# ── Tests: parse_record ───────────────────────────────────────────────────────

class TestParseRecord:
    def test_anthropic_messages(self):
        r = parse_record(ANTHROPIC_MESSAGES_LOG)
        assert r["request_id"] == "req-abc123"
        assert r["model_id"] == "anthropic.claude-3-sonnet-20240229-v1:0"
        assert r["model_family"] == "anthropic.claude-3-sonnet-20240229-v1"
        assert r["input_tokens"] == 25
        assert r["output_tokens"] == 12
        assert r["latency_ms"] == 980
        assert r["user_id"] == "user-1"
        assert r["tenant_id"] == "acme"
        assert r["session_id"] == "sess-001"
        assert r["trace_id"] == "trace-xyz"
        assert r["date"] == "2026-01-15"
        assert r["tenant_id_part"] == "acme"
        assert r["guardrail_action"] == "NONE"
        assert r["injection_detected"] is False
        assert r["error_code"] == ""

    def test_text_extracted_into_raw_fields(self):
        r = parse_record(ANTHROPIC_MESSAGES_LOG)
        assert "Hello, world!" in r["_input_text_raw"]
        assert "Hello! How can I help you?" in r["_output_text_raw"]
        # Before PII processing, redacted == raw
        assert r["input_text_redacted"] == r["_input_text_raw"]

    def test_titan(self):
        r = parse_record(TITAN_LOG)
        assert r["model_id"] == "amazon.titan-text-express-v1"
        assert "Summarize this document." in r["_input_text_raw"]
        assert r["user_id"] == ""
        assert r["tenant_id_part"] == "_unknown"

    def test_blocked_guardrail(self):
        r = parse_record(GUARDRAIL_BLOCKED_LOG)
        assert r["guardrail_action"] == "BLOCKED"
        assert "Violence" in r["guardrail_topics_triggered"]
        assert r["injection_detected"] is True
        assert r["guardrail_id"] == "grd-001"

    def test_error_record(self):
        r = parse_record(ERROR_LOG)
        assert r["error_code"] == "ThrottlingException"
        assert r["error_message"] == "Rate limit exceeded"

    def test_unknown_schema_returns_empty(self):
        r = parse_record({"schemaType": "SomethingElse"})
        assert r == {}

    def test_missing_metadata_uses_defaults(self):
        log = {**ANTHROPIC_MESSAGES_LOG, "requestMetadata": None}
        r = parse_record(log)
        assert r["user_id"] == ""
        assert r["tenant_id"] == ""

    def test_string_body_json_parsed(self):
        log = {**ANTHROPIC_MESSAGES_LOG}
        log["input"] = {
            "inputBodyJson": json.dumps({"messages": [{"role": "user", "content": "Test"}]}),
            "inputTokenCount": 5,
        }
        r = parse_record(log)
        assert "Test" in r["_input_text_raw"]

    def test_model_family_extraction(self):
        r = parse_record(ANTHROPIC_MESSAGES_LOG)
        # model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
        # family = first 2 dot-parts
        assert r["model_family"].startswith("anthropic.")

    def test_sampled_defaults_true(self):
        r = parse_record(ANTHROPIC_MESSAGES_LOG)
        assert r["sampled"] is True

    def test_event_time_iso_format(self):
        r = parse_record(ANTHROPIC_MESSAGES_LOG)
        # Should be parseable ISO datetime
        from datetime import datetime
        datetime.fromisoformat(r["event_time"])

    def test_invalid_timestamp_uses_now(self):
        log = {**ANTHROPIC_MESSAGES_LOG, "timestamp": "not-a-date"}
        r = parse_record(log)
        assert r["event_time"]  # should not raise and should have a value
