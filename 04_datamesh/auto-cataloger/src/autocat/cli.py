"""auto-cataloger: Bedrock-powered Glue Data Catalog enrichment.

Subcommands:
  seed     -- upload seed CSVs to S3 and register Glue tables
  catalog  -- for each Glue table: sample rows, ask Bedrock for column
              descriptions / glossary suggestions / PII tags, write the
              answers back as Glue catalog metadata
  destroy  -- drop the Glue database + tables and delete the S3 bucket
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from io import StringIO
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


DEFAULT_DB = "autocat_demo"
DEFAULT_PREFIX = "autocat-demo"
SEED_TABLES = ["customers", "orders", "products"]

# Inference profile ID for Claude Sonnet 4.6 in us-* regions
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


# ------------------------------------------------------------------ helpers

def bucket_name(account_id: str, region: str, prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}-{account_id}-{region}"


def ensure_bucket(s3, name: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=name)
        return
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            if e.response["Error"]["Code"] != "403":
                raise
            # 403 typically means the bucket exists in a different account
    print(f"  creating bucket s3://{name}")
    kwargs = {"Bucket": name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)


def csv_columns(path: Path) -> list[str]:
    with path.open() as f:
        return next(csv.reader(f))


# ------------------------------------------------------------------ seed

def cmd_seed(args) -> int:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    s3 = session.client("s3")
    glue = session.client("glue")
    acct = sts.get_caller_identity()["Account"]
    name = bucket_name(acct, args.region)
    print(f"== seed: account={acct} region={args.region} bucket={name} db={args.database} ==")

    # 1. bucket
    ensure_bucket(s3, name, args.region)

    # 2. upload CSVs (one prefix per table, so each becomes its own Glue table)
    seed_dir = Path(__file__).resolve().parents[2] / "seed_data"
    for table in SEED_TABLES:
        local = seed_dir / f"{table}.csv"
        key = f"{table}/{table}.csv"
        s3.upload_file(str(local), name, key)
        print(f"  uploaded s3://{name}/{key}")

    # 3. database
    try:
        glue.create_database(DatabaseInput={"Name": args.database})
        print(f"  created Glue database {args.database}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        print(f"  reuse Glue database {args.database}")

    # 4. tables (manual EXTERNAL_TABLE registration -- skips crawler cost)
    for table in SEED_TABLES:
        cols = csv_columns(seed_dir / f"{table}.csv")
        try:
            glue.delete_table(DatabaseName=args.database, Name=table)
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityNotFoundException":
                raise
        glue.create_table(
            DatabaseName=args.database,
            TableInput={
                "Name": table,
                "TableType": "EXTERNAL_TABLE",
                "Parameters": {
                    "classification": "csv",
                    "skip.header.line.count": "1",
                    "delimiter": ",",
                },
                "StorageDescriptor": {
                    "Columns": [{"Name": c, "Type": "string"} for c in cols],
                    "Location": f"s3://{name}/{table}/",
                    "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                    "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    "SerdeInfo": {
                        "SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
                        "Parameters": {"field.delim": ","},
                    },
                },
            },
        )
        print(f"  registered Glue table {args.database}.{table} ({len(cols)} cols)")

    print()
    print(f"== seed complete: db={args.database} tables={SEED_TABLES} ==")
    return 0


# ------------------------------------------------------------------ catalog

PROMPT_SYSTEM = """You are a data catalog assistant. Given a sample of rows
from a Glue table, return JSON describing each column. Be concise and concrete.

Output strictly this JSON shape and nothing else (no prose, no code fences):

{
  "table_description": "<one-sentence business description of the table>",
  "glossary_suggestions": ["<term1>", "<term2>", ...],
  "columns": [
    {
      "name": "<column name exactly as given>",
      "description": "<one short sentence>",
      "is_pii": true|false,
      "pii_category": "<null | EMAIL | PHONE | SSN | NAME | ADDRESS | DOB | OTHER>"
    }
  ]
}

