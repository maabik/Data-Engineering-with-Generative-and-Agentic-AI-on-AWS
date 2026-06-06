"""RULE: bedrock-kb-encryption-cmk

Checks that every Bedrock Knowledge Base uses a customer-managed KMS key
(CMK) rather than an AWS-managed key or no encryption.

Resource type: AWS::Bedrock::KnowledgeBase

Rationale: Knowledge bases may contain ingested confidential documents or
proprietary data. CMKs let you control key rotation, revocation, and audit
via CloudTrail, meeting requirements for Confidential/Restricted data tiers.

NON_COMPLIANT if:
  - storageConfiguration has no kmsKeyArn, or kmsKeyArn is empty
  - kmsKeyArn belongs to an AWS-managed key (arn contains ':aws/')

Rule parameter (optional):
  required_key_prefix: ARN prefix that the KMS key must match
                       (e.g. "arn:aws:kms:us-west-2:123456789012:key/")
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Bedrock::KnowledgeBase"


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    # KMS key can live at top level or inside storageConfiguration.
    kms_arn = (
        config.get("kmsKeyArn")
        or (config.get("storageConfiguration") or {}).get("kmsKeyArn")
        or ""
    ).strip()

    kb_name = config.get("name") or config_item.get("resourceId", "unknown")

    if not kms_arn:
        return (
            Status.NON_COMPLIANT,
            f"Knowledge base '{kb_name}' is not encrypted with a CMK. "
            "Configure kmsKeyArn with a customer-managed key.",
        )

    # AWS-managed keys have ':aws/' in the ARN path or alias 'aws/...'
    if ":aws/" in kms_arn or "/aws/" in kms_arn:
        return (
            Status.NON_COMPLIANT,
            f"Knowledge base '{kb_name}' uses an AWS-managed key ({kms_arn}). "
            "Use a customer-managed CMK to maintain key control.",
        )

    required_prefix = rule_params.get("required_key_prefix", "").strip()
    if required_prefix and not kms_arn.startswith(required_prefix):
        return (
            Status.NON_COMPLIANT,
            f"Knowledge base '{kb_name}' KMS key {kms_arn!r} does not match "
            f"required prefix {required_prefix!r}.",
        )

    return Status.COMPLIANT, f"Knowledge base '{kb_name}' encrypted with CMK {kms_arn}."


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
