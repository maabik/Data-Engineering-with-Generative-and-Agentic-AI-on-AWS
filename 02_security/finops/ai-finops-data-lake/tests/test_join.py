"""Tests for pipeline/join.py"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.join import build_cur_index, join_record, join_all
from tests.fixtures.sample_records import BEDROCK_LOG_RECORD, CUR_RECORD


def _inv(overrides=None):
    base = {
        "source": "bedrock_invocation",
        "date": "2026-01-15",
        "account_id": "123456789012",
        "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
        "model_family": "anthropic.claude-3-sonnet-20240229-v1",
        "tenant_id": "acme",
        "request_id": "req-001",
        "input_tokens": 500,
        "output_tokens": 200,
        "total_tokens": 700,
    }
    return {**base, **(overrides or {})}


def _cur(overrides=None):
    base = {**CUR_RECORD}
    return {**base, **(overrides or {})}


class TestBuildCurIndex:
    def test_index_by_request_id(self):
        idx = build_cur_index([_cur()])
        assert "req-001" in idx["by_request_id"]

    def test_index_by_bucket(self):
        idx = build_cur_index([_cur()])
        bucket = ("2026-01-15", "123456789012", "anthropic.claude-3-sonnet-20240229-v1:0")
        assert bucket in idx["by_bucket"]

    def test_empty_cur(self):
        idx = build_cur_index([])
        assert idx["by_request_id"] == {}
        assert idx["by_bucket"] == {}


class TestJoinRecord:
    def test_exact_request_id_match(self):
        idx = build_cur_index([_cur({"request_id": "req-001", "actual_cost_usd": 0.002})])
        result = join_record(_inv(), idx)
        assert result["joined"] is True
        assert result["join_method"] == "exact_request_id"
        assert result["actual_cost_usd"] == 0.002

    def test_fuzzy_token_match(self):
        # Same bucket, no request_id match, but tokens within 10%
        idx = build_cur_index([_cur({"request_id": "OTHER", "total_tokens": 710, "actual_cost_usd": 0.003})])
        result = join_record(_inv({"request_id": "req-xyz"}), idx)
        assert result["joined"] is True
        assert result["join_method"] == "fuzzy_token"

    def test_no_match(self):
        idx = build_cur_index([_cur({"request_id": "other", "total_tokens": 9999})])
        result = join_record(_inv({"request_id": "no-match"}), idx)
        assert result["joined"] is False
        assert result["join_method"] == "none"
        assert result["actual_cost_usd"] is None

    def test_iam_principal_propagated(self):
        idx = build_cur_index([_cur({"request_id": "req-001",
                                      "iam_principal": "arn:aws:iam::123:role/app"})])
        result = join_record(_inv({"iam_principal": ""}), idx)
        assert result["iam_principal"] == "arn:aws:iam::123:role/app"

    def test_fuzzy_outside_tolerance_no_match(self):
        # 50% difference — beyond ±10% tolerance
        idx = build_cur_index([_cur({"request_id": "other", "total_tokens": 1400})])
        result = join_record(_inv({"request_id": "r2", "total_tokens": 700}), idx)
        assert result["joined"] is False


class TestJoinAll:
    def test_join_all_matches(self):
        invocations = [_inv({"request_id": f"req-{i}"}) for i in range(3)]
        cur_records = [_cur({"request_id": f"req-{i}", "actual_cost_usd": 0.001 * (i + 1)})
                       for i in range(3)]
        results = join_all(invocations, cur_records)
        assert len(results) == 3
        assert all(r["joined"] for r in results)

    def test_join_all_no_cur(self):
        invocations = [_inv()]
        results = join_all(invocations, [])
        assert results[0]["joined"] is False

    def test_join_all_empty_invocations(self):
        results = join_all([], [_cur()])
        assert results == []

    def test_join_all_preserves_all_fields(self):
        invocations = [_inv({"trace_id": "trace-xyz"})]
        results = join_all(invocations, [_cur()])
        assert results[0]["trace_id"] == "trace-xyz"
