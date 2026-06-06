#!/usr/bin/env python3
"""Provision the S3 Vectors bucket + index after the CDK deploy.

CDK doesn't yet have an L2 construct for S3 Vectors, so the vector
bucket is created via the ``s3vectors`` API. Re-runnable: existing
buckets and indexes are reused.
"""

from __future__ import annotations

import os
import sys

import boto3


def main() -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    bucket = ssm.get_parameter(Name="/kg/vectors_bucket")["Parameter"]["Value"]
    index = os.environ.get("KG_S3_VECTORS_INDEX", "kg-entities")
    dimension = int(os.environ.get("KG_EMBEDDING_DIMENSIONS", "1024"))

    s3v = boto3.client("s3vectors", region_name=region)

    print(f"📦 ensuring vector bucket {bucket!r}...")
    try:
        s3v.create_vector_bucket(vectorBucketName=bucket)
        print("  ✅ created")
    except s3v.exceptions.ConflictException:
        print("  ✅ already exists")

    print(f"📐 ensuring index {index!r} (dim={dimension})...")
    try:
        s3v.create_index(
            vectorBucketName=bucket,
            indexName=index,
            dataType="float32",
            dimension=dimension,
            distanceMetric="cosine",
        )
        print("  ✅ created")
    except s3v.exceptions.ConflictException:
        print("  ✅ already exists")

    # Quick verification
    info = s3v.get_index(vectorBucketName=bucket, indexName=index)
    md = info.get("index", {})
    print(f"  arn:       {md.get('indexArn')}")
    print(f"  dim:       {md.get('dimension')}")
    print(f"  metric:    {md.get('distanceMetric')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
