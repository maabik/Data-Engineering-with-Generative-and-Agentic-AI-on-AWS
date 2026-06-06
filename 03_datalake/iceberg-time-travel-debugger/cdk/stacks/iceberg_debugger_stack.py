"""CDK stack: Iceberg Time-Travel Debugger

Provisions the AWS infrastructure for the iceberg-time-travel-debugger CLI.

Resources:
  - S3 bucket          — Iceberg table data + Athena query results
  - Glue database      — hosts the Iceberg permits table
  - Athena workgroup   — Iceberg-enabled, output to results prefix
  - Seed Lambda        — creates the Iceberg table via Athena CTAS + seeds rows

The CLI (cli/debugger.py) runs locally against this infrastructure.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_athena as athena,
    aws_glue as glue,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct

DATABASE_NAME = "iceberg_debugger"


class IcebergDebuggerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── S3 bucket (table data + Athena results) ───────────────────────────
        data_bucket = s3.Bucket(
            self, "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── Glue database ─────────────────────────────────────────────────────
        glue.CfnDatabase(
            self, "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=DATABASE_NAME,
                description="Iceberg time-travel debugger demo database",
            ),
        )

        # ── Athena workgroup (Iceberg engine v3 required for FOR VERSION AS OF)
        athena.CfnWorkGroup(
            self, "IcebergWorkgroup",
            name="iceberg-debugger",
            description="Athena workgroup for Iceberg time-travel queries",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                engine_version=athena.CfnWorkGroup.EngineVersionProperty(
                    selected_engine_version="Athena engine version 3",
                ),
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=data_bucket.s3_url_for_object("athena-results/"),
                ),
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
            ),
        )

        # ── Seed Lambda role ──────────────────────────────────────────────────
        seed_role = iam.Role(
            self, "SeedLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )
        data_bucket.grant_read_write(seed_role)

        # Athena permissions for creating and querying Iceberg tables
        seed_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "athena:StartQueryExecution",
                "athena:GetQueryExecution",
                "athena:GetQueryResults",
                "athena:StopQueryExecution",
                "athena:ListQueryExecutions",
            ],
            resources=[
                f"arn:aws:athena:{self.region}:{self.account}:workgroup/iceberg-debugger",
            ],
        ))
        seed_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "glue:GetDatabase",
                "glue:GetTable",
                "glue:CreateTable",
                "glue:UpdateTable",
                "glue:GetPartitions",
                "glue:BatchCreatePartition",
            ],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/{DATABASE_NAME}",
                f"arn:aws:glue:{self.region}:{self.account}:table/{DATABASE_NAME}/*",
            ],
        ))

        # ── Seed Lambda ───────────────────────────────────────────────────────
        seed_fn = lambda_.Function(
            self, "SeedFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="seed_iceberg.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            role=seed_role,
            timeout=Duration.minutes(10),
            memory_size=256,
            environment={
                "ATHENA_DB": DATABASE_NAME,
                "ATHENA_WORKGROUP": "iceberg-debugger",
                "TABLE_BUCKET": data_bucket.bucket_name,
            },
        )

        # ── CLI IAM policy (for local use) ────────────────────────────────────
        # Attach this policy to the IAM principal running the CLI locally.
        cli_policy = iam.ManagedPolicy(
            self, "CliPolicy",
            managed_policy_name=f"IcebergDebuggerCliPolicy-{self.region}",
            description="Minimum permissions for iceberg-time-travel-debugger CLI",
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "athena:StartQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:ListQueryExecutions",
                    ],
                    resources=[
                        f"arn:aws:athena:{self.region}:{self.account}:workgroup/iceberg-debugger",
                        f"arn:aws:athena:{self.region}:{self.account}:workgroup/primary",
                        f"arn:aws:athena:{self.region}:{self.account}:workgroup/medallion",
                    ],
                ),
                iam.PolicyStatement(
                    actions=[
                        "glue:GetDatabase",
                        "glue:GetTable",
                        "glue:GetPartitions",
                    ],
                    resources=[
                        f"arn:aws:glue:{self.region}:{self.account}:catalog",
                        f"arn:aws:glue:{self.region}:{self.account}:database/{DATABASE_NAME}",
                        f"arn:aws:glue:{self.region}:{self.account}:table/{DATABASE_NAME}/*",
                    ],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                    resources=[
                        data_bucket.bucket_arn,
                        data_bucket.arn_for_objects("*"),
                    ],
                ),
            ],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "DataBucketName",
                      value=data_bucket.bucket_name,
                      description="S3 bucket for Iceberg table data and Athena results")
        cdk.CfnOutput(self, "GlueDatabaseName",
                      value=DATABASE_NAME,
                      description="Glue database name")
        cdk.CfnOutput(self, "AthenaWorkgroup",
                      value="iceberg-debugger",
                      description="Athena workgroup (Iceberg engine v3)")
        cdk.CfnOutput(self, "SeedFunctionName",
                      value=seed_fn.function_name,
                      description="Invoke to create and seed the Iceberg permits table")
        cdk.CfnOutput(self, "CliPolicyArn",
                      value=cli_policy.managed_policy_arn,
                      description="Attach this policy to your IAM user/role to run the CLI")
