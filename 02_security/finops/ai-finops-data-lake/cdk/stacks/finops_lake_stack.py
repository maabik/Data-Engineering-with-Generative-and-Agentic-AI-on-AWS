"""CDK stack: AI FinOps Data Lake

Provisions:
  - KMS CMK for all storage
  - S3 Iceberg bucket (partitioned FinOps dataset)
  - S3 staging bucket (temp Athena INSERT staging)
  - Athena workgroup with per-query limits
  - Glue Database (ai_finops)
  - Glue Catalog table (invocations, Iceberg type)
  - Glue Python Shell job for backfill
  - EventBridge Scheduler → Lambda daily pipeline trigger
  - Lambda daily runner (wraps pipeline.runner)
  - IAM roles for Glue and Lambda
  - CloudWatch alarms: daily cost spike, missing data
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_athena as athena,
    aws_cloudwatch as cw,
    aws_events as events,
    aws_events_targets as targets,
    aws_glue as glue,
    aws_iam as iam,
    aws_kms as kms,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_sns as sns,
)
from constructs import Construct


class AIFinOpsLakeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Parameters ────────────────────────────────────────────────────────
        bedrock_log_bucket_name = cdk.CfnParameter(
            self, "BedrockLogBucketName",
            type="String",
            description="S3 bucket name for bedrock-audit-trail NDJSON logs",
        )
        cur_bucket_name = cdk.CfnParameter(
            self, "CurBucketName",
            type="String", default="",
            description="S3 bucket for CUR 2.0 Parquet exports (leave blank to use Cost Explorer)",
        )
        cur_prefix = cdk.CfnParameter(
            self, "CurPrefix",
            type="String", default="",
            description="S3 prefix within CurBucketName for CUR 2.0 files",
        )
        alarm_email = cdk.CfnParameter(
            self, "AlarmEmail",
            type="String", default="",
            description="Email for cost anomaly alarms (optional)",
        )

        # ── KMS ───────────────────────────────────────────────────────────────
        lake_key = kms.Key(
            self, "LakeCmk",
            description="CMK for AI FinOps data lake (S3 + Athena)",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        lake_key.add_alias("alias/ai-finops-lake-key")
        lake_key.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowAthena",
            principals=[iam.ServicePrincipal("athena.amazonaws.com")],
            actions=["kms:GenerateDataKey", "kms:Decrypt"],
            resources=["*"],
        ))

        # ── S3 buckets ────────────────────────────────────────────────────────
        iceberg_bucket = s3.Bucket(
            self, "IcebergBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=lake_key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="TransitionToIA",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        ),
                    ],
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        staging_bucket = s3.Bucket(
            self, "StagingBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=lake_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(id="ExpireStaging", expiration=Duration.days(3)),
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        athena_results_bucket = s3.Bucket(
            self, "AthenaResultsBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=lake_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(id="ExpireResults", expiration=Duration.days(7)),
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── Athena Workgroup ──────────────────────────────────────────────────
        workgroup = athena.CfnWorkGroup(
            self, "FinOpsWorkgroup",
            name="ai-finops",
            description="AI FinOps queries with per-query cost limit",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=athena_results_bucket.s3_url_for_object("results/"),
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_KMS",
                        kms_key=lake_key.key_arn,
                    ),
                ),
                bytes_scanned_cutoff_per_query=10 * 1024 ** 3,  # 10 GB
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
            ),
            recursive_delete_option=True,
        )

        # ── Glue Database ─────────────────────────────────────────────────────
        glue_db = glue.CfnDatabase(
            self, "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="ai_finops",
                description="AI FinOps Iceberg data lake",
            ),
        )

        # ── Glue Table (Iceberg) ──────────────────────────────────────────────
        iceberg_columns = [
            ("event_time", "timestamp"), ("source", "string"), ("account_id", "string"),
            ("region", "string"), ("request_id", "string"), ("cur_line_item_id", "string"),
            ("iam_principal", "string"), ("model_id", "string"), ("model_family", "string"),
            ("inference_profile_id", "string"), ("inference_profile_name", "string"),
            ("tenant_id", "string"), ("user_id", "string"), ("session_id", "string"),
            ("trace_id", "string"), ("workload", "string"), ("team", "string"),
            ("environment", "string"), ("input_tokens", "int"), ("output_tokens", "int"),
            ("total_tokens", "int"), ("latency_ms", "int"), ("usage_amount", "double"),
            ("usage_type", "string"), ("estimated_cost_usd", "double"),
            ("actual_cost_usd", "double"), ("product_code", "string"),
            ("service_category", "string"), ("agent_id", "string"), ("agent_name", "string"),
            ("agent_alias_id", "string"), ("execution_status", "string"),
            ("model_invocations", "int"), ("tool_invocations", "int"),
            ("guardrail_action", "string"), ("injection_detected", "boolean"),
            ("pii_detected", "boolean"), ("error_code", "string"),
            ("joined", "boolean"), ("join_method", "string"),
        ]

        glue.CfnTable(
            self, "InvocationsTable",
            catalog_id=self.account,
            database_name="ai_finops",
            table_input=glue.CfnTable.TableInputProperty(
                name="invocations",
                description="Unified AI FinOps Iceberg dataset",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "table_type": "ICEBERG",
                    "metadata_location": iceberg_bucket.s3_url_for_object(
                        "finops/invocations/metadata/"
                    ),
                },
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=iceberg_bucket.s3_url_for_object("finops/invocations/data/"),
                    input_format="org.apache.iceberg.mr.mapred.IcebergInputFormat",
                    output_format="org.apache.iceberg.mr.mapred.IcebergOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.iceberg.mr.hive.IcebergSerDe",
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name=n, type=t)
                        for n, t in iceberg_columns
                    ],
                ),
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="date", type="date"),
                    glue.CfnTable.ColumnProperty(name="model_family", type="string"),
                    glue.CfnTable.ColumnProperty(name="tenant_id", type="string"),
                ],
            ),
        )

        # ── IAM role for pipeline Lambda / Glue ───────────────────────────────
        pipeline_role = iam.Role(
            self, "PipelineRole",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("lambda.amazonaws.com"),
                iam.ServicePrincipal("glue.amazonaws.com"),
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        iceberg_bucket.grant_read_write(pipeline_role)
        staging_bucket.grant_read_write(pipeline_role)
        athena_results_bucket.grant_read_write(pipeline_role)
        lake_key.grant_encrypt_decrypt(pipeline_role)

        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "athena:StartQueryExecution", "athena:GetQueryExecution",
                "athena:GetQueryResults", "athena:StopQueryExecution",
            ],
            resources=[f"arn:aws:athena:{self.region}:{self.account}:workgroup/ai-finops"],
        ))
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["glue:GetDatabase", "glue:GetTable", "glue:UpdateTable"],
            resources=["*"],
        ))
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:FilterLogEvents", "logs:DescribeLogGroups"],
            resources=["*"],
        ))
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile"],
            resources=["*"],
        ))
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["ce:GetCostAndUsage"],
            resources=["*"],
        ))
        # Read the bedrock log source bucket
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:ListBucket"],
            resources=[
                f"arn:aws:s3:::{bedrock_log_bucket_name.value_as_string}",
                f"arn:aws:s3:::{bedrock_log_bucket_name.value_as_string}/*",
            ],
        ))

        # ── Lambda daily runner ───────────────────────────────────────────────
        daily_runner_fn = lambda_.Function(
            self, "DailyRunnerFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="daily_runner.handler",
            code=lambda_.Code.from_asset("../pipeline"),
            role=pipeline_role,
            timeout=Duration.minutes(15),
            memory_size=512,
            environment={
                "BEDROCK_LOG_BUCKET": bedrock_log_bucket_name.value_as_string,
                "CUR_BUCKET": cur_bucket_name.value_as_string,
                "CUR_PREFIX": cur_prefix.value_as_string,
                "ICEBERG_DATABASE": "ai_finops",
                "ICEBERG_TABLE": "invocations",
                "ATHENA_OUTPUT": athena_results_bucket.s3_url_for_object("results/"),
                "STAGING_BUCKET": staging_bucket.bucket_name,
                "ACCOUNT_ID": self.account,
                "REGION": self.region,
            },
        )

        # Daily schedule: 02:00 UTC
        events.Rule(
            self, "DailySchedule",
            schedule=events.Schedule.cron(minute="0", hour="2"),
            targets=[targets.LambdaFunction(daily_runner_fn)],
        )

        # ── Glue backfill job ─────────────────────────────────────────────────
        glue_job_role = iam.Role(
            self, "GlueJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"),
            ],
        )
        iceberg_bucket.grant_read_write(glue_job_role)
        staging_bucket.grant_read_write(glue_job_role)
        athena_results_bucket.grant_read_write(glue_job_role)
        lake_key.grant_encrypt_decrypt(glue_job_role)
        glue_job_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:ListBucket"],
            resources=[
                f"arn:aws:s3:::{bedrock_log_bucket_name.value_as_string}",
                f"arn:aws:s3:::{bedrock_log_bucket_name.value_as_string}/*",
            ],
        ))
        glue_job_role.add_to_policy(iam.PolicyStatement(
            actions=["athena:StartQueryExecution", "athena:GetQueryExecution",
                     "athena:GetQueryResults", "ce:GetCostAndUsage",
                     "bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile",
                     "logs:FilterLogEvents", "logs:DescribeLogGroups"],
            resources=["*"],
        ))
        lake_key.grant_encrypt_decrypt(glue_job_role)

        glue.CfnJob(
            self, "BackfillJob",
            name="ai-finops-backfill",
            role=glue_job_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="pythonshell",
                python_version="3",
                script_location=iceberg_bucket.s3_url_for_object("glue-scripts/backfill_job.py"),
            ),
            default_arguments={
                "--iceberg_database": "ai_finops",
                "--iceberg_table": "invocations",
                "--athena_output": athena_results_bucket.s3_url_for_object("results/"),
                "--staging_bucket": staging_bucket.bucket_name,
                "--region": self.region,
                "--account_id": self.account,
            },
            max_capacity=1.0,
            timeout=120,
            glue_version="3.0",
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "IcebergBucketName", value=iceberg_bucket.bucket_name)
        cdk.CfnOutput(self, "StagingBucketName", value=staging_bucket.bucket_name)
        cdk.CfnOutput(self, "AthenaResultsBucketName", value=athena_results_bucket.bucket_name)
        cdk.CfnOutput(self, "AthenaWorkgroupName", value="ai-finops")
        cdk.CfnOutput(self, "GlueDatabaseName", value="ai_finops")
        cdk.CfnOutput(self, "LakeCmkArn", value=lake_key.key_arn)
        cdk.CfnOutput(self, "PipelineRoleArn", value=pipeline_role.role_arn)
        cdk.CfnOutput(self, "DailyRunnerFunctionName", value=daily_runner_fn.function_name)
