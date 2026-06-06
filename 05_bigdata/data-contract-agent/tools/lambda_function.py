"""Lambda target for AgentCore Gateway. Hosts MCP tools for the
data-contract-agent.

The agent operates in two modes against the same dataset:

  AUTHOR  (no contract yet for this insights file)
    - read_insights(insights_uri)
    - read_contract(table)              -> ok=true, exists=false
    - write_contract(table, contract)   -> persists YAML to s3://.../contracts/<table>.yaml
    - compile_contract_to_deequ_checks(table, contract?)
                                        -> writes a Deequ checks JSON the
                                           data-quality-deequ-agent can pick up
    - write_verdict(insights_uri, verdict)

  ENFORCE (contract exists)
    - read_insights(insights_uri)
    - read_contract(table)              -> ok=true, exists=true, contract=...
    - describe_glue_table(database, table?)
                                        -> live Glue schema, types, partition keys,
                                           description, parameters
    - diff_contract_vs_table(contract, glue_table)
                                        -> findings classified breaking|warning|info
    - write_verdict(insights_uri, verdict)

`compile_contract_to_deequ_checks` and `diff_contract_vs_table` are pure
functions (no side effects beyond the resulting S3 write); the rest read
from / write to S3 + Glue.

YAML (de)serialisation uses PyYAML, supplied via the
`data-contract-agent-pyyaml` Lambda layer that deploy.sh builds.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from typing import Any

import boto3
import yaml
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
S3 = boto3.client("s3", config=_CFG)
GLUE = boto3.client("glue", config=_CFG)

PREFIX = os.environ["PREFIX"]
DATA_BUCKET = os.environ["DATA_BUCKET"]
CONTRACTS_PREFIX = os.environ.get("CONTRACTS_PREFIX", "contracts/").rstrip("/") + "/"
VERDICTS_PREFIX = os.environ.get("VERDICTS_PREFIX", "contract-verdicts/").rstrip("/") + "/"
DEEQU_CHECKS_PREFIX = os.environ.get("DEEQU_CHECKS_PREFIX", "contract-deequ-checks/").rstrip("/") + "/"
DEFAULT_DATABASE = os.environ.get("DEFAULT_DATABASE", f"{PREFIX}_curated")

# Glue/Hive types we accept in contracts. Anything else maps to 'string' on
# normalisation so the diff doesn't false-positive on cosmetic spellings
# (e.g. varchar(255) vs string).
_TYPE_ALIASES = {
    "varchar": "string", "char": "string", "text": "string",
    "int": "int", "integer": "int",
    "long": "bigint", "bigint": "bigint",
    "smallint": "int", "tinyint": "int",
    "real": "double", "float": "double", "double": "double",
    "bool": "boolean", "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp", "datetime": "timestamp",
    "string": "string",
}


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    rest = s3_uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"malformed s3 uri: {s3_uri!r}")
    return bucket, urllib.parse.unquote_plus(key)


def _safe_name(name: str) -> str:
    n = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower() or "col"
    return n[:255]


def _normalise_type(t: str | None) -> str:
    if not t:
        return "string"
    base = t.strip().lower()
    base = re.sub(r"\(.*\)$", "", base)
    return _TYPE_ALIASES.get(base, base)


def _contract_key(table: str) -> str:
    return f"{CONTRACTS_PREFIX}{_safe_name(table)}.yaml"


# ─── Tools ──────────────────────────────────────────────────────────────────
def tool_read_insights(args: dict) -> dict:
    insights_uri = args["insights_uri"]
    bucket, key = _parse_s3_uri(insights_uri)
    body = S3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return {"ok": True, "insightsUri": insights_uri, "insights": json.loads(body)}


def tool_read_contract(args: dict) -> dict:
    table = _safe_name(args["table"])
    key = _contract_key(table)
    try:
        body = S3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {"ok": True, "exists": False, "table": table, "contractUri": f"s3://{DATA_BUCKET}/{key}"}
        raise
    contract = yaml.safe_load(body) or {}
    if not isinstance(contract, dict):
        return {"ok": False, "error": f"contract YAML at s3://{DATA_BUCKET}/{key} did not parse to a mapping"}
    return {
        "ok": True,
        "exists": True,
        "table": table,
        "contractUri": f"s3://{DATA_BUCKET}/{key}",
        "contract": contract,
        "yaml": body,
    }


def tool_write_contract(args: dict) -> dict:
    table = _safe_name(args["table"])
    contract = args["contract"]
    if not isinstance(contract, dict):
        return {"ok": False, "error": "contract must be a dict/object"}
    contract.setdefault("table", table)
    yaml_body = yaml.safe_dump(contract, sort_keys=False, default_flow_style=False)
    key = _contract_key(table)
    S3.put_object(
        Bucket=DATA_BUCKET, Key=key,
        Body=yaml_body.encode("utf-8"),
        ContentType="application/yaml",
    )
    return {"ok": True, "table": table, "contractUri": f"s3://{DATA_BUCKET}/{key}", "bytes": len(yaml_body)}


def tool_describe_glue_table(args: dict) -> dict:
    database = args.get("database") or DEFAULT_DATABASE
    table = _safe_name(args["table"])
    try:
        resp = GLUE.get_table(DatabaseName=database, Name=table)["Table"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityNotFoundException":
            return {"ok": True, "exists": False, "database": database, "table": table}
        raise
    sd = resp.get("StorageDescriptor", {})
    cols = [
        {"name": c["Name"], "type": _normalise_type(c.get("Type", "string"))}
        for c in sd.get("Columns", [])
    ]
    parts = [
        {"name": p["Name"], "type": _normalise_type(p.get("Type", "string"))}
        for p in resp.get("PartitionKeys", [])
    ]
    return {
        "ok": True,
        "exists": True,
        "database": database,
        "table": table,
        "description": resp.get("Description"),
        "location": sd.get("Location"),
        "format": resp.get("Parameters", {}).get("classification"),
        "columns": cols,
        "partitionKeys": parts,
        "parameters": resp.get("Parameters", {}),
    }


def _column_index(cols: list[dict]) -> dict[str, dict]:
    return {_safe_name(c["name"]): {"name": _safe_name(c["name"]), "type": _normalise_type(c.get("type"))} for c in cols}


def tool_diff_contract_vs_table(args: dict) -> dict:
    contract = args["contract"]
    glue_table = args["glue_table"]

    if not glue_table.get("exists", True):
        return {
            "ok": True,
            "status": "breaking",
            "findings": [{
                "severity": "breaking",
                "code": "table_missing",
                "message": f"contract references {glue_table.get('database')}.{glue_table.get('table')} but the live Glue table does not exist",
            }],
        }

    contract_cols = _column_index(contract.get("columns") or [])
    live_cols_list = (glue_table.get("columns") or []) + (glue_table.get("partitionKeys") or [])
    live_cols = _column_index(live_cols_list)

    deprecated = {_safe_name(n) for n in (contract.get("deprecated_columns") or [])}
    findings: list[dict] = []

    for name, spec in contract_cols.items():
        required = bool((contract.get("columns") or [{}])[0]) and any(
            _safe_name(c.get("name", "")) == name and bool(c.get("required", False))
            for c in (contract.get("columns") or [])
        )
        live = live_cols.get(name)
        if live is None:
            findings.append({
                "severity": "breaking" if required else "warning",
                "code": "column_missing",
                "column": name,
                "message": (
                    f"required column '{name}' missing from live table"
                    if required else
                    f"optional column '{name}' missing from live table"
                ),
            })
            continue
        if live["type"] != spec["type"]:
            findings.append({
                "severity": "breaking",
                "code": "type_changed",
                "column": name,
                "message": f"column '{name}' type changed: contract={spec['type']} live={live['type']}",
                "contractType": spec["type"],
                "liveType": live["type"],
            })

    for name in live_cols:
        if name in contract_cols:
            continue
        if name in deprecated:
            findings.append({
                "severity": "warning",
                "code": "deprecated_present",
                "column": name,
                "message": f"deprecated column '{name}' is still present in live table",
            })
        else:
            findings.append({
                "severity": "info",
                "code": "column_added",
                "column": name,
                "message": f"live table has column '{name}' not declared in contract (additive, safe)",
            })

    if not (glue_table.get("description") or "").strip():
        findings.append({
            "severity": "warning",
            "code": "description_missing",
            "message": "live Glue table has no Description set",
        })

    severities = {f["severity"] for f in findings}
    status = "breaking" if "breaking" in severities else ("warning" if "warning" in severities else "ok")

    return {
        "ok": True,
        "status": status,
        "totalFindings": len(findings),
        "breaking": sum(1 for f in findings if f["severity"] == "breaking"),
        "warning":  sum(1 for f in findings if f["severity"] == "warning"),
        "info":     sum(1 for f in findings if f["severity"] == "info"),
        "findings": findings,
    }


def _deequ_checks_from_contract(contract: dict) -> list[dict]:
    """Compile contract clauses into Deequ-flavoured constraint specs.

    Output is a list of {column?, constraint, args} the data-quality-deequ-agent
    can wire into pydeequ's VerificationSuite. Schema-level concerns (column
    presence, type, description) are NOT compiled here — they belong to the
    contract diff, not Deequ.
    """
    checks: list[dict] = []
    cols = contract.get("columns") or []
    for c in cols:
        name = _safe_name(c.get("name", ""))
        if not name:
            continue
        if c.get("required"):
            checks.append({"column": name, "constraint": "isComplete", "args": {}})
        if c.get("unique"):
            checks.append({"column": name, "constraint": "isUnique", "args": {}})
        if isinstance(c.get("allowed_values"), list):
            checks.append({
                "column": name, "constraint": "isContainedIn",
                "args": {"allowed": list(c["allowed_values"])},
            })
        if isinstance(c.get("pattern"), str):
            checks.append({"column": name, "constraint": "hasPattern", "args": {"regex": c["pattern"]}})
        if c.get("min") is not None:
            checks.append({"column": name, "constraint": "satisfies", "args": {"expr": f"{name} >= {c['min']}"}})
        if c.get("max") is not None:
            checks.append({"column": name, "constraint": "satisfies", "args": {"expr": f"{name} <= {c['max']}"}})
        if isinstance(c.get("nullable_threshold"), (int, float)):
            checks.append({
                "column": name, "constraint": "hasCompleteness",
                "args": {"min": 1.0 - float(c["nullable_threshold"])},
            })
    if isinstance(contract.get("min_row_count"), int):
        checks.append({"column": None, "constraint": "hasSize", "args": {"min": contract["min_row_count"]}})
    return checks


def tool_compile_contract_to_deequ_checks(args: dict) -> dict:
    table = _safe_name(args["table"])
    contract = args.get("contract")
    if contract is None:
        existing = tool_read_contract({"table": table})
        if not existing.get("exists"):
            return {"ok": False, "error": f"no contract found for {table}"}
        contract = existing["contract"]
    checks = _deequ_checks_from_contract(contract)
    key = f"{DEEQU_CHECKS_PREFIX}{table}.json"
    body = json.dumps({"table": table, "checks": checks}, indent=2).encode("utf-8")
    S3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body, ContentType="application/json")
    return {
        "ok": True,
        "table": table,
        "checksUri": f"s3://{DATA_BUCKET}/{key}",
        "checkCount": len(checks),
    }


def tool_write_verdict(args: dict) -> dict:
    insights_uri = args["insights_uri"]
    verdict = args["verdict"]
    base = os.path.basename(_parse_s3_uri(insights_uri)[1]).rsplit(".", 1)[0] or "verdict"
    key = f"{VERDICTS_PREFIX}{base}.json"
    body = json.dumps(verdict, indent=2).encode("utf-8")
    S3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body, ContentType="application/json")
    return {"ok": True, "verdictUri": f"s3://{DATA_BUCKET}/{key}", "bytes": len(body)}


_TOOLS = {
    "read_insights": tool_read_insights,
    "read_contract": tool_read_contract,
    "write_contract": tool_write_contract,
    "describe_glue_table": tool_describe_glue_table,
    "diff_contract_vs_table": tool_diff_contract_vs_table,
    "compile_contract_to_deequ_checks": tool_compile_contract_to_deequ_checks,
    "write_verdict": tool_write_verdict,
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
        return {"error": f"unknown tool {tool!r}", "available": list(_TOOLS)}
    try:
        return handler(args)
    except Exception as e:
        logger.exception("tool %s failed", tool)
        return {"error": f"{type(e).__name__}: {e}", "tool": tool}
