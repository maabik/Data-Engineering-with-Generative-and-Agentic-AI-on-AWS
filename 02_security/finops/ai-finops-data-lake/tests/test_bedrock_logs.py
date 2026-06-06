"""Tests for pipeline/sources/bedrock_logs.py"""

import gzip
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tests.fixtures.sample_records import BEDROCK_LOG_RECORD
from pipeline.sources.bedrock_logs import (
    extract, _lookup_price, _estimate_cost, DEFAULT_PRICING
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_s3(records: list[dict], key="data/2026/01/15/records.json"):
    """Fake S3 client returning a single NDJSON object."""
    body = "\n".join(json.dumps(r) for r in records).encode()

    class FakeS3:
        def get_paginator(self, op):
            class Pager:
                def paginate(self, **kw):
                    return [{"Contents": [{"Key": key}]}]
            return Pager()

        def get_object(self, Bucket, Key):
            return {"Body": _BytesBody(body)}

    class _BytesBody:
        def __init__(self, data): self._data = data
        def read(self): return self._data

    return FakeS3()


def _make_s3_gz(records: list[dict]):
    body = gzip.compress("\n".join(json.dumps(r) for r in records).encode())
    key = "data/2026/01/15/records.json.gz"

    class FakeS3:
        def get_paginator(self, op):
            class Pager:
                def paginate(self, **kw):
                    return [{"Contents": [{"Key": key}]}]
            return Pager()
        def get_object(self, Bucket, Key):
            return {"Body": _Body(body)}

    class _Body:
        def __init__(self, d): self._d = d
        def read(self): return self._d

    return FakeS3()


# ── Tests: pricing ─────────────────────────────────────────────────────────────

class TestPricing:
    def test_exact_model_match(self):
        inp, out = _lookup_price("anthropic.claude-3-sonnet-20240229-v1:0", DEFAULT_PRICING)
        assert inp == 0.003
        assert out == 0.015

    def test_longest_prefix_wins(self):
        # claude-3-5-sonnet should match over claude-3-sonnet
        inp, _ = _lookup_price("anthropic.claude-3-5-sonnet-20241022-v2:0", DEFAULT_PRICING)
        assert inp == 0.003

    def test_unknown_model_returns_zero(self):
        inp, out = _lookup_price("unknown.model-x", DEFAULT_PRICING)
        assert inp == 0.0
        assert out == 0.0

    def test_estimate_cost_calculation(self):
        cost = _estimate_cost(1000, 500, "anthropic.claude-3-sonnet-20240229-v1:0", DEFAULT_PRICING)
        expected = (1000 / 1000.0) * 0.003 + (500 / 1000.0) * 0.015
        assert abs(cost - expected) < 1e-9

    def test_pricing_override(self):
        overrides = {"anthropic.claude-3-sonnet": (0.001, 0.005)}
        inp, out = _lookup_price("anthropic.claude-3-sonnet-20240229-v1:0", overrides)
        assert inp == 0.001

    def test_zero_tokens_zero_cost(self):
        assert _estimate_cost(0, 0, "anthropic.claude-3-sonnet-20240229-v1:0", DEFAULT_PRICING) == 0.0


# ── Tests: extract ─────────────────────────────────────────────────────────────

class TestExtract:
    def test_basic_record(self):
        s3 = _make_s3([BEDROCK_LOG_RECORD])
        records = list(extract("test-bucket", s3_client=s3))
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "bedrock_invocation"
        assert r["request_id"] == "req-001"
        assert r["tenant_id"] == "acme"
        assert r["input_tokens"] == 500
        assert r["output_tokens"] == 200
        assert r["total_tokens"] == 700
        assert r["estimated_cost_usd"] > 0
        assert r["model_family"] == "anthropic.claude-3-sonnet-20240229-v1"

    def test_gzipped_records(self):
        s3 = _make_s3_gz([BEDROCK_LOG_RECORD])
        records = list(extract("test-bucket", s3_client=s3))
        assert len(records) == 1

    def test_missing_tenant_defaults_to_unknown(self):
        rec = {**BEDROCK_LOG_RECORD, "tenant_id": ""}
        s3 = _make_s3([rec])
        records = list(extract("test-bucket", s3_client=s3))
        assert records[0]["tenant_id"] == "_unknown"

    def test_multiple_records(self):
        recs = [
            {**BEDROCK_LOG_RECORD, "request_id": f"req-{i}"}
            for i in range(5)
        ]
        s3 = _make_s3(recs)
        records = list(extract("test-bucket", s3_client=s3))
        assert len(records) == 5

    def test_bad_json_line_skipped(self):
        body = b'{"request_id":"req-1","model_id":"anthropic.claude-3-sonnet-20240229-v1:0","input_tokens":10,"output_tokens":5,"event_time":"2026-01-15T10:30:00Z","tenant_id":"acme","account_id":"","region":"","user_id":"","session_id":"","trace_id":"","latency_ms":0,"guardrail_action":"NONE","injection_detected":false,"pii_detected":false,"error_code":"","workload":""}\nnot json\n'

        class FakeS3:
            def get_paginator(self, op):
                class P:
                    def paginate(self, **kw):
                        return [{"Contents": [{"Key": "data/f.json"}]}]
                return P()
            def get_object(self, **kw):
                return {"Body": type("B", (), {"read": lambda self: body})()}

        records = list(extract("b", s3_client=FakeS3()))
        assert len(records) == 1

    def test_empty_bucket(self):
        class EmptyS3:
            def get_paginator(self, op):
                class P:
                    def paginate(self, **kw):
                        return [{"Contents": []}]
                return P()

        records = list(extract("b", s3_client=EmptyS3()))
        assert records == []

    def test_haiku_pricing(self):
        rec = {**BEDROCK_LOG_RECORD, "model_id": "anthropic.claude-3-haiku-20240307-v1:0",
               "input_tokens": 1000, "output_tokens": 1000}
        s3 = _make_s3([rec])
        records = list(extract("b", s3_client=s3))
        r = records[0]
        expected = (1000 / 1000.0) * 0.00025 + (1000 / 1000.0) * 0.00125
        assert abs(r["estimated_cost_usd"] - expected) < 1e-9
