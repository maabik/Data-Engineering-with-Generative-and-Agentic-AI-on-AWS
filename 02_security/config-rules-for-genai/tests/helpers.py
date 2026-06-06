"""Test helpers: fake Config client and config-item builders."""

from __future__ import annotations
import json


class FakeConfigClient:
    """Captures put_evaluations calls without hitting AWS."""
    def __init__(self):
        self.calls: list[list[dict]] = []

    def put_evaluations(self, Evaluations, ResultToken):
        self.calls.append(Evaluations)

    @property
    def all_evaluations(self) -> list[dict]:
        return [e for batch in self.calls for e in batch]

    def compliance_types(self) -> list[str]:
        return [e["ComplianceType"] for e in self.all_evaluations]


def make_event(
    resource_type: str,
    resource_id: str,
    configuration: dict | str,
    tags: dict | None = None,
    status: str = "OK",
    supplementary: dict | None = None,
    rule_params: dict | None = None,
    message_type: str = "ConfigurationItemChangeNotification",
) -> dict:
    ci = {
        "resourceType": resource_type,
        "resourceId": resource_id,
        "configuration": configuration if isinstance(configuration, str) else json.dumps(configuration),
        "configurationItemStatus": status,
        "configurationItemCaptureTime": "2026-01-01T00:00:00Z",
        "tags": tags or {},
    }
    if supplementary:
        ci["supplementaryConfiguration"] = supplementary
    return {
        "invokingEvent": json.dumps({
            "configurationItem": ci,
            "messageType": message_type,
        }),
        "ruleParameters": json.dumps(rule_params or {}),
        "resultToken": "TESTTOKEN",
        "eventLeftScope": False,
    }
