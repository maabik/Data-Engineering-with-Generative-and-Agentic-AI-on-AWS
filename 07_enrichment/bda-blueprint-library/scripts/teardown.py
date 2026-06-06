#!/usr/bin/env python3
"""Tear down everything deploy.py created.

Reads build/blueprint-arns.json, deletes each blueprint, then empties and
deletes the S3 bucket.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "blueprint-arns.json"


def _empty_bucket(s3, bucket: str) -> None:
    paginator = s3.get_paginator("list_object_versions")
    to_delete: list[dict] = []
    try:
        for page in paginator.paginate(Bucket=bucket):
            for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
                to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
                if len(to_delete) == 1000:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                    to_delete = []
        if to_delete:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchBucket":
            raise


def main() -> int:
    if not BUILD.exists():
        print(f"[teardown] nothing to do — {BUILD} missing")
        return 0
    cfg = json.loads(BUILD.read_text())
    region = cfg["region"]
    bucket = cfg["bucket"]
    arns = cfg["blueprints"]

    bda = boto3.client("bedrock-data-automation", region_name=region)
    for slug, arn in arns.items():
        try:
            print(f"[bda] delete {slug}: {arn}")
            bda.delete_blueprint(blueprintArn=arn)
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("ResourceNotFoundException", "NotFoundException"):
                raise

    s3 = boto3.client("s3", region_name=region)
    print(f"[s3] empty + delete {bucket}")
    _empty_bucket(s3, bucket)
    try:
        s3.delete_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchBucket":
            raise

    BUILD.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
