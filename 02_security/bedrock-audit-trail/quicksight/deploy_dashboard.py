"""Deploy or update the QuickSight dashboard from the JSON template.

Usage:
    python quicksight/deploy_dashboard.py \
        --account-id 123456789012 \
        --dataset-arn arn:aws:quicksight:us-east-1:123456789012:dataset/bedrock-invocations \
        --principal arn:aws:quicksight:us-east-1:123456789012:user/default/admin

The script creates (or updates) the dataset-backed analysis and publishes it
as a versioned dashboard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

TEMPLATE_FILE = Path(__file__).parent / "dashboard_template.json"
DASHBOARD_ID = "bedrock-audit-trail-dashboard"
ANALYSIS_ID = "bedrock-audit-trail-analysis"


def _get_client(region: str):
    return boto3.client("quicksight", region_name=region)


def deploy(account_id: str, dataset_arn: str, principal: str, region: str):
    qs = _get_client(region)
    template = json.loads(TEMPLATE_FILE.read_text())

    permissions = [
        {
            "Principal": principal,
            "Actions": [
                "quicksight:DescribeDashboard",
                "quicksight:ListDashboardVersions",
                "quicksight:UpdateDashboardPermissions",
                "quicksight:QueryDashboard",
                "quicksight:UpdateDashboard",
                "quicksight:DeleteDashboard",
                "quicksight:DescribeDashboardPermissions",
                "quicksight:PublishDashboard",
            ],
        }
    ]

    data_set_references = [
        {
            "DataSetPlaceholder": "bedrock_invocations",
            "DataSetArn": dataset_arn,
        }
    ]

    # Check if dashboard exists
    try:
        qs.describe_dashboard(AwsAccountId=account_id, DashboardId=DASHBOARD_ID)
        exists = True
    except qs.exceptions.ResourceNotFoundException:
        exists = False

    if exists:
        resp = qs.update_dashboard(
            AwsAccountId=account_id,
            DashboardId=DASHBOARD_ID,
            Name=template["Name"],
            SourceEntity={
                "SourceTemplate": {
                    "Arn": f"arn:aws:quicksight:{region}:{account_id}:template/{DASHBOARD_ID}",
                    "DataSetReferences": data_set_references,
                }
            },
        )
        version = resp["VersionArn"].split("/")[-1]
        qs.update_dashboard_published_version(
            AwsAccountId=account_id,
            DashboardId=DASHBOARD_ID,
            VersionNumber=int(version),
        )
        print(f"Updated dashboard {DASHBOARD_ID} to version {version}")
    else:
        qs.create_dashboard(
            AwsAccountId=account_id,
            DashboardId=DASHBOARD_ID,
            Name=template["Name"],
            Permissions=permissions,
            SourceEntity={
                "SourceTemplate": {
                    "Arn": f"arn:aws:quicksight:{region}:{account_id}:template/{DASHBOARD_ID}",
                    "DataSetReferences": data_set_references,
                }
            },
            DashboardPublishOptions={
                "AdHocFilteringOption": {"AvailabilityStatus": "ENABLED"},
                "ExportToCSVOption": {"AvailabilityStatus": "ENABLED"},
                "SheetControlsOption": {"VisibilityState": "EXPANDED"},
            },
        )
        print(f"Created dashboard {DASHBOARD_ID}")


def main():
    p = argparse.ArgumentParser(description="Deploy Bedrock Audit Trail QuickSight dashboard")
    p.add_argument("--account-id", required=True)
    p.add_argument("--dataset-arn", required=True)
    p.add_argument("--principal", required=True, help="QuickSight user/group ARN for permissions")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()
    deploy(args.account_id, args.dataset_arn, args.principal, args.region)


if __name__ == "__main__":
    main()
