"""RULE: s3-ai-data-no-public-access

Checks that S3 buckets used to store AI training data, model artefacts, or
Knowledge Base source documents have the S3 Block Public Access setting fully
enabled (all four flags true).

Resource type: AWS::S3::Bucket

Rationale: Accidentally public AI data buckets expose training corpora,
inference logs, and fine-tuned model artefacts to the internet. S3 Block
Public Access is a defence-in-depth control that overrides any ACL or bucket
policy that would otherwise grant public access.

The rule is scoped to buckets tagged with tag_key=tag_value to avoid
flagging intentionally public website or media buckets.

Rule parameters (optional):
  tag_key:   Tag key to identify AI-data buckets (default: "DataClassification")
  tag_value: Tag value to require (default: "" = match AI_DATA_VALUES set)
             Set both to "" to evaluate ALL S3 buckets.

NON_COMPLIANT if any of BlockPublicAcls / IgnorePublicAcls /
BlockPublicPolicy / RestrictPublicBuckets is false.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::S3::Bucket"
DEFAULT_TAG_KEY = "DataClassification"
AI_DATA_VALUES = {"AI", "AIData", "Confidential", "Restricted", "GenAI"}
BPA_FLAGS = ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    tag_key = rule_params.get("tag_key", DEFAULT_TAG_KEY).strip()
    tag_value = rule_params.get("tag_value", "").strip()

    if tag_key:
        tags = config_item.get("tags") or {}
        actual_val = tags.get(tag_key, "")
        if tag_value and actual_val != tag_value:
            return Status.NOT_APPLICABLE, f"Tag {tag_key}={tag_value!r} not present — skipping."
        if not tag_value and actual_val not in AI_DATA_VALUES:
            return Status.NOT_APPLICABLE, f"Tag {tag_key}={actual_val!r} not an AI data tag."

    bucket_name = config.get("bucketName") or config_item.get("resourceId", "unknown")
    bpa = config.get("publicAccessBlockConfiguration") or {}

    disabled = [flag for flag in BPA_FLAGS if not bpa.get(flag, False)]
    if disabled:
        return (
            Status.NON_COMPLIANT,
            f"Bucket '{bucket_name}' has Block Public Access disabled for: "
            f"{', '.join(disabled)}. Enable all four BPA flags.",
        )

    return Status.COMPLIANT, f"Bucket '{bucket_name}' has all Block Public Access flags enabled."


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
