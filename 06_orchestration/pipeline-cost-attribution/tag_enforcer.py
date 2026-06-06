"""Apply chargeback tags to data-pipeline resources as they are created.

EventBridge fires this Lambda on `CreateJob` (Glue), `CreateStateMachine`
(Step Functions), `CreateWorkGroup` (Athena), and the analogous tag-mutating
events. We read a pipeline -> {team, business_unit, pipeline_id, env}
mapping from a JSON file in S3 and apply the tags via the resource's own
service API (Glue, Step Functions, Athena).

Configuration (env vars):
    MAPPING_BUCKET / MAPPING_KEY   JSON file mapping resource-name pattern -> tags
    DEFAULT_TEAM, DEFAULT_BU       Fallbacks for unmatched resources
    DEFAULT_ENV                    e.g. dev / prod
    REQUIRED_TAGS                  Comma-separated list of keys that must end up on the resource
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
S3 = boto3.client("s3", config=_CFG)
GLUE = boto3.client("glue", config=_CFG)
SFN = boto3.client("stepfunctions", config=_CFG)
ATHENA = boto3.client("athena", config=_CFG)

MAPPING_BUCKET = os.environ["MAPPING_BUCKET"]
MAPPING_KEY = os.environ["MAPPING_KEY"]
DEFAULT_TEAM = os.environ.get("DEFAULT_TEAM", "unassigned")
DEFAULT_BU = os.environ.get("DEFAULT_BU", "unassigned")
DEFAULT_ENV = os.environ.get("DEFAULT_ENV", "dev")
REQUIRED_TAGS = tuple(
    t.strip()
    for t in os.environ.get("REQUIRED_TAGS", "team,business_unit,pipeline_id,env").split(",")
    if t.strip()
)

_MAPPING_CACHE: dict | None = None


def _load_mapping() -> list[dict]:
    global _MAPPING_CACHE
    if _MAPPING_CACHE is not None:
        return _MAPPING_CACHE["rules"]
    raw = S3.get_object(Bucket=MAPPING_BUCKET, Key=MAPPING_KEY)["Body"].read()
    data = json.loads(raw)
    rules = data.get("rules", [])
    _MAPPING_CACHE = {"rules": rules}
    return rules


def _resolve_tags(resource_name: str) -> dict:
    for rule in _load_mapping():
        pattern = rule.get("match", "*")
        if fnmatch.fnmatchcase(resource_name, pattern):
            tags = dict(rule.get("tags", {}))
            tags.setdefault("team", DEFAULT_TEAM)
            tags.setdefault("business_unit", DEFAULT_BU)
            tags.setdefault("env", DEFAULT_ENV)
            tags.setdefault("pipeline_id", resource_name)
            return tags
    return {
        "team": DEFAULT_TEAM,
        "business_unit": DEFAULT_BU,
        "env": DEFAULT_ENV,
        "pipeline_id": resource_name,
    }


def _ensure_required(tags: dict) -> dict:
    return {k: tags.get(k, "unassigned") for k in REQUIRED_TAGS} | tags


def _tag_glue_job(name: str, tags: dict) -> None:
    sts = boto3.client("sts")
    region = boto3.session.Session().region_name
    account = sts.get_caller_identity()["Account"]
    arn = f"arn:aws:glue:{region}:{account}:job/{name}"
    GLUE.tag_resource(ResourceArn=arn, TagsToAdd=tags)


def _tag_state_machine(arn: str, tags: dict) -> None:
    sfn_tags = [{"key": k, "value": v} for k, v in tags.items()]
    SFN.tag_resource(resourceArn=arn, tags=sfn_tags)


def _tag_athena_workgroup(name: str, tags: dict) -> None:
    sts = boto3.client("sts")
    region = boto3.session.Session().region_name
    account = sts.get_caller_identity()["Account"]
    arn = f"arn:aws:athena:{region}:{account}:workgroup/{name}"
    ATHENA.tag_resource(ResourceARN=arn, Tags=[{"Key": k, "Value": v} for k, v in tags.items()])


def _handle_glue(detail: dict) -> dict:
    name = detail.get("requestParameters", {}).get("name") or detail.get(
        "responseElements", {}
    ).get("name")
    if not name:
        return {"skipped": "no glue job name"}
    tags = _ensure_required(_resolve_tags(name))
    _tag_glue_job(name, tags)
    return {"glueJob": name, "tags": tags}


def _handle_sfn(detail: dict) -> dict:
    arn = detail.get("responseElements", {}).get("stateMachineArn")
    if not arn:
        req = detail.get("requestParameters", {}) or {}
        name = req.get("name")
        if not name:
            return {"skipped": "no state machine arn or name"}
        sts = boto3.client("sts")
        region = boto3.session.Session().region_name
        account = sts.get_caller_identity()["Account"]
        arn = f"arn:aws:states:{region}:{account}:stateMachine:{name}"
    name = arn.rsplit(":", 1)[-1]
    tags = _ensure_required(_resolve_tags(name))
    _tag_state_machine(arn, tags)
    return {"stateMachine": arn, "tags": tags}


def _handle_athena(detail: dict) -> dict:
    name = detail.get("requestParameters", {}).get("name")
    if not name:
        return {"skipped": "no athena workgroup name"}
    tags = _ensure_required(_resolve_tags(name))
    _tag_athena_workgroup(name, tags)
    return {"athenaWorkgroup": name, "tags": tags}


_HANDLERS = {
    "glue.amazonaws.com": _handle_glue,
    "states.amazonaws.com": _handle_sfn,
    "athena.amazonaws.com": _handle_athena,
}


def lambda_handler(event, context):
    detail = event.get("detail", {})
    source = detail.get("eventSource", "")
    handler = _HANDLERS.get(source)
    if not handler:
        logger.info("ignoring event source=%s name=%s", source, detail.get("eventName"))
        return {"Skipped": True, "EventSource": source}
    try:
        result = handler(detail)
        logger.info("tagged %s", json.dumps(result, default=str))
        return {"Success": True, "Result": result}
    except ClientError as e:
        logger.exception("tagging failed source=%s", source)
        return {"Success": False, "Error": str(e)}


def manual_tag(resource_type: str, name_or_arn: str) -> dict:
    """Direct invoke for backfilling tags on existing resources."""
    if resource_type == "glue":
        tags = _ensure_required(_resolve_tags(name_or_arn))
        _tag_glue_job(name_or_arn, tags)
        return {"glueJob": name_or_arn, "tags": tags}
    if resource_type == "stepfunctions":
        name = name_or_arn.rsplit(":", 1)[-1]
        tags = _ensure_required(_resolve_tags(name))
        _tag_state_machine(name_or_arn, tags)
        return {"stateMachine": name_or_arn, "tags": tags}
    if resource_type == "athena":
        tags = _ensure_required(_resolve_tags(name_or_arn))
        _tag_athena_workgroup(name_or_arn, tags)
        return {"athenaWorkgroup": name_or_arn, "tags": tags}
    raise ValueError(f"unknown resource type {resource_type}")
