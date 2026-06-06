"""Tests for lambda/transform/transform.py (Firehose handler)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda", "transform"))

import base64
import json
import pytest


def _encode(d: dict) -> str:
    return base64.b64encode(json.dumps(d).encode()).decode()


def _decode(data: str) -> dict:
    return json.loads(base64.b64decode(data).decode().strip())


SAMPLE_LOG = {
    "schemaType": "ModelInvocationLog",
    "timestamp": "2026-01-15T10:30:00Z",
    "accountId": "123456789012",
    "region": "us-east-1",
    "requestId": "req-1",
    "modelId": "anthropic.claude-3-sonnet-20240229-v1:0",
    "input": {
        "inputBodyJson": {"messages": [{"role": "user", "content": "Hello"}]},
        "inputTokenCount": 10,
    },
    "output": {
        "outputBodyJson": {"content": [{"type": "text", "text": "Hi there"}]},
        "outputTokenCount": 5,
    },
    "requestMetadata": {"user_id": "u1", "tenant_id": "acme"},
    "performance": {"latency": 500},
}


def _make_event(records: list[dict]) -> dict:
    return {
        "records": [
            {"recordId": f"rec-{i}", "data": _encode(r)}
            for i, r in enumerate(records)
        ]
    }


class TestFirehoseHandler:
    def _patch_pii_noop(self, monkeypatch):
        """Patch PII detection to return no PII (no AWS calls)."""
        import pii

        def no_op(text, request_id):
            class NoPii:
                detected = False
                entity_types = []
                token_ids = []
                redacted_text = text
            return NoPii()

        monkeypatch.setattr(pii, "detect_and_redact", no_op)
        monkeypatch.setenv("TOKEN_BUCKET", "")
        monkeypatch.setenv("SAMPLE_RATE", "100")
        import sampling
        sampling._SAMPLE_RATE = None

    def test_ok_result_for_valid_record(self, monkeypatch):
        self._patch_pii_noop(monkeypatch)
        import transform
        event = _make_event([SAMPLE_LOG])
        result = transform.handler(event, None)
        assert len(result["records"]) == 1
        rec = result["records"][0]
        assert rec["result"] == "Ok"
        out = _decode(rec["data"])
        assert out["request_id"] == "req-1"
        assert out["user_id"] == "u1"
        assert out["tenant_id"] == "acme"

    def test_processing_failed_for_invalid_json(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "100")
        import transform
        bad_record = {"recordId": "bad-1", "data": base64.b64encode(b"not json").decode()}
        result = transform.handler({"records": [bad_record]}, None)
        assert result["records"][0]["result"] == "ProcessingFailed"

    def test_dropped_for_unknown_schema(self, monkeypatch):
        self._patch_pii_noop(monkeypatch)
        import transform
        unknown = {**SAMPLE_LOG, "schemaType": "UnknownType"}
        event = _make_event([unknown])
        result = transform.handler(event, None)
        assert result["records"][0]["result"] == "Dropped"

    def test_dropped_when_sampling_rejects(self, monkeypatch):
        import pii as _pii_mod

        def no_pii(text, rid):
            class R:
                detected = False
                entity_types = []
                token_ids = []
                redacted_text = text
            return R()

        monkeypatch.setattr(_pii_mod, "detect_and_redact", no_pii)
        monkeypatch.setenv("TOKEN_BUCKET", "")
        monkeypatch.setenv("SAMPLE_RATE", "0")

        import sampling
        sampling._SAMPLE_RATE = None

        import transform
        event = _make_event([SAMPLE_LOG])
        result = transform.handler(event, None)
        # 0% sample rate for non-flagged records → Dropped
        assert result["records"][0]["result"] == "Dropped"

    def test_pii_flagged_never_dropped(self, monkeypatch):
        import pii as _pii_mod

        def has_pii(text, rid):
            class R:
                detected = True
                entity_types = ["EMAIL"]
                token_ids = ["tok_abc"]
                redacted_text = text
            return R()

        monkeypatch.setattr(_pii_mod, "detect_and_redact", has_pii)
        monkeypatch.setenv("TOKEN_BUCKET", "")
        monkeypatch.setenv("SAMPLE_RATE", "0")  # 0% normal sampling

        import sampling
        sampling._SAMPLE_RATE = None

        import transform
        event = _make_event([SAMPLE_LOG])
        result = transform.handler(event, None)
        # PII detected → always kept regardless of sample rate
        assert result["records"][0]["result"] == "Ok"

    def test_multiple_records_processed(self, monkeypatch):
        self._patch_pii_noop(monkeypatch)
        import transform
        logs = [
            {**SAMPLE_LOG, "requestId": f"req-{i}"}
            for i in range(5)
        ]
        event = _make_event(logs)
        result = transform.handler(event, None)
        assert len(result["records"]) == 5
        assert all(r["result"] == "Ok" for r in result["records"])

    def test_pii_error_is_non_fatal(self, monkeypatch):
        import pii as _pii_mod

        call_count = {"n": 0}

        def flaky(text, rid):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Comprehend down")

            class R:
                detected = False
                entity_types = []
                token_ids = []
                redacted_text = text
            return R()

        monkeypatch.setattr(_pii_mod, "detect_and_redact", flaky)
        monkeypatch.setenv("TOKEN_BUCKET", "")
        monkeypatch.setenv("SAMPLE_RATE", "100")

        import sampling
        sampling._SAMPLE_RATE = None

        import transform
        event = _make_event([SAMPLE_LOG])
        result = transform.handler(event, None)
        # PII error is caught and logged; record is still emitted as Ok
        assert result["records"][0]["result"] == "Ok"

    def test_output_contains_sampled_field(self, monkeypatch):
        self._patch_pii_noop(monkeypatch)
        import transform
        event = _make_event([SAMPLE_LOG])
        result = transform.handler(event, None)
        out = _decode(result["records"][0]["data"])
        assert "sampled" in out
