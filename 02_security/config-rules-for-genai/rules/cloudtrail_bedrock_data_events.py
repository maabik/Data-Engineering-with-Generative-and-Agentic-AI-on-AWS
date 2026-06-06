"""RULE: cloudtrail-bedrock-data-events

Checks that at least one CloudTrail trail has data events enabled for the
Bedrock service (InvokeModel, InvokeModelWithResponseStream,
InvokeAgent, Retrieve, RetrieveAndGenerate).

Resource type: AWS::CloudTrail::Trail

Rationale: Bedrock API calls are management-plane events by default, but
InvokeModel and agent invocations are data-plane events that require explicit
CloudTrail data-event configuration. Without data events you cannot detect
unauthorized model invocations, prompt injection attempts, or data exfiltration
via the model API.

NON_COMPLIANT if:
  - The trail has no data event selectors
  - No selector covers "AWS::Bedrock::*" or the specific Bedrock ARNs
  - Advanced event selectors exist but none target the Bedrock resource type

COMPLIANT if any selector targets "AWS::Bedrock::*" or lists Bedrock resources.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::CloudTrail::Trail"

BEDROCK_EVENT_SOURCES = {
    "bedrock.amazonaws.com",
    "bedrock-agent.amazonaws.com",
    "bedrock-agent-runtime.amazonaws.com",
    "bedrock-runtime.amazonaws.com",
}

BEDROCK_ARN_PREFIXES = (
    "arn:aws:bedrock:",
    "arn:aws-cn:bedrock:",
    "arn:aws-us-gov:bedrock:",
)


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    trail_name = config.get("name") or config_item.get("resourceId", "unknown")

    # --- Classic event selectors ---
    selectors = config.get("eventSelectors") or []
    for sel in selectors:
        for dr in sel.get("dataResources", []):
            resource_type = dr.get("type", "")
            values = dr.get("values", [])
            # AWS::Bedrock::* covers all Bedrock resource types
            if resource_type.startswith("AWS::Bedrock") or resource_type == "AWS::Bedrock::*":
                return Status.COMPLIANT, f"Trail '{trail_name}' logs Bedrock data events via classic selector."
            # Or the values list explicitly references Bedrock ARNs
            if any(
                str(v).startswith(BEDROCK_ARN_PREFIXES) or str(v) == "arn:aws:bedrock:*"
                for v in values
            ):
                return Status.COMPLIANT, f"Trail '{trail_name}' logs Bedrock data events via ARN selector."

    # --- Advanced event selectors (newer API) ---
    adv_selectors = config.get("advancedEventSelectors") or []
    for adv in adv_selectors:
        for fc in adv.get("fieldSelectors", []):
            field = fc.get("field", "")
            fc_values = fc.get("equals", []) + fc.get("startsWith", [])
            if field == "eventSource" and any(
                v in BEDROCK_EVENT_SOURCES for v in fc_values
            ):
                return Status.COMPLIANT, f"Trail '{trail_name}' logs Bedrock data events via advanced selector."
            if field == "resources.type" and any(
                v.startswith("AWS::Bedrock") for v in fc_values
            ):
                return Status.COMPLIANT, f"Trail '{trail_name}' logs Bedrock data events via advanced selector."

    return (
        Status.NON_COMPLIANT,
        f"Trail '{trail_name}' does not have data events enabled for Bedrock. "
        "Add a data event selector for AWS::Bedrock::* to capture InvokeModel "
        "and agent invocations.",
    )


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
