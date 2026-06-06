"""RULE: bedrock-invocation-logging-enabled

Checks that Amazon Bedrock model invocation logging is enabled for the account
and that logs are sent to CloudWatch Logs and/or S3.

Resource type: AWS::Bedrock::ModelInvocationLoggingConfiguration
(Config records this as a pseudo-resource per account/region.)

Rationale: Without invocation logging you have no audit trail of what prompts
were sent to foundation models or what they returned — a critical control for
data-exfiltration detection and compliance.

NON_COMPLIANT if:
  - loggingConfig is absent or null
  - neither cloudWatchConfig.enabled nor s3Config.enabled is true
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Bedrock::ModelInvocationLoggingConfiguration"


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    logging_cfg = config.get("loggingConfig") or {}
    if not logging_cfg:
        return Status.NON_COMPLIANT, "Bedrock invocation logging is not configured."

    cw_enabled = (logging_cfg.get("cloudWatchConfig") or {}).get("enabled", False)
    s3_enabled = (logging_cfg.get("s3Config") or {}).get("enabled", False)

    if not cw_enabled and not s3_enabled:
        return (
            Status.NON_COMPLIANT,
            "Invocation logging is configured but no destination (CloudWatch or S3) is enabled.",
        )

    destinations = []
    if cw_enabled:
        destinations.append("CloudWatch")
    if s3_enabled:
        destinations.append("S3")
    return Status.COMPLIANT, f"Invocation logging enabled → {', '.join(destinations)}."


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
