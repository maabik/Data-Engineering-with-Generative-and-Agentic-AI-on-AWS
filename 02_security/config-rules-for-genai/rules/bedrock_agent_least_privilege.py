"""RULE: bedrock-agent-least-privilege

Checks that the IAM execution role attached to a Bedrock Agent does not carry
wildcarded resource ARNs on sensitive actions.

Resource type: AWS::Bedrock::Agent

Rationale: Bedrock Agents use an IAM role to call action-group Lambdas, query
Knowledge Bases, and invoke foundation models. An overly-permissive role
(e.g. bedrock:* on * or lambda:InvokeFunction on *) is exploitable via prompt
injection — an attacker can jailbreak the agent and invoke arbitrary resources
the role can reach.

NON_COMPLIANT if the agentResourceRoleArn:
  - is absent or empty
  - matches a wildcard role pattern supplied in rule_params

This rule performs a structural check only — it does NOT make IAM API calls.
For a deep policy check, combine with the managed rule
`iam-no-inline-policy-check` and your own SCP.

Rule parameters (optional):
  disallowed_role_patterns:  comma-separated glob substrings that, if found
                              in the role ARN, mark the agent NON_COMPLIANT.
                              e.g. "AdminRole,PowerUserRole,FullAccess"
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Bedrock::Agent"

# Roles whose names contain these fragments are inherently too broad for agents.
DEFAULT_DISALLOWED_FRAGMENTS = [
    "Admin", "PowerUser", "FullAccess", "AdministratorAccess",
]


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    agent_name = config.get("agentName") or config_item.get("resourceId", "unknown")
    role_arn = (config.get("agentResourceRoleArn") or "").strip()

    if not role_arn:
        return (
            Status.NON_COMPLIANT,
            f"Agent '{agent_name}' has no execution role ARN. "
            "An execution role is required.",
        )

    # Parse disallowed patterns from rule params.
    raw_patterns = rule_params.get("disallowed_role_patterns", "")
    patterns = [p.strip() for p in raw_patterns.split(",") if p.strip()]
    if not patterns:
        patterns = DEFAULT_DISALLOWED_FRAGMENTS

    # Extract role name from ARN: arn:aws:iam::123:role/AdminRole
    role_name = role_arn.split("/")[-1] if "/" in role_arn else role_arn

    for fragment in patterns:
        if fragment.lower() in role_name.lower():
            return (
                Status.NON_COMPLIANT,
                f"Agent '{agent_name}' uses role '{role_name}' which matches "
                f"disallowed pattern '{fragment}'. Use a least-privilege role "
                "scoped to the specific actions and resources the agent needs.",
            )

    return Status.COMPLIANT, f"Agent '{agent_name}' role '{role_name}' passed naming checks."


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
