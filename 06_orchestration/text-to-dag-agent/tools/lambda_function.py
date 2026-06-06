"""Lambda target for AgentCore Gateway. Hosts four MCP tools:

  - generate_asl(description)        -> Step Functions ASL JSON
  - generate_airflow_dag(description) -> Airflow DAG (Python)
  - generate_iam_policy(definition)   -> least-priv inline policy
  - validate_asl(definition)          -> validation report
  - deploy_sfn(name, definition)      -> creates a state machine and returns ARN

Gateway invokes the Lambda once per tool call. The tool name is provided in
`event['__context__']['toolName']` (per AgentCore Gateway docs).

Bedrock model + role ARN come from env vars set at deploy time.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
BEDROCK = boto3.client("bedrock-runtime", config=_CFG)
SFN = boto3.client("stepfunctions", config=_CFG)
STS = boto3.client("sts", config=_CFG)
SFN_BUILDER = boto3.client("stepfunctions", config=_CFG)  # for ValidateStateMachineDefinition

MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
SFN_ROLE_ARN = os.environ["SFN_ROLE_ARN"]
ALLOWED_RESOURCE_PREFIXES = tuple(
    p.strip()
    for p in os.environ.get(
        "ALLOWED_TASK_RESOURCE_PREFIXES",
        "arn:aws:states:::glue:,arn:aws:states:::lambda:,arn:aws:states:::aws-sdk:",
    ).split(",")
    if p.strip()
)
NAME_PREFIX = os.environ.get("STATE_MACHINE_NAME_PREFIX", "text-to-dag-agent")

_NAME_SAFE = re.compile(r"[^A-Za-z0-9-_]")


def _bedrock(prompt: str, system: str, max_tokens: int = 4000) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = BEDROCK.invoke_model(
        modelId=MODEL_ID,
        accept="application/json",
        contentType="application/json",
        body=json.dumps(body).encode("utf-8"),
    )
    payload = json.loads(resp["body"].read())
    for block in payload.get("content") or []:
        if block.get("type") == "text":
            return block["text"]
    raise RuntimeError(f"unexpected Bedrock response: {payload!r}")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _extract_json_object(text: str) -> str:
    text = _strip_fences(text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return text[start : end + 1]


# ─── Tool: generate_asl ──────────────────────────────────────────────────────

ASL_SYSTEM = (
    "You generate AWS Step Functions Amazon States Language (ASL) JSON. "
    "Return ONLY a single JSON object - no prose, no markdown fences. "
    "The JSON must have top-level keys 'StartAt' and 'States'. Every Task "
    "state's Resource MUST use AWS service integration ARNs of the form "
    "arn:aws:states:::<service>:<action>[.sync|.waitForTaskToken]. Do not "
    "invent IAM roles, account IDs, or endpoints - leave placeholder Parameters."
)


def tool_generate_asl(args: dict) -> dict:
    description = args["description"]
    raw = _bedrock(description, ASL_SYSTEM, max_tokens=4000)
    definition_str = _extract_json_object(raw)
    definition = json.loads(definition_str)
    return {"definition": definition}


# ─── Tool: generate_airflow_dag ──────────────────────────────────────────────

DAG_SYSTEM = (
    "You generate Apache Airflow 2.x DAG Python source. Return ONLY the "
    "Python file content - no prose, no markdown fences. Use the @dag and "
    "@task decorators from airflow.decorators where natural. Set "
    "schedule=None and catchup=False unless the user explicitly asked. Use "
    "AwsGlueJobOperator / S3KeySensor / EmrContainerOperator from "
    "airflow.providers.amazon.aws when AWS services are involved. The DAG "
    "id should be a valid Python identifier."
)


def tool_generate_airflow_dag(args: dict) -> dict:
    description = args["description"]
    raw = _bedrock(description, DAG_SYSTEM, max_tokens=4000)
    code = _strip_fences(raw)
    return {"dag_python": code}


# ─── Tool: generate_iam_policy ───────────────────────────────────────────────

IAM_SYSTEM = (
    "You produce least-privilege AWS IAM policy JSON for a Step Functions "
    "execution role. Return ONLY the policy document JSON - no prose. The "
    "policy must allow exactly the actions implied by the Task `Resource` "
    "service integrations in the provided ASL definition. Use specific "
    "Resource ARNs where possible; only fall back to '*' for actions that "
    "do not support resource-level perms (e.g. logs:CreateLogDelivery)."
)


def tool_generate_iam_policy(args: dict) -> dict:
    definition = args["definition"]
    if isinstance(definition, str):
        definition_str = definition
    else:
        definition_str = json.dumps(definition)
    raw = _bedrock(
        f"ASL definition:\n{definition_str}\n\nGenerate the IAM policy.",
        IAM_SYSTEM,
        max_tokens=2000,
    )
    policy = json.loads(_extract_json_object(raw))
    return {"policy": policy}


# ─── Tool: validate_asl ──────────────────────────────────────────────────────


def _validate_definition(definition: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(definition, dict):
        return ["definition must be a JSON object"]
    for key in ("StartAt", "States"):
        if key not in definition:
            errors.append(f"missing required key '{key}'")
    states = definition.get("States")
    if not isinstance(states, dict) or not states:
        errors.append("'States' must be a non-empty object")
        return errors
    if definition.get("StartAt") not in states:
        errors.append("'StartAt' references an undefined state")
    for name, state in states.items():
        if not isinstance(state, dict) or "Type" not in state:
            errors.append(f"state '{name}' missing 'Type'")
            continue
        if state["Type"] == "Task":
            resource = state.get("Resource", "")
            if not isinstance(resource, str) or not resource.startswith(
                ALLOWED_RESOURCE_PREFIXES
            ):
                errors.append(
                    f"state '{name}' Resource '{resource}' not in allowlist"
                )
    return errors


def tool_validate_asl(args: dict) -> dict:
    definition = args["definition"]
    if isinstance(definition, str):
        try:
            definition_obj = json.loads(definition)
        except json.JSONDecodeError as e:
            return {"ok": False, "errors": [f"invalid JSON: {e}"], "diagnostics": []}
        definition_str = definition
    else:
        definition_obj = definition
        definition_str = json.dumps(definition)

    errors = _validate_definition(definition_obj)
    diagnostics: list[dict] = []
    try:
        resp = SFN_BUILDER.validate_state_machine_definition(definition=definition_str)
        diagnostics = resp.get("diagnostics") or []
        for d in diagnostics:
            if d.get("severity") == "ERROR":
                errors.append(f"AWS lint: {d.get('code')}: {d.get('message')}")
    except (ClientError, AttributeError):
        logger.exception("validate_state_machine_definition unavailable")
    return {"ok": not errors, "errors": errors, "diagnostics": diagnostics}


# ─── Tool: validate_airflow_dag ──────────────────────────────────────────────
import ast


def _walk_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _called_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def tool_validate_airflow_dag(args: dict) -> dict:
    src = args["dag_python"]
    errors: list[str] = []
    warnings: list[str] = []
    info: dict[str, Any] = {}

    if not isinstance(src, str) or not src.strip():
        return {"ok": False, "errors": ["dag_python is empty"], "warnings": [], "info": {}}

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "errors": [f"SyntaxError: {e}"], "warnings": [], "info": {}}

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    info["imports"] = imports

    if not any(i == "airflow" or i.startswith("airflow.") for i in imports):
        errors.append("no `import airflow` or `from airflow...` found")

    dag_id = None
    schedule = None
    catchup = None
    saw_dag = False
    operators: list[str] = []
    operator_task_ids: list[str] = []

    for call in _walk_calls(tree):
        name = _called_name(call)
        if name == "DAG" or name == "dag":
            saw_dag = True
            for kw in call.keywords:
                if kw.arg == "dag_id" and isinstance(kw.value, ast.Constant):
                    dag_id = kw.value.value
                elif kw.arg in ("schedule", "schedule_interval") and isinstance(kw.value, ast.Constant):
                    schedule = kw.value.value
                elif kw.arg == "catchup" and isinstance(kw.value, ast.Constant):
                    catchup = kw.value.value
        if name.endswith("Operator") and name != "BaseOperator":
            operators.append(name)
            for kw in call.keywords:
                if kw.arg == "task_id" and isinstance(kw.value, ast.Constant):
                    operator_task_ids.append(kw.value.value)

    if not saw_dag:
        errors.append("no DAG(...) constructor or @dag decorator usage found")
    if dag_id is None:
        warnings.append("could not statically determine dag_id")
    elif not dag_id.replace("_", "").isalnum():
        errors.append(f"dag_id '{dag_id}' must be a valid identifier")
    if catchup is True:
        warnings.append("catchup=True will backfill on first deploy")

    if operator_task_ids and len(operator_task_ids) != len(set(operator_task_ids)):
        dupes = {t for t in operator_task_ids if operator_task_ids.count(t) > 1}
        errors.append(f"duplicate task_id values: {sorted(dupes)}")

    info.update(
        {
            "dag_id": dag_id,
            "schedule": schedule,
            "catchup": catchup,
            "operators": operators,
            "task_ids": operator_task_ids,
        }
    )
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info}


# ─── Tool: deploy_sfn ────────────────────────────────────────────────────────


def _name(raw: str) -> str:
    safe = _NAME_SAFE.sub("-", raw)[:40] or "agent"
    return f"{NAME_PREFIX}-{safe}"


def tool_deploy_sfn(args: dict) -> dict:
    name_in = args["name"]
    definition = args["definition"]
    if isinstance(definition, dict):
        definition = json.dumps(definition, separators=(",", ":"))
    errors = _validate_definition(json.loads(definition))
    if errors:
        return {"ok": False, "errors": errors}
    name = _name(name_in)
    region = boto3.session.Session().region_name
    account = STS.get_caller_identity()["Account"]
    arn = f"arn:aws:states:{region}:{account}:stateMachine:{name}"
    try:
        resp = SFN.create_state_machine(
            name=name, definition=definition, roleArn=SFN_ROLE_ARN
        )
        arn = resp["stateMachineArn"]
    except SFN.exceptions.StateMachineAlreadyExists:
        SFN.update_state_machine(
            stateMachineArn=arn, definition=definition, roleArn=SFN_ROLE_ARN
        )
    return {"ok": True, "stateMachineArn": arn, "name": name}


# ─── Dispatch ────────────────────────────────────────────────────────────────

_TOOLS = {
    "generate_asl": tool_generate_asl,
    "generate_airflow_dag": tool_generate_airflow_dag,
    "generate_iam_policy": tool_generate_iam_policy,
    "validate_asl": tool_validate_asl,
    "validate_airflow_dag": tool_validate_airflow_dag,
    "deploy_sfn": tool_deploy_sfn,
}


def _resolve_tool(event, context) -> str | None:
    cc = getattr(context, "client_context", None)
    if cc is not None:
        custom = getattr(cc, "custom", None) or {}
        full = custom.get("bedrockAgentCoreToolName")
        if full:
            return full.split("___", 1)[-1]
    return event.get("toolName") or event.get("tool_name")


def lambda_handler(event, context):
    tool = _resolve_tool(event, context)
    args = {
        k: v
        for k, v in event.items()
        if not k.startswith("__") and k not in ("toolName", "tool_name")
    }
    logger.info("invoking tool=%s args_keys=%s", tool, list(args.keys()))
    handler = _TOOLS.get(tool)
    if handler is None:
        return {"error": f"unknown tool '{tool}'", "available": list(_TOOLS)}
    try:
        return handler(args)
    except Exception as e:
        logger.exception("tool %s failed", tool)
        return {"error": str(e), "tool": tool}
