"""CDK stack: Progressive Medallion Starter Kit

Provisions the full 7-stage progressive medallion data lake architecture
as defined in "Data Engineering with AWS and Generative AI" Chapter 3.

Stages follow the book's naming convention [provider]-[product]-[stage]:
  Stage 0 — Landing Zone      (raw CDC ingest, PII risk zone)
  Stage 1 — Audit             (S3 Object Lock WORM, immutable compliance copy)
  Stage 2 — Raw               (flat-file text, Glue-cataloged, troubleshooting)
  Stage 3 — Optimized         (Parquet, light transforms, Athena ad-hoc)
  Stage 4 — Conform           (validated, quality-checked, Deequ-ready)
  Stage 5 — Unified Analytics (integrated, denormalized, aggregations)
  Stage 6 — Published         (highest quality, business reporting)

Glue database naming: [provider]-[product]-[stage]
  e.g. chicago-buildings-raw

Bucket naming is controlled by the `bucket_naming` CDK context key.
Pass it at deploy time with --context bucket_naming=<convention>.

  Convention          Example bucket name
  ──────────────────────────────────────────────────────────────────
  stage-only          datalake-raw-123456789012-us-west-2         (default)
  org-env             myorg-prod-raw-123456789012-us-west-2
  provider-product    chicago-buildings-raw-123456789012-us-west-2
  cdk-generated       <random CDK hash suffix>

For org-env, also pass --context org=<name> --context env=<name>.

Resources provisioned:
  - 7 S3 buckets (Audit gets Object Lock / WORM)
  - 5 Glue databases (Raw → Published)
  - 5 Glue crawlers (one per cataloged stage)
  - Lake Formation: register S3 locations, LF-Tag taxonomy, tag assignments
  - Sample CDC ingest Lambda (EventBridge-triggered every 15 min)
  - Athena workgroup with result output bucket
  - SNS topic for pipeline failure alerts
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_athena as athena,
    aws_events as events,
    aws_events_targets as targets,
    aws_glue as glue,
    aws_iam as iam,
    aws_lakeformation as lakeformation,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_sns as sns,
)
from constructs import Construct

PROVIDER = "chicago"
PRODUCT = "buildings"

# Stage slugs — match the book's stage names (hyphenated for S3/Glue compatibility)
ALL_STAGES = ["landing-zone", "audit", "raw", "optimized", "conform", "unified-analytics", "published"]

# Stages that get Glue databases and crawlers (Landing Zone + Audit are pre-catalog)
CATALOGED_STAGES = ["raw", "optimized", "conform", "unified-analytics", "published"]

# CDK logical ID safe versions (no hyphens, no spaces)
STAGE_LOGICAL = {
    "landing-zone":       "LandingZone",
    "audit":              "Audit",
    "raw":                "Raw",
    "optimized":          "Optimized",
    "conform":            "Conform",
    "unified-analytics":  "UnifiedAnalytics",
    "published":          "Published",
}

# LF-Tag values for medallion_stage
LF_STAGE_VALUES = ["raw", "optimized", "conform", "unified-analytics", "published"]

# Supported naming conventions
NAMING_CONVENTIONS = ("stage-only", "org-env", "provider-product", "cdk-generated")


def _bucket_name(convention: str, stage: str, account: str, region: str,
                 org: str = "myorg", env: str = "prod",
                 suffix: str = "") -> str | None:
    """Return a bucket name for the given convention, or None for cdk-generated."""
    slug = f"-{suffix}" if suffix else ""
    if convention == "stage-only":
        return f"datalake-{stage}{slug}-{account}-{region}"
    if convention == "org-env":
        return f"{org}-{env}-{stage}{slug}-{account}-{region}"
    if convention == "provider-product":
        return f"{PROVIDER}-{PRODUCT}-{stage}{slug}-{account}-{region}"
    # cdk-generated: let CDK assign a random name
    return None


class MedallionStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Bucket naming convention (CDK context) ────────────────────────────
        convention = self.node.try_get_context("bucket_naming") or "stage-only"
        if convention not in NAMING_CONVENTIONS:
            raise ValueError(
                f"Invalid bucket_naming '{convention}'. "
                f"Choose one of: {', '.join(NAMING_CONVENTIONS)}"
            )
        org = self.node.try_get_context("org") or "myorg"
        env = self.node.try_get_context("env") or "prod"

        # ── Parameters ────────────────────────────────────────────────────────
        alert_email = cdk.CfnParameter(
            self, "AlertEmail",
            type="String",
            default="",
            description="Email for pipeline failure alerts (optional)",
        )
        source_table = cdk.CfnParameter(
            self, "SourceTable",
            type="String",
            default="chicago_buildings",
            description="Logical source table name for CDC ingest",
        )

        # ── SNS alert topic ───────────────────────────────────────────────────
        alert_topic = sns.Topic(
            self, "PipelineAlertTopic",
            display_name="Progressive Medallion Pipeline Alerts",
        )
        email_provided = cdk.CfnCondition(
            self, "EmailProvided",
            expression=cdk.Fn.condition_not(
                cdk.Fn.condition_equals(alert_email.value_as_string, "")
            ),
        )
        email_sub = sns.CfnSubscription(
            self, "AlertEmailSub",
            topic_arn=alert_topic.topic_arn,
            protocol="email",
            endpoint=alert_email.value_as_string,
        )
        email_sub.cfn_options.condition = email_provided

        # ── S3 buckets ────────────────────────────────────────────────────────
        common_bucket_props = dict(
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        stage_buckets: dict[str, s3.Bucket] = {}
        for stage in ALL_STAGES:
            logical = STAGE_LOGICAL[stage]
            name = _bucket_name(convention, stage, self.account, self.region, org, env)
            extra = dict(bucket_name=name) if name else {}

            if stage == "audit":
                bucket = s3.Bucket(
                    self, f"StageBucket{logical}",
                    object_lock_enabled=True,
                    versioned=True,  # required for Object Lock
                    **extra,
                    **common_bucket_props,
                )
            else:
                bucket = s3.Bucket(
                    self, f"StageBucket{logical}",
                    **extra,
                    **common_bucket_props,
                )
            stage_buckets[stage] = bucket

        # ── Athena results bucket ─────────────────────────────────────────────
        athena_name = _bucket_name(convention, "athena-results", self.account, self.region, org, env)
        athena_extra = dict(bucket_name=athena_name) if athena_name else {}
        athena_results_bucket = s3.Bucket(
            self, "AthenaResultsBucket",
            **athena_extra,
            **common_bucket_props,
        )

        # ── Athena workgroup ──────────────────────────────────────────────────
        athena.CfnWorkGroup(
            self, "MedallionWorkgroup",
            name="medallion",
            description="Athena workgroup for progressive medallion queries",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=athena_results_bucket.s3_url_for_object("athena-results/"),
                ),
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
            ),
        )

        # ── Glue databases — named [provider]-[product]-[stage] ───────────────
        glue_databases: dict[str, glue.CfnDatabase] = {}
        for stage in CATALOGED_STAGES:
            logical = STAGE_LOGICAL[stage]
            db_name = f"{PROVIDER}-{PRODUCT}-{stage}"
            db = glue.CfnDatabase(
                self, f"GlueDb{logical}",
                catalog_id=self.account,
                database_input=glue.CfnDatabase.DatabaseInputProperty(
                    name=db_name,
                    description=f"Data lake {stage} stage — {PROVIDER} {PRODUCT}",
                ),
            )
            glue_databases[stage] = db

        # ── Glue IAM role (shared across all crawlers) ────────────────────────
        glue_role = iam.Role(
            self, "GlueCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"),
            ],
        )
        for stage in CATALOGED_STAGES:
            stage_buckets[stage].grant_read(glue_role)

        # ── Glue crawlers — named medallion-[stage]-crawler ──────────────────
        for stage in CATALOGED_STAGES:
            logical = STAGE_LOGICAL[stage]
            db_name = f"{PROVIDER}-{PRODUCT}-{stage}"
            bucket = stage_buckets[stage]
            glue.CfnCrawler(
                self, f"Crawler{logical}",
                name=f"medallion-{stage}-crawler",
                role=glue_role.role_arn,
                database_name=db_name,
                description=f"Crawler for medallion {stage} stage",
                targets=glue.CfnCrawler.TargetsProperty(
                    s3_targets=[
                        glue.CfnCrawler.S3TargetProperty(
                            path=bucket.s3_url_for_object(f"{PROVIDER}_{PRODUCT}/"),
                        )
                    ]
                ),
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
            ).add_dependency(glue_databases[stage])

        # ── Lake Formation: register S3 locations ────────────────────────────
        iam.Role(
            self, "LakeFormationAdminRole",
            assumed_by=iam.ServicePrincipal("lakeformation.amazonaws.com"),
        )

        for stage in CATALOGED_STAGES:
            lakeformation.CfnResource(
                self, f"LFResource{STAGE_LOGICAL[stage]}",
                resource_arn=stage_buckets[stage].bucket_arn,
                use_service_linked_role=True,
            )

        # ── Lake Formation: LF-Tag taxonomy ──────────────────────────────────
        lakeformation.CfnTag(
            self, "LFTagDataDomain",
            tag_key="data_domain",
            tag_values=["buildings", "permits", "infrastructure", "finance"],
        )
        lakeformation.CfnTag(
            self, "LFTagDataSensitivity",
            tag_key="data_sensitivity",
            tag_values=["public", "internal", "confidential", "restricted"],
        )
        lakeformation.CfnTag(
            self, "LFTagMedallionStage",
            tag_key="medallion_stage",
            tag_values=LF_STAGE_VALUES,
        )

        # Assign LF-Tags to each Glue database
        for stage in CATALOGED_STAGES:
            logical = STAGE_LOGICAL[stage]
            db_name = f"{PROVIDER}-{PRODUCT}-{stage}"
            lakeformation.CfnTagAssociation(
                self, f"LFTagAssoc{logical}",
                lf_tags=[
                    lakeformation.CfnTagAssociation.LFTagPairProperty(
                        catalog_id=self.account,
                        tag_key="data_domain",
                        tag_values=["buildings"],
                    ),
                    lakeformation.CfnTagAssociation.LFTagPairProperty(
                        catalog_id=self.account,
                        tag_key="data_sensitivity",
                        tag_values=["public"] if stage in ("published", "unified-analytics") else ["internal"],
                    ),
                    lakeformation.CfnTagAssociation.LFTagPairProperty(
                        catalog_id=self.account,
                        tag_key="medallion_stage",
                        tag_values=[stage],
                    ),
                ],
                resource=lakeformation.CfnTagAssociation.ResourceProperty(
                    database=lakeformation.CfnTagAssociation.DatabaseResourceProperty(
                        catalog_id=self.account,
                        name=db_name,
                    ),
                ),
            ).add_dependency(glue_databases[stage])

        # ── CDC Ingest Lambda role ────────────────────────────────────────────
        cdc_role = iam.Role(
            self, "CdcLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        stage_buckets["landing-zone"].grant_put(cdc_role)
        stage_buckets["audit"].grant_put(cdc_role)

        # ── CDC Ingest Lambda ─────────────────────────────────────────────────
        cdc_fn = lambda_.Function(
            self, "CdcIngestFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="cdc_ingest.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            role=cdc_role,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "LANDING_BUCKET": stage_buckets["landing-zone"].bucket_name,
                "SOURCE_TABLE": source_table.value_as_string,
            },
        )

        # ── EventBridge: CDC ingest every 15 min ──────────────────────────────
        events.Rule(
            self, "CdcIngestSchedule",
            schedule=events.Schedule.rate(Duration.minutes(15)),
            targets=[targets.LambdaFunction(cdc_fn)],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "BucketNamingConvention", value=convention,
                      description="Active bucket naming convention")

        for stage in ALL_STAGES:
            logical = STAGE_LOGICAL[stage]
            cdk.CfnOutput(
                self, f"OutputBucket{logical}",
                value=stage_buckets[stage].bucket_name,
                description=f"{stage} stage S3 bucket",
            )

        for stage in CATALOGED_STAGES:
            logical = STAGE_LOGICAL[stage]
            cdk.CfnOutput(
                self, f"OutputGlueDb{logical}",
                value=f"{PROVIDER}-{PRODUCT}-{stage}",
                description=f"Glue database for {stage} stage",
            )

        cdk.CfnOutput(self, "CdcFunctionName", value=cdc_fn.function_name,
                      description="CDC ingest Lambda function name")
        cdk.CfnOutput(self, "AthenaWorkgroup", value="medallion",
                      description="Athena workgroup name")
        cdk.CfnOutput(self, "AlertTopicArn", value=alert_topic.topic_arn,
                      description="SNS pipeline alert topic ARN")
