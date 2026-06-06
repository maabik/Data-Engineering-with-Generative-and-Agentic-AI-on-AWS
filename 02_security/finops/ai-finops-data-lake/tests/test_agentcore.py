"""Tests for pipeline/sources/agentcore.py"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
import pytest
from tests.fixtures.sample_records import AGENTCORE_LOG_EVENT
from pipeline.sources.agentcore import extract


def _make_cw(events: list[dict], log_group="/aws/bedrock/agentcore/runtime"):
    """Fake CloudWatch Logs client."""
    encoded = [{"message": json.dumps(e)} for e in events]

    class FakeCW:
        def get_paginator(self, op):
            if op == "filter_log_events":
                class FP:
                    def paginate(self, **kw):
                        return [{"events": encoded}]
                return FP()
            if op == "describe_log_groups":
                class DP:
                    def paginate(self, **kw):
                        return [{"logGroups": [{"logGroupName": log_group}]}]
                return DP()

    return FakeCW()


class TestAgentcoreExtract:
    def test_basic_execution(self):
        cw = _make_cw([AGENTCORE_LOG_EVENT])
        records = list(extract(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
            cw_client=cw,
            account_id="123456789012",
            region="us-east-1",
        ))
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "agentcore_runtime"
        assert r["request_id"] == "exec-001"
        assert r["agent_id"] == "ag-001"
        assert r["agent_name"] == "my-agent"
        assert r["tenant_id"] == "globex"
        assert r["workload"] == "support-bot"
        assert r["input_tokens"] == 800
        assert r["output_tokens"] == 300
        assert r["total_tokens"] == 1100
        assert r["model_invocations"] == 3
        assert r["tool_invocations"] == 2
        assert r["estimated_cost_usd"] > 0
        assert r["latency_ms"] == 5000  # 5 seconds

    def test_failed_execution(self):
        evt = {**AGENTCORE_LOG_EVENT, "status": "FAILED", "executionId": "exec-fail"}
        cw = _make_cw([evt])
        records = list(extract(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
            cw_client=cw,
        ))
        assert records[0]["error_code"] == "FAILED"

    def test_no_log_groups_returns_empty(self):
        class NoCW:
            def get_paginator(self, op):
                class P:
                    def paginate(self, **kw):
                        return [{"logGroups": []}]
                return P()

        records = list(extract(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
            log_group_prefix="/nonexistent/prefix",
            cw_client=NoCW(),
        ))
        assert records == []

    def test_tenant_fallback_from_tags(self):
        evt = {**AGENTCORE_LOG_EVENT,
               "requestMetadata": {},
               "costAllocationTags": {"TenantId": "fallback-tenant"}}
        cw = _make_cw([evt])
        records = list(extract(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
            cw_client=cw,
        ))
        assert records[0]["tenant_id"] == "fallback-tenant"

    def test_missing_metadata_defaults(self):
        evt = {**AGENTCORE_LOG_EVENT,
               "requestMetadata": None,
               "costAllocationTags": None}
        cw = _make_cw([evt])
        records = list(extract(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
            cw_client=cw,
        ))
        assert records[0]["tenant_id"] == "_unknown"

    def test_invalid_json_skipped(self):
        class BadCW:
            def get_paginator(self, op):
                if op == "filter_log_events":
                    class FP:
                        def paginate(self, **kw):
                            return [{"events": [{"message": "not json"}, {"message": json.dumps(AGENTCORE_LOG_EVENT)}]}]
                    return FP()
                if op == "describe_log_groups":
                    class DP:
                        def paginate(self, **kw):
                            return [{"logGroups": [{"logGroupName": "/aws/bedrock/agentcore/runtime"}]}]
                    return DP()

        records = list(extract(
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 15),
            cw_client=BadCW(),
        ))
        # Bad JSON skipped, valid event extracted
        assert len(records) == 1
