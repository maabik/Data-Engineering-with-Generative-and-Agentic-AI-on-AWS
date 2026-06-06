"""CDK stack: Bitcoin Prices Data Catalog

Provisions:
  - S3 bucket for ingested Bitcoin price data (time-partitioned layout)
  - Glue Database (bitcoin_prices)
  - Glue IAM role with AWSGlueServiceRole + scoped S3 access
  - Glue Crawler with partition indexing targeting year=/month=/day=/hour= prefixes
  - Lambda: ingest.py — hourly fetch + time-partitioned S3 write
  - Lambda: annotate.py — post-crawl Bedrock annotation, drift detection, metadata tagging
  - SNS topic for schema drift alerts
  - EventBridge rule: hourly ingest trigger
  - EventBridge rule: crawler SUCCEEDED → annotate Lambda
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_events as events,
    aws_events_targets as targets,
    aws_glue as glue,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_sns as sns,
)
from constructs import Construct


class DataCatalogStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Parameters ────────────────────────────────────────────────────────
        api_url = cdk.CfnParameter(
            self, "ApiUrl",
            type="String",
            default="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            description="CoinGecko API URL for Bitcoin price data",
        )
        alert_email = cdk.CfnParameter(
            self, "AlertEmail",
            type="String",
            default="",
            description="Email address for schema drift alerts (optional)",
        )
        bedrock_model_id = cdk.CfnParameter(
            self, "BedrockModelId",
            type="String",
            default="us.anthropic.claude-sonnet-4-6",
            description="Bedrock model ID for schema annotation",
        )
        metadata_owner = cdk.CfnParameter(
            self, "MetadataOwner",
            type="String",
            default="data-engineering",
            description="Table owner tag value",
        )
        metadata_sla_tier = cdk.CfnParameter(
            self, "MetadataSLATier",
            type="String",
            default="bronze",
            description="SLA tier tag value (bronze/silver/gold)",
        )

        # ── S3 bucket ─────────────────────────────────────────────────────────
        data_bucket = s3.Bucket(
            self, "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="TransitionToIA",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        )
                    ],
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ── SNS drift alert topic ─────────────────────────────────────────────
        drift_topic = sns.Topic(
            self, "SchemaDriftTopic",
            display_name="Bitcoin Prices Schema Drift Alerts",
        )
        # Email subscription is created only when AlertEmail is provided.
        # CfnParameter.value_as_string is always a token at synth time, so we
        # use a CfnCondition to conditionally attach it.
        email_provided = cdk.CfnCondition(
            self, "EmailProvided",
            expression=cdk.Fn.condition_not(
                cdk.Fn.condition_equals(alert_email.value_as_string, "")
            ),
        )
        email_sub = sns.CfnSubscription(
            self, "AlertEmailSubscription",
            topic_arn=drift_topic.topic_arn,
            protocol="email",
            endpoint=alert_email.value_as_string,
        )
        email_sub.cfn_options.condition = email_provided

        # ── Glue Database ─────────────────────────────────────────────────────
        glue.CfnDatabase(
            self, "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="bitcoin_prices",
                description="Bitcoin price data lake catalog",
            ),
        )

        # ── Glue IAM Role ─────────────────────────────────────────────────────
        glue_role = iam.Role(
            self, "GlueCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"),
            ],
        )
        data_bucket.grant_read(glue_role)

        # ── Glue Crawler (partition-aware) ────────────────────────────────────
        glue.CfnCrawler(
            self, "BitcoinPricesCrawler",
            name="bitcoin-prices-crawler",
            role=glue_role.role_arn,
            database_name="bitcoin_prices",
            description="Crawler for time-partitioned Bitcoin prices JSON data in S3",
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[
                    glue.CfnCrawler.S3TargetProperty(
                        path=data_bucket.s3_url_for_object("bitcoin_prices/"),
                    )
                ]
            ),
            # CRAWL_NEW_FOLDERS_ONLY mandates LOG for both schema change behaviors.
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="LOG",
                delete_behavior="LOG",
            ),
            recrawl_policy=glue.CfnCrawler.RecrawlPolicyProperty(
                recrawl_behavior="CRAWL_NEW_FOLDERS_ONLY",
            ),
            configuration=cdk.Fn.to_json_string({
                "Version": 1.0,
                "CrawlerOutput": {
                    "Partitions": {"AddOrUpdateBehavior": "InheritFromTable"},
                    "Tables": {"AddOrUpdateBehavior": "MergeNewColumns"},
                },
                "Grouping": {"TableGroupingPolicy": "CombineCompatibleSchemas"},
            }),
        )

        # ── Ingest Lambda role ────────────────────────────────────────────────
        ingest_role = iam.Role(
            self, "IngestLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        data_bucket.grant_put(ingest_role)

        # ── Ingest Lambda ─────────────────────────────────────────────────────
        ingest_fn = lambda_.Function(
            self, "IngestFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="ingest.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            role=ingest_role,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
                "API_URL": api_url.value_as_string,
            },
        )

        # ── Annotate Lambda role ──────────────────────────────────────────────
        annotate_role = iam.Role(
            self, "AnnotateLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        # Schema snapshots live in the same data bucket under schema-snapshots/
        data_bucket.grant_read_write(annotate_role)
        drift_topic.grant_publish(annotate_role)

        annotate_role.add_to_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:UpdateTable"],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/bitcoin_prices",
                f"arn:aws:glue:{self.region}:{self.account}:table/bitcoin_prices/*",
            ],
        ))
        annotate_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-6",
            ],
        ))

        # ── Annotate Lambda ───────────────────────────────────────────────────
        annotate_fn = lambda_.Function(
            self, "AnnotateFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="annotate.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            role=annotate_role,
            timeout=Duration.minutes(5),
            memory_size=256,
            environment={
                "BUCKET_NAME": data_bucket.bucket_name,
                "DATABASE_NAME": "bitcoin_prices",
                "TABLE_NAME": "bitcoin_prices",
                "DRIFT_TOPIC_ARN": drift_topic.topic_arn,
                "BEDROCK_MODEL_ID": bedrock_model_id.value_as_string,
                "METADATA_OWNER": metadata_owner.value_as_string,
                "METADATA_CLASSIFICATION": "public",
                "METADATA_SOURCE_SYSTEM": "coindesk-api",
                "METADATA_SLA_TIER": metadata_sla_tier.value_as_string,
            },
        )

        # ── EventBridge: hourly ingest ────────────────────────────────────────
        events.Rule(
            self, "HourlyIngestSchedule",
            schedule=events.Schedule.rate(Duration.hours(1)),
            targets=[targets.LambdaFunction(ingest_fn)],
        )

        # ── EventBridge: crawler SUCCEEDED → annotate ─────────────────────────
        events.Rule(
            self, "CrawlerSucceededRule",
            event_pattern=events.EventPattern(
                source=["aws.glue"],
                detail_type=["Glue Crawler State Change"],
                detail={
                    "crawlerName": ["bitcoin-prices-crawler"],
                    "state": ["Succeeded"],
                },
            ),
            targets=[targets.LambdaFunction(annotate_fn)],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        cdk.CfnOutput(self, "GlueDatabaseName", value="bitcoin_prices")
        cdk.CfnOutput(self, "GlueCrawlerName", value="bitcoin-prices-crawler")
        cdk.CfnOutput(self, "IngestFunctionName", value=ingest_fn.function_name)
        cdk.CfnOutput(self, "AnnotateFunctionName", value=annotate_fn.function_name)
        cdk.CfnOutput(self, "DriftTopicArn", value=drift_topic.topic_arn)
