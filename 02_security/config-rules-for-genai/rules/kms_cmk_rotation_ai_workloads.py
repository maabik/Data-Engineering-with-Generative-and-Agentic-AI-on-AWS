"""RULE: kms-cmk-rotation-ai-workloads

Checks that KMS customer-managed keys used for AI workloads:
  1. Have automatic key rotation enabled (yearly).
  2. Are single-region keys (not multi-region primaries or replicas).

Resource type: AWS::KMS::Key

Rationale: AI workloads (Bedrock KB encryption, S3 data-lake buckets, model
artefact stores) should use CMKs that rotate automatically to limit the blast
radius of credential exposure. Multi-region keys add replication complexity
and cross-region data residency risk; single-region CMKs keep data in one
regulatory jurisdiction.

The rule is scoped to keys tagged Workload=AI (or any value in AI_TAG_VALUES)
to avoid flagging CMKs used by non-AI workloads.

Rule parameters (optional):
  tag_key:               Tag key to identify AI-workload keys (default: "Workload")
  tag_value:             Tag value to match (default: "" = match AI_TAG_VALUES set)
  allow_multi_region:    "true" to suppress multi-region finding (default: "false")

NON_COMPLIANT conditions:
  - keyRotationEnabled is false
  - multiRegion is true (unless allow_multi_region="true")
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::KMS::Key"
DEFAULT_TAG_KEY = "Workload"
AI_TAG_VALUES = {"AI", "GenAI", "Bedrock", "MCP"}


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    tag_key = rule_params.get("tag_key", DEFAULT_TAG_KEY).strip()
    tag_value = rule_params.get("tag_value", "").strip()
    allow_multi = rule_params.get("allow_multi_region", "false").lower() == "true"

    # Skip AWS-managed keys (keyManager == "AWS")
    if config.get("keyManager", "").upper() == "AWS":
        return Status.NOT_APPLICABLE, "AWS-managed key — not in scope."

    # Skip keys in pending deletion or disabled
    key_state = config.get("keyState", "").upper()
    if key_state in ("PENDINGDELETION", "UNAVAILABLE", "PENDINGIMPORT"):
        return Status.NOT_APPLICABLE, f"Key state {key_state} — not in scope."

    # Tag scope check
    if tag_key:
        tags = config_item.get("tags") or {}
        actual_val = tags.get(tag_key, "")
        if tag_value and actual_val != tag_value:
            return Status.NOT_APPLICABLE, f"Tag {tag_key}={tag_value!r} not present — skipping."
        if not tag_value and actual_val not in AI_TAG_VALUES:
            return Status.NOT_APPLICABLE, f"Tag {tag_key}={actual_val!r} not an AI workload value."

    key_id = config.get("keyId") or config_item.get("resourceId", "unknown")
    findings: list[str] = []

    if not config.get("keyRotationEnabled", False):
        findings.append("automatic key rotation is disabled")

    if not allow_multi and config.get("multiRegion", False):
        findings.append("key is multi-region (data residency risk)")

    if findings:
        return Status.NON_COMPLIANT, f"KMS key {key_id}: {'; '.join(findings)}."

    return Status.COMPLIANT, f"KMS key {key_id}: rotation enabled, single-region."


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
