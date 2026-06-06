"""RULE: mcp-server-auth-required

Checks that Lambda functions acting as MCP servers are protected by either:
  a) OAuth 2.1 — indicated by an environment variable MCP_AUTH_TYPE=oauth
  b) SigV4    — indicated by a Function URL with AuthType=AWS_IAM

A function is identified as an MCP server if it carries the tag:
  MCPServer=true  (or the tag_key/tag_value rule params)

Resource type: AWS::Lambda::Function

Rationale: MCP servers expose tool APIs to LLM agents. An MCP server without
authentication can be called by any principal on the internet, enabling:
  - Prompt injection via malicious tool responses
  - Unauthorized tool invocations (side effects, cost, data access)
  - Exfiltration of data returned by tools

This rule requires at least one of:
  1. env var MCP_AUTH_TYPE=oauth|sigv4 is set  (declares the auth mechanism)
  2. the function has a Function URL with AuthType=AWS_IAM (detected via
     supplementaryConfiguration.FunctionUrlConfig)

NON_COMPLIANT if neither condition is met.

Rule parameters (optional):
  tag_key:   Tag key to identify MCP server functions (default: "MCPServer")
  tag_value: Tag value (default: "true")
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import Evaluator, Status

RESOURCE_TYPE = "AWS::Lambda::Function"
DEFAULT_TAG_KEY = "MCPServer"
DEFAULT_TAG_VALUE = "true"

VALID_AUTH_TYPES = {"oauth", "oauth2", "sigv4", "aws_iam"}


def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    config = config_item.get("configuration") or {}
    if isinstance(config, str):
        import json
        config = json.loads(config)

    tag_key = rule_params.get("tag_key", DEFAULT_TAG_KEY).strip()
    tag_value = rule_params.get("tag_value", DEFAULT_TAG_VALUE).strip()

    # Scope: only functions tagged as MCP servers
    tags = config_item.get("tags") or {}
    if tag_key:
        actual_val = tags.get(tag_key, "")
        if tag_value and actual_val.lower() != tag_value.lower():
            return Status.NOT_APPLICABLE, f"Not an MCP server ({tag_key}={actual_val!r}) — skipping."

    func_name = config.get("functionName") or config_item.get("resourceId", "unknown")

    # Check 1: env var declares auth type
    env_vars = config.get("environment", {}).get("variables", {}) or {}
    auth_env = env_vars.get("MCP_AUTH_TYPE", "").lower().strip()
    if auth_env in VALID_AUTH_TYPES:
        return Status.COMPLIANT, f"Function '{func_name}' declares MCP_AUTH_TYPE={auth_env!r}."

    # Check 2: Function URL config with AWS_IAM auth
    supp = config_item.get("supplementaryConfiguration") or {}
    url_cfg = supp.get("FunctionUrlConfig") or {}
    if isinstance(url_cfg, str):
        import json
        url_cfg = json.loads(url_cfg) if url_cfg else {}
    url_auth = url_cfg.get("AuthType", "").upper()
    if url_auth == "AWS_IAM":
        return Status.COMPLIANT, f"Function '{func_name}' has a Function URL with AuthType=AWS_IAM."

    return (
        Status.NON_COMPLIANT,
        f"MCP server function '{func_name}' has no detected authentication. "
        "Set env var MCP_AUTH_TYPE=oauth or MCP_AUTH_TYPE=sigv4, "
        "or configure a Function URL with AuthType=AWS_IAM.",
    )


def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=[RESOURCE_TYPE],
    )
