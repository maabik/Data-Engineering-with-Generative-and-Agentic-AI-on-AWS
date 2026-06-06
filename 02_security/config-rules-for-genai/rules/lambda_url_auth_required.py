"""RULE: lambda-url-auth-required

Checks that any Lambda Function URL hosting an MCP server (or any AI-workload
Lambda) uses AuthType AWS_IAM, not NONE.

Resource type: AWS::Lambda::Url

Rationale: Function URLs with AuthType NONE are publicly accessible without
any authentication. MCP servers behind a Function URL that is NONE can be
invoked by anyone — allowing prompt injection, tool abuse, or resource
exhaustion without any identity check.

The rule optionally restricts enforcement to Lambdas tagged with a specific
key/value (e.g. Workload=MCP or Workload=GenAI) so you can scope it to
AI-workload functions without flagging unrelated webhooks.

Rule parameters (optional):
  tag_key:   Tag key to filter functions (default: "Workload")
  tag_value: Tag value to require        (default: "" = match any value)
             Set both to "" to evaluate ALL Lambda URLs.

NON_COMPLIANT if:
  - authType is "NONE"
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Lambda::Url"

DEFAULT_TAG_KEY = "Workload"
AI_TAG_VALUES = {"MCP", "GenAI", "Bedrock", "AI"}


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    tag_key = rule_params.get("tag_key", DEFAULT_TAG_KEY).strip()
    tag_value = rule_params.get("tag_value", "").strip()

    # Tag-scoped evaluation: skip if the function doesn't carry the required tag.
    if tag_key:
        tags = config_item.get("tags") or {}
        func_tag_val = tags.get(tag_key, "")
        if tag_value and func_tag_val != tag_value:
            return Status.NOT_APPLICABLE, f"Tag {tag_key}={tag_value!r} not present — skipping."
        if not tag_value and func_tag_val not in AI_TAG_VALUES:
            return Status.NOT_APPLICABLE, f"Tag {tag_key} value {func_tag_val!r} not an AI workload tag."

    auth_type = config.get("authType", "").upper()
    func_arn = config.get("functionArn") or config_item.get("resourceId", "unknown")

    if auth_type == "NONE":
        return (
            Status.NON_COMPLIANT,
            f"Lambda URL for {func_arn} has AuthType NONE. "
            "Set AuthType to AWS_IAM to require SigV4 authentication.",
        )

    if auth_type == "AWS_IAM":
        return Status.COMPLIANT, f"Lambda URL for {func_arn} requires AWS_IAM auth."

    return (
        Status.NON_COMPLIANT,
        f"Lambda URL for {func_arn} has unknown or missing authType '{auth_type}'.",
    )


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
