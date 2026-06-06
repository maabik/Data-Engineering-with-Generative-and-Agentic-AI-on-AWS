#!/bin/bash
# Deployment script for Redshift Sales Data Warehouse with Text-to-SQL API.
#
# Usage:
#   MASTER_PASSWORD='your-strong-password' ./deploy.sh
#
# If MASTER_PASSWORD is not set, the script generates a random password and
# stores it in AWS Secrets Manager as part of the text-to-sql-api stack
# (secret name: redshift-text-to-sql-credentials).

set -euo pipefail

echo "=== Deploying Redshift Sales Data Warehouse with Text-to-SQL API ==="

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 is required but not installed." >&2
    exit 1
fi

echo "Installing Python dependencies..."
python3 -m pip install -q -r requirements.txt

echo "Starting deployment..."
python3 scripts/deploy_all.py "$@"

echo ""
echo "=== Deployment Complete ==="
echo "Redshift Sales Data Warehouse with Text-to-SQL API deployed."
echo "Frontend API endpoint will be displayed by deploy_all.py output."
