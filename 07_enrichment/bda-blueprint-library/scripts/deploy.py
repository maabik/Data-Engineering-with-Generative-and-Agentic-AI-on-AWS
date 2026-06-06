#!/usr/bin/env python3
"""Deploy the BDA blueprint library.

Idempotent: if a blueprint with the same name already exists in this account,
it is updated in place via UpdateBlueprint instead of recreated.

Outputs build/blueprint-arns.json:
  {
    "region": "...",
    "bucket": "bda-blueprint-library-<acct>-<region>",
    "blueprints": {
      "invoice": "arn:aws:bedrock:.../blueprint/...",
      ...
    },
    "data_automation_profile_arn": "arn:aws:bedrock:..."
  }
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT_DIR = ROOT / "blueprints"
CORPUS_DIR = ROOT / "corpus"
BUILD_DIR = ROOT / "build"
BUILD_DIR.mkdir(exist_ok=True)

# Curated blueprints get a stable, hand-picked deployed name. Anything else in
# blueprints/ — typically produced by scripts/generate_blueprint.py — is named
# automatically as BdaLib-Gen-<slug>.
CURATED_NAMES = {
    "invoice": "BdaLib-Invoice",
    "bill_of_lading": "BdaLib-BillOfLading",
    "k1_1065": "BdaLib-K1-1065",
    "lease_abstract": "BdaLib-LeaseAbstract",
    "msa": "BdaLib-MSA",
}


def _discover_blueprint_names() -> dict[str, str]:
    out = dict(CURATED_NAMES)
    for path in sorted(BLUEPRINT_DIR.glob("*.json")):
        slug = path.stem
        if slug in out:
            continue
        out[slug] = f"BdaLib-Gen-{slug.replace('_', '-')}"
    return out


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def _ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"[s3] bucket exists: {bucket}")
        return
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise

    print(f"[s3] creating bucket: {bucket}")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def _upload_corpus(s3, bucket: str) -> None:
    for doc in sorted(p for ext in ("pdf", "png", "jpg", "jpeg")
                      for p in CORPUS_DIR.glob(f"*/*.{ext}")):
        key = f"corpus/{doc.parent.name}/{doc.name}"
        ct = {"pdf": "application/pdf", "png": "image/png",
              "jpg": "image/jpeg", "jpeg": "image/jpeg"}[doc.suffix.lstrip(".").lower()]
        print(f"[s3] put s3://{bucket}/{key}")
        s3.upload_file(str(doc), bucket, key, ExtraArgs={"ContentType": ct})


def _existing_blueprints(bda) -> dict[str, str]:
    """Return name -> arn for blueprints owned by this account."""
    out: dict[str, str] = {}
    paginator = bda.get_paginator("list_blueprints")
    for page in paginator.paginate(resourceOwner="ACCOUNT"):
        for bp in page.get("blueprints", []):
            out[bp["blueprintName"]] = bp["blueprintArn"]
    return out


def _put_blueprint(bda, name: str, schema_text: str, existing: dict[str, str]) -> str:
    if name in existing:
        arn = existing[name]
        print(f"[bda] update {name}")
        bda.update_blueprint(blueprintArn=arn, schema=schema_text, blueprintStage="LIVE")
        return arn

    print(f"[bda] create {name}")
    resp = bda.create_blueprint(
        blueprintName=name,
        type="DOCUMENT",
        blueprintStage="LIVE",
        schema=schema_text,
    )
    return resp["blueprint"]["blueprintArn"]


def main() -> int:
    region = _region()
    account = _account_id()
    bucket = f"bda-blueprint-library-{account}-{region}"

    print(f"[deploy] account={account} region={region} bucket={bucket}")

    s3 = boto3.client("s3", region_name=region)
    bda = boto3.client("bedrock-data-automation", region_name=region)

    _ensure_bucket(s3, bucket, region)
    _upload_corpus(s3, bucket)

    existing = _existing_blueprints(bda)

    arns: dict[str, str] = {}
    blueprint_names = _discover_blueprint_names()
    for slug, deployed_name in blueprint_names.items():
        schema_path = BLUEPRINT_DIR / f"{slug}.json"
        # send the schema as a JSON string (CreateBlueprint requires a string)
        schema_text = json.dumps(json.loads(schema_path.read_text()))
        arns[slug] = _put_blueprint(bda, deployed_name, schema_text, existing)

    profile_arn = (
        f"arn:aws:bedrock:{region}:{account}:data-automation-profile/us.data-automation-v1"
    )

    out = {
        "region": region,
        "account": account,
        "bucket": bucket,
        "blueprints": arns,
        "data_automation_profile_arn": profile_arn,
    }
    (BUILD_DIR / "blueprint-arns.json").write_text(json.dumps(out, indent=2))
    print(f"[deploy] wrote {BUILD_DIR / 'blueprint-arns.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
