"""RULE: bedrock-guardrail-attached

Checks that every Bedrock Agent has a Guardrail attached.

Resource type: AWS::Bedrock::Agent

Rationale: Guardrails enforce topic restrictions, PII masking, and content
filters at the inference layer. An agent without a guardrail has no safety
net independent of whatever system-prompt instructions it was given.

NON_COMPLIANT if:
  - guardrailConfiguration is absent, null, or guardrailId is empty/missing
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Bedrock::Agent"


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    guardrail = config.get("guardrailConfiguration") or {}
    guardrail_id = guardrail.get("guardrailId", "").strip()

    if not guardrail_id:
        agent_name = config.get("agentName") or config_item.get("resourceId", "unknown")
        return (
            Status.NON_COMPLIANT,
            f"Agent '{agent_name}' has no Guardrail attached. "
            "Attach a Guardrail to enforce content and topic policies.",
        )

    version = guardrail.get("guardrailVersion", "DRAFT")
    return Status.COMPLIANT, f"Guardrail '{guardrail_id}' (version {version}) is attached."


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
