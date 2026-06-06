"""RULE: bedrock-model-access-tag-required

Checks that Bedrock provisioned throughput resources (ProvisionedModelThroughput)
carry mandatory classification tags so you can enforce cost attribution,
data-residency policy, and access controls via ABAC.

Resource type: AWS::Bedrock::ProvisionedModelThroughput

Rationale: Provisioned throughput creates a dedicated model endpoint that
incurs cost and processes potentially sensitive prompts. Tagging it with
DataClassification and Team enables tag-based IAM conditions (ABAC) so only
authorized workloads invoke that endpoint, and enables chargeback and audit.

Rule parameters (optional, all comma-separated):
  required_tags: comma-separated tag keys that MUST be present
                 (default: "DataClassification,Team,Environment")

NON_COMPLIANT if any required tag key is absent or has an empty value.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Bedrock::ProvisionedModelThroughput"
DEFAULT_REQUIRED_TAGS = ["DataClassification", "Team", "Environment"]


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    raw = rule_params.get("required_tags", "")
    required_tags = [t.strip() for t in raw.split(",") if t.strip()] or DEFAULT_REQUIRED_TAGS

    tags = config_item.get("tags") or {}
    resource_id = config.get("provisionedModelArn") or config_item.get("resourceId", "unknown")

    missing = [k for k in required_tags if not tags.get(k, "").strip()]
    if missing:
        return (
            Status.NON_COMPLIANT,
            f"Provisioned throughput '{resource_id}' missing required tags: "
            f"{', '.join(missing)}. Add these tags to enable ABAC and cost attribution.",
        )

    return (
        Status.COMPLIANT,
        f"Provisioned throughput '{resource_id}' has all required tags: "
        f"{', '.join(required_tags)}.",
    )


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
