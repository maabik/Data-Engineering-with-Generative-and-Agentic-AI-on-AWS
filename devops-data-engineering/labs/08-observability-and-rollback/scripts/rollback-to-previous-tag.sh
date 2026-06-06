#!/usr/bin/env bash
# Roll back the named environment to the previous successful release.
#
# Usage:
#   ./rollback-to-previous-tag.sh prod
#   ./rollback-to-previous-tag.sh staging
#
# The previous tag is the second-most-recent annotated tag matching v*.
# Schema migrations are not rolled back automatically — see schema-migrations/README.md.
set -euo pipefail

env="${1:?environment (staging|prod)}"

prev_tag=$(git tag --list 'v*' --sort=-creatordate | sed -n '2p')
if [[ -z "$prev_tag" ]]; then
  echo "ERROR: no previous tag found." >&2
  exit 1
fi

echo "Rolling ${env} back to ${prev_tag}"

git checkout "$prev_tag"
cd "reference-pipeline/terraform/envs/${env}"
terraform init
terraform apply -auto-approve

# Tag the rollback in CloudWatch metrics for incident timeline.
aws cloudwatch put-metric-data \
  --namespace DataPipeline/Reference \
  --metric-data "MetricName=Rollback,Value=1,Unit=Count,Dimensions=[{Name=Environment,Value=${env}},{Name=DeployId,Value=${prev_tag}}]"

echo "Rolled back ${env} to ${prev_tag}. Verify smoke + DQ next."
