"""Post-crawl Lambda: schema drift detection, Bedrock annotation, metadata tagging.

Triggered by EventBridge when the Glue crawler reaches SUCCEEDED state.

Flow:
  1. Load current schema from Glue catalog
  2. Compare against snapshot in S3 — emit SNS drift alert if changed
  3. Save new snapshot to S3
  4. Call Bedrock (Claude) to generate table + column descriptions
  5. Write descriptions back to Glue catalog
  6. Tag table with governance metadata
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET_NAME = os.environ["BUCKET_NAME"]
DATABASE_NAME = os.environ["DATABASE_NAME"]
TABLE_NAME = os.environ["TABLE_NAME"]
DRIFT_TOPIC_ARN = os.environ["DRIFT_TOPIC_ARN"]
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
SNAPSHOT_KEY = "schema-snapshots/bitcoin_prices_schema.json"

METADATA_TAGS = {
    "owner": os.environ.get("METADATA_OWNER", "data-engineering"),
    "data_classification": os.environ.get("METADATA_CLASSIFICATION", "public"),
    "source_system": os.environ.get("METADATA_SOURCE_SYSTEM", "coindesk-api"),
    "sla_tier": os.environ.get("METADATA_SLA_TIER", "bronze"),
}

glue = boto3.client("glue")
s3 = boto3.client("s3")
sns = boto3.client("sns")
bedrock = boto3.client("bedrock-runtime")


# ── Schema helpers ────────────────────────────────────────────────────────────

def get_current_schema() -> dict:
    response = glue.get_table(DatabaseName=DATABASE_NAME, Name=TABLE_NAME)
    table = response["Table"]
    columns = table.get("StorageDescriptor", {}).get("Columns", [])
    return {
        "table_name": TABLE_NAME,
        "database_name": DATABASE_NAME,
        "columns": [
            {"name": c["Name"], "type": c["Type"], "comment": c.get("Comment", "")}
            for c in columns
        ],
    }


def load_snapshot() -> dict | None:
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=SNAPSHOT_KEY)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def save_snapshot(schema: dict) -> None:
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=SNAPSHOT_KEY,
        Body=json.dumps(schema, indent=2),
        ContentType="application/json",
    )


# ── Drift detection ───────────────────────────────────────────────────────────

def detect_drift(previous: dict | None, current: dict) -> dict | None:
    if previous is None:
        return None

    prev_cols = {c["name"]: c["type"] for c in previous["columns"]}
    curr_cols = {c["name"]: c["type"] for c in current["columns"]}

    added = [n for n in curr_cols if n not in prev_cols]
    removed = [n for n in prev_cols if n not in curr_cols]
    type_changed = [
        {"column": n, "from": prev_cols[n], "to": curr_cols[n]}
        for n in curr_cols
        if n in prev_cols and curr_cols[n] != prev_cols[n]
    ]

    if not added and not removed and not type_changed:
        return None

    return {"added": added, "removed": removed, "type_changed": type_changed}


def publish_drift_alert(drift: dict) -> None:
    message = (
        f"Schema drift detected in {DATABASE_NAME}.{TABLE_NAME}\n\n"
        f"Added columns: {drift['added'] or 'none'}\n"
        f"Removed columns: {drift['removed'] or 'none'}\n"
        f"Type changes: {drift['type_changed'] or 'none'}"
    )
    sns.publish(
        TopicArn=DRIFT_TOPIC_ARN,
        Subject=f"[Schema Drift] {DATABASE_NAME}.{TABLE_NAME}",
        Message=message,
    )
    logger.info("Drift alert published: %s", drift)


# ── Bedrock annotation ────────────────────────────────────────────────────────

def generate_annotations(schema: dict) -> dict:
    columns_text = "\n".join(
        f"  - {c['name']} ({c['type']})" for c in schema["columns"]
    )
    prompt = f"""You are a data catalog documentation expert. Given this Glue table schema, write concise, business-friendly descriptions.

Table: {schema['table_name']}
Database: {schema['database_name']}
Columns:
{columns_text}

Respond with valid JSON only — no markdown, no explanation — in this exact shape:
{{
  "table_description": "<one sentence describing what this table contains>",
  "columns": {{
    "<column_name>": "<one sentence description>",
    ...
  }}
}}"""

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )

    body = json.loads(response["body"].read())
    text = body["content"][0]["text"].strip()
    # Strip markdown code fences if the model wraps the JSON
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


# ── Glue catalog updates ──────────────────────────────────────────────────────

def apply_annotations(schema: dict, annotations: dict) -> None:
    table_response = glue.get_table(DatabaseName=DATABASE_NAME, Name=TABLE_NAME)
    table_input = table_response["Table"]

    # Strip read-only fields before passing back to update_table
    for key in ("DatabaseName", "CreateTime", "UpdateTime", "CreatedBy", "IsRegisteredWithLakeFormation", "CatalogId", "VersionId", "IsMultiDialectView", "Status"):
        table_input.pop(key, None)

    table_input["Description"] = annotations.get("table_description", "")

    col_descriptions = annotations.get("columns", {})
    for col in table_input.get("StorageDescriptor", {}).get("Columns", []):
        if col["Name"] in col_descriptions:
            col["Comment"] = col_descriptions[col["Name"]]

    glue.update_table(DatabaseName=DATABASE_NAME, TableInput=table_input)
    logger.info("Applied Bedrock annotations to %s.%s", DATABASE_NAME, TABLE_NAME)


def apply_metadata_tags(schema: dict) -> None:
    table_response = glue.get_table(DatabaseName=DATABASE_NAME, Name=TABLE_NAME)
    table_input = table_response["Table"]

    for key in ("DatabaseName", "CreateTime", "UpdateTime", "CreatedBy", "IsRegisteredWithLakeFormation", "CatalogId", "VersionId", "IsMultiDialectView", "Status"):
        table_input.pop(key, None)

    params = table_input.setdefault("Parameters", {})
    params.update(METADATA_TAGS)

    glue.update_table(DatabaseName=DATABASE_NAME, TableInput=table_input)
    logger.info("Applied metadata tags: %s", METADATA_TAGS)


# ── Handler ───────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    logger.info("Post-crawl annotation triggered: %s", json.dumps(event))

    schema = get_current_schema()
    logger.info("Loaded schema: %d columns", len(schema["columns"]))

    snapshot = load_snapshot()
    drift = detect_drift(snapshot, schema)
    if drift:
        publish_drift_alert(drift)
    else:
        logger.info("No schema drift detected")

    save_snapshot(schema)

    annotations = generate_annotations(schema)
    logger.info("Bedrock annotations generated for %d columns", len(annotations.get("columns", {})))

    apply_annotations(schema, annotations)
    apply_metadata_tags(schema)

    return {
        "statusCode": 200,
        "drift_detected": drift is not None,
        "columns_annotated": len(annotations.get("columns", {})),
    }
