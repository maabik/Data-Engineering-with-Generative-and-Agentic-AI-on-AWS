"""Tests for lambda/recorder/handler.py using moto DynamoDB."""

import sys, os
_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _ROOT)
# handler.py lives in lambda/recorder/ — add that dir so `import handler` works
_HANDLER_DIR = os.path.join(_ROOT, "lambda", "recorder")
sys.path.insert(0, _HANDLER_DIR)

from datetime import datetime, timezone
import boto3
import pytest

try:
    from moto import mock_aws
    _MOTO = True
except ImportError:
    _MOTO = False

pytestmark = pytest.mark.skipif(not _MOTO, reason="moto not installed")

_TABLE = "ai-token-budgets"


def _create_table(ddb):
    ddb.create_table(
        TableName=_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("BUDGET_TABLE", _TABLE)
    monkeypatch.setenv("ALERT_TOPIC_ARN", "")


# ── Direct invocation (budget_gateway source) ─────────────────────────────────

class TestDirectInvocation:
    def test_records_usage_and_returns_state(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_table(ddb)
            import importlib
            import handler as h
            importlib.reload(h)
            h._ddb = ddb

            event = {
                "source": "budget_gateway",
                "tenant_id": "acme",
                "user_id": "alice",
                "agent_id": "",
                "model_id": "amazon.nova-micro-v1:0",
                "input_tokens": 500,
                "output_tokens": 200,
                "cost_usd": 0.005,
            }
            result = h.handler(event, None)
            assert result["statusCode"] == 200
            assert result["processed"] is True
            assert result["state"] in ("ok", "throttled", "blocked", "unknown")

    def test_no_tenant_id_skips_record(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_table(ddb)
            import importlib
            import handler as h
            importlib.reload(h)
            h._ddb = ddb

            event = {
                "source": "budget_gateway",
                "tenant_id": "",
                "model_id": "amazon.nova-micro-v1:0",
                "input_tokens": 100,
                "output_tokens": 50,
            }
            result = h.handler(event, None)
            assert result["statusCode"] == 200
            assert result["processed"] is False


# ── CloudTrail / EventBridge source ──────────────────────────────────────────

class TestCloudTrailEvent:
    def _ct_event(self, model_id="amazon.nova-micro-v1:0",
                  input_t=100, output_t=50, tenant="acme", user="alice"):
        return {
            "source": "aws.bedrock",
            "detail": {
                "eventName": "Converse",
                "requestParameters": {
                    "modelId": model_id,
                    "requestMetadata": {
                        "tenant_id": tenant,
                        "user_id": user,
                    },
                },
                "responseElements": {
                    "usage": {
                        "inputTokens": input_t,
                        "outputTokens": output_t,
                    }
                },
            },
        }

    def test_cloudtrail_event_recorded(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_table(ddb)
            import importlib
            import handler as h
            importlib.reload(h)
            h._ddb = ddb

            result = h.handler(self._ct_event(), None)
            assert result["statusCode"] == 200
            assert result["processed"] is True

    def test_cloudtrail_zero_tokens_skipped(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_table(ddb)
            import importlib
            import handler as h
            importlib.reload(h)
            h._ddb = ddb

            event = self._ct_event(input_t=0, output_t=0)
            result = h.handler(event, None)
            assert result["processed"] is False

    def test_unknown_source_returns_400(self):
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_table(ddb)
            import importlib
            import handler as h
            importlib.reload(h)
            h._ddb = ddb

            result = h.handler({"source": "something_else"}, None)
            assert result["statusCode"] == 400
