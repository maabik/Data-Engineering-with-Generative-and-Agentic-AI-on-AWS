#!/usr/bin/env bash
# Empty an S3 bucket including all object versions, so a Terraform
# destroy can finish. Use when destroy stalls on a non-empty bucket
# (Iceberg metadata, Athena results, Glue script versions).
#
# Usage: ./empty-bucket.sh <bucket-name>
set -euo pipefail

bucket="${1:?bucket name}"

echo "Emptying s3://${bucket}"

aws s3 rm "s3://${bucket}" --recursive --quiet || true

# Delete all object versions and delete markers (versioned buckets only).
aws s3api list-object-versions --bucket "$bucket" \
  --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
  --output json 2>/dev/null \
  | jq -c '.Objects // [] | _nwise(500)' \
  | while read -r batch; do
      [[ "$batch" == "[]" ]] && continue
      aws s3api delete-objects --bucket "$bucket" \
        --delete "{\"Objects\": $batch, \"Quiet\": true}" >/dev/null
    done

aws s3api list-object-versions --bucket "$bucket" \
  --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
  --output json 2>/dev/null \
  | jq -c '.Objects // [] | _nwise(500)' \
  | while read -r batch; do
      [[ "$batch" == "[]" ]] && continue
      aws s3api delete-objects --bucket "$bucket" \
        --delete "{\"Objects\": $batch, \"Quiet\": true}" >/dev/null
    done

echo "Done."