Rules:
- Output every column listed in the input schema, in the same order.
- Set is_pii=true only when the values would directly identify an individual.
- Use pii_category=null when is_pii is false."""


def sample_rows(s3, bucket: str, prefix: str, n: int = 5) -> list[dict]:
    """Read first n rows from the first CSV under the prefix."""
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=10)
    contents = resp.get("Contents", [])
    if not contents:
        return []
    key = next((o["Key"] for o in contents if o["Key"].endswith(".csv")), None)
    if not key:
        return []
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()
    reader = csv.DictReader(StringIO(body))
    return [row for _, row in zip(range(n), reader)]


def call_bedrock(brt, model_id: str, table_name: str, columns: list[str], rows: list[dict]) -> dict:
    user_msg = (
        f"Table name: {table_name}\n"
        f"Columns (in order): {columns}\n"
        f"Sample rows ({len(rows)} of them):\n"
        + json.dumps(rows, indent=2)
    )
    resp = brt.converse(
        modelId=model_id,
        system=[{"text": PROMPT_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        inferenceConfig={"maxTokens": 2000, "temperature": 0.0},
    )
    text = resp["output"]["message"]["content"][0]["text"].strip()
    # tolerate accidental code-fence wrapping
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
        text = text.rstrip("`").strip()
    return json.loads(text)


def write_back_to_glue(glue, database: str, table: str, current_table: dict, enrichment: dict) -> None:
    """Update the Glue table: column descriptions, table-level Parameters with
    PII flags / glossary suggestions / table description."""
    sd = current_table["StorageDescriptor"]
    by_name = {c["name"]: c for c in enrichment.get("columns", [])}
    new_columns = []
    pii_cols = []
    for col in sd["Columns"]:
        e = by_name.get(col["Name"], {})
        col_copy = {"Name": col["Name"], "Type": col["Type"]}
        if e.get("description"):
            col_copy["Comment"] = e["description"]
        params = {}
        if e.get("is_pii"):
            params["pii"] = "true"
            params["pii_category"] = e.get("pii_category") or "OTHER"
            pii_cols.append(col["Name"])
        else:
            params["pii"] = "false"
        col_copy["Parameters"] = params
        new_columns.append(col_copy)

    new_sd = dict(sd)
    new_sd["Columns"] = new_columns

    table_params = dict(current_table.get("Parameters", {}))
    if enrichment.get("table_description"):
        table_params["table_description_ai"] = enrichment["table_description"]
    if enrichment.get("glossary_suggestions"):
        table_params["glossary_suggestions"] = ", ".join(enrichment["glossary_suggestions"])
    if pii_cols:
        table_params["pii_columns"] = ", ".join(pii_cols)
    table_params["autocat_enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    glue.update_table(
        DatabaseName=database,
        TableInput={
            "Name": table,
            "TableType": current_table.get("TableType", "EXTERNAL_TABLE"),
            "Description": enrichment.get("table_description") or current_table.get("Description"),
            "Parameters": table_params,
            "StorageDescriptor": new_sd,
        },
    )


def cmd_catalog(args) -> int:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    s3 = session.client("s3")
    glue = session.client("glue")
    brt = session.client("bedrock-runtime")
    acct = sts.get_caller_identity()["Account"]
    print(f"== catalog: account={acct} region={args.region} db={args.database} model={args.model} ==")

    paginator = glue.get_paginator("get_tables")
    tables: list[dict] = []
    for page in paginator.paginate(DatabaseName=args.database):
        tables.extend(page.get("TableList", []))
    if not tables:
        print(f"  no tables in database {args.database}")
        return 1

    if args.tables:
        wanted = set(args.tables.split(","))
        tables = [t for t in tables if t["Name"] in wanted]

    for t in tables:
        name = t["Name"]
        cols = [c["Name"] for c in t["StorageDescriptor"]["Columns"]]
        loc = t["StorageDescriptor"]["Location"]
        # location is s3://bucket/prefix/
        assert loc.startswith("s3://"), loc
        bucket, _, prefix = loc[5:].partition("/")
        print(f"\n[{name}] columns={len(cols)} location={loc}")
        rows = sample_rows(s3, bucket, prefix, n=args.sample_size)
        print(f"  sampled {len(rows)} rows")
        if not rows:
            print("  (no sample rows; skipping)")
            continue
        enrichment = call_bedrock(brt, args.model, name, cols, rows)
        print(f"  bedrock returned: table_description, "
              f"{len(enrichment.get('glossary_suggestions', []))} glossary terms, "
              f"{sum(1 for c in enrichment.get('columns', []) if c.get('is_pii'))} pii columns")
        write_back_to_glue(glue, args.database, name, t, enrichment)
        print(f"  Glue catalog updated for {name}")

    print()
    print("== catalog complete ==")
    return 0


# ------------------------------------------------------------------ destroy

def cmd_destroy(args) -> int:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    s3 = session.client("s3")
    glue = session.client("glue")
    acct = sts.get_caller_identity()["Account"]
    name = bucket_name(acct, args.region)
    print(f"== destroy: account={acct} region={args.region} bucket={name} db={args.database} ==")

    # 1. drop database (which drops its tables)
    try:
        glue.delete_database(Name=args.database)
        print(f"  deleted Glue database {args.database}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityNotFoundException":
            raise
        print(f"  Glue database {args.database} not present, skipping")

    # 2. empty + delete bucket
    try:
        s3.head_bucket(Bucket=name)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            print(f"  bucket s3://{name} not present, skipping")
            print()
            print("== destroy complete ==")
            return 0
        raise

    paginator = s3.get_paginator("list_object_versions")
    deleted = 0
    for page in paginator.paginate(Bucket=name):
        objs = []
        for o in page.get("Versions", []) or []:
            objs.append({"Key": o["Key"], "VersionId": o["VersionId"]})
        for o in page.get("DeleteMarkers", []) or []:
            objs.append({"Key": o["Key"], "VersionId": o["VersionId"]})
        if objs:
            s3.delete_objects(Bucket=name, Delete={"Objects": objs, "Quiet": True})
            deleted += len(objs)
    # plain-list fallback for non-versioned buckets
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=name):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", []) or []]
        if objs:
            s3.delete_objects(Bucket=name, Delete={"Objects": objs, "Quiet": True})
            deleted += len(objs)
    s3.delete_bucket(Bucket=name)
    print(f"  emptied {deleted} objects and deleted bucket s3://{name}")

    print()
    print("== destroy complete ==")
    return 0


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(prog="autocat", description="Bedrock-powered Glue catalog enrichment")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "default"))
    common.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    common.add_argument("--database", default=DEFAULT_DB)

    p_seed = sub.add_parser("seed", parents=[common], help="upload CSVs and register Glue tables")
    p_seed.set_defaults(func=cmd_seed)

    p_cat = sub.add_parser("catalog", parents=[common], help="ask Bedrock to enrich each table")
    p_cat.add_argument("--model", default=DEFAULT_MODEL_ID)
    p_cat.add_argument("--sample-size", type=int, default=5)
    p_cat.add_argument("--tables", default=None,
                       help="comma-separated subset of tables to process (default: all)")
    p_cat.set_defaults(func=cmd_catalog)

    p_dst = sub.add_parser("destroy", parents=[common], help="delete bucket and Glue database")
    p_dst.set_defaults(func=cmd_destroy)

    args = parser.parse_args()
    sys.exit(args.func(args))
