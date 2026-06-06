#!/bin/bash
# Deploy the text-to-SQL CloudFormation stack and upload the Lambda code.
#
# Required env vars:
#   MASTER_PASSWORD       Redshift master user password
#   BEDROCK_AGENT_ID      Bedrock TextToSQL agent ID
#   BEDROCK_AGENT_ALIAS_ID  Bedrock TextToSQL agent alias ID
#
# Optional:
#   AWS_REGION            (default: us-east-1)

set -euo pipefail

: "${MASTER_PASSWORD:?MASTER_PASSWORD env var is required}"
: "${BEDROCK_AGENT_ID:?BEDROCK_AGENT_ID env var is required}"
: "${BEDROCK_AGENT_ALIAS_ID:?BEDROCK_AGENT_ALIAS_ID env var is required}"

python3 scripts/deploy_text_to_sql.py "$@"
