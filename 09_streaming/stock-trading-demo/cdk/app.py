#!/usr/bin/env python3
"""CDK app for the Stock Trading streaming demo.

Provisions the full pipeline in code:
  - Kinesis Data Stream
  - Glue database + Iceberg table referencing an existing S3 Tables bucket
  - Firehose delivery stream (Iceberg destination) + scoped IAM role
  - Lake Formation grants for the Firehose role
  - Trend-analyzer Lambda (Python 3.12) + DLQ + scoped Bedrock/SNS IAM
  - SNS topic for trend notifications
  - CloudWatch dashboard + alarms

The S3 Tables bucket itself must exist out-of-band (see README) because S3 Tables
buckets are not yet first-class CloudFormation resources.
"""
import os

import aws_cdk as cdk
from aws_cdk import (
    App,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_athena as athena,
    aws_kinesis as kinesis,
    aws_kinesisfirehose as firehose,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_logs as logs,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sqs as sqs,
)
from constructs import Construct

S3_TABLES_BUCKET_NAME = "stock-trading-demo"
S3_TABLES_NAMESPACE = "trading"
S3_TABLES_TABLE = "trades"
FIREHOSE_STREAM_NAME = "KDS-s3tables-trades"
KINESIS_STREAM_NAME = "stock-trades-stream"
ATHENA_DATA_CATALOG_NAME = "s3tables_stock_trading"

class StockTradingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        s3_tables_bucket_arn = (
            f"arn:aws:s3tables:{self.region}:{self.account}:bucket/{S3_TABLES_BUCKET_NAME}"
        )

        kinesis_stream = kinesis.Stream(
            self,
            "StockTradingStream",
            stream_name=KINESIS_STREAM_NAME,
            shard_count=2,
            retention_period=Duration.hours(24),
        )

        backup_bucket = s3.Bucket(
            self,
            "StockTradingBackupBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        sns_topic = sns.Topic(
            self,
            "StockTradingTrendTopic",
            display_name="Stock Trading Trend Notifications",
        )

        # The S3 Tables ↔ AWS analytics integration creates a federated Glue
        # catalog `s3tablescatalog` and auto-discovers buckets/namespaces/tables
        # below it. Firehose (and Athena) must reference that catalog hierarchy
        # rather than a manually-created Glue database/table in the default
        # catalog. We do NOT create a Glue DB/Table here — they are autoexposed
        # by the federation.
        federated_catalog_id = f"{self.account}:s3tablescatalog/{S3_TABLES_BUCKET_NAME}"
        federated_catalog_arn = (
            f"arn:aws:glue:{self.region}:{self.account}:catalog/s3tablescatalog/{S3_TABLES_BUCKET_NAME}"
        )

        # Athena needs a DataCatalog binding to query the federated S3 Tables
        # catalog. Without this, query execution context can only target the
        # default AwsDataCatalog and the federated namespace shows up as
        # SCHEMA_NOT_FOUND.
        athena_data_catalog = athena.CfnDataCatalog(
            self,
            "S3TablesAthenaCatalog",
            name=ATHENA_DATA_CATALOG_NAME,
            type="GLUE",
            description="S3 Tables federated catalog for stock-trading-demo",
            parameters={"catalog-id": federated_catalog_id},
        )

        firehose_log_group = logs.LogGroup(
            self,
            "FirehoseLogGroup",
            log_group_name=f"/aws/kinesisfirehose/{FIREHOSE_STREAM_NAME}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        firehose_destination_log_stream = logs.LogStream(
            self,
            "FirehoseDestinationLogStream",
            log_group=firehose_log_group,
            log_stream_name="DestinationDelivery",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Pin the role name so Lake Formation grants on the federated catalog
        # can be issued against the exact ARN out-of-band (CFN's
        # AWS::LakeFormation::Permissions / ::PrincipalPermissions both reject
        # the federated catalog_id format `<account>:s3tablescatalog/<bucket>`
        # in their schema validation). See deploy.sh for the LF grant step.
        firehose_role = iam.Role(
            self,
            "FirehoseS3TablesRole",
            role_name="StockTradingFirehoseS3TablesRole",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
            description="Firehose service role for Iceberg destination on S3 Tables",
        )

        # The federated Glue catalog calls back into S3 Tables for namespace +
        # bucket lookups (s3tables:GetNamespace, GetTableBucket, etc.) so the
        # role needs s3tables:* read on bucket+namespace+table even in
        # "Lake Formation mode" — the docs don't list these but the federation
        # fails without them.
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3TablesFederationLookups",
                actions=[
                    "s3tables:GetTableBucket",
                    "s3tables:GetNamespace",
                    "s3tables:ListNamespaces",
                    "s3tables:ListTables",
                    "s3tables:GetTable",
                    "s3tables:GetTableData",
                    "s3tables:GetTableMetadataLocation",
                    "s3tables:UpdateTableMetadataLocation",
                    "s3tables:PutTableData",
                ],
                resources=[
                    f"arn:aws:s3tables:{self.region}:{self.account}:bucket/*",
                    f"arn:aws:s3tables:{self.region}:{self.account}:bucket/*/table/*",
                ],
            )
        )

        # IAM resource scope per AWS Firehose+S3-Tables docs. The federated
        # `s3tablescatalog` catalogs and child databases/tables must each be
        # explicitly listed.
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                sid="GlueCatalogAccessPermission",
                actions=[
                    "glue:GetDatabase",
                    "glue:GetDatabases",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:UpdateTable",
                ],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:catalog/s3tablescatalog",
                    f"arn:aws:glue:{self.region}:{self.account}:catalog/s3tablescatalog/*",
                    f"arn:aws:glue:{self.region}:{self.account}:database/*",
                    f"arn:aws:glue:{self.region}:{self.account}:table/*/*",
                ],
            )
        )

        firehose_role.add_to_policy(
            iam.PolicyStatement(
                sid="LakeFormationDataAccess",
                actions=["lakeformation:GetDataAccess"],
                # lakeformation:GetDataAccess does not support resource-level
                # permissions; "*" is the only valid value per AWS docs.
                resources=["*"],
            )
        )

        backup_bucket.grant_read_write(firehose_role)
        # Firehose requires kinesis:DescribeStream explicitly; grant_read() does
        # not include it. Adding the full read set the service expects.
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                sid="KinesisStreamRead",
                actions=[
                    "kinesis:DescribeStream",
                    "kinesis:DescribeStreamSummary",
                    "kinesis:GetRecords",
                    "kinesis:GetShardIterator",
                    "kinesis:ListShards",
                    "kinesis:SubscribeToShard",
                ],
                resources=[kinesis_stream.stream_arn],
            )
        )
        firehose_log_group.grant_write(firehose_role)

        # Lake Formation grants on the federated catalog must be issued
        # out-of-band (see grant_lake_formation.sh) because CloudFormation's
        # AWS::LakeFormation::Permissions / ::PrincipalPermissions schemas
        # reject the federated catalog_id format
        # `<account>:s3tablescatalog/<bucket>`. Those grants target the
        # role pinned above (StockTradingFirehoseS3TablesRole).

        # 2-phase deploy: on the very first `cdk deploy`, set
        # `-c skipFirehose=true` so the IAM role lands without the Firehose.
        # Then issue the LF grant on the federated catalog (see
        # grant_lake_formation.sh), and re-run `cdk deploy` (without the flag)
        # to create the Firehose. CFN's LF::Permissions / ::PrincipalPermissions
        # schemas both reject the federated catalog_id format, so this is the
        # cleanest path that keeps the rest of the pipeline pure CDK.
        skip_firehose = self.node.try_get_context("skipFirehose") == "true"

        if not skip_firehose:
            delivery_stream = firehose.CfnDeliveryStream(
                self,
                "TradesFirehose",
                delivery_stream_name=FIREHOSE_STREAM_NAME,
                delivery_stream_type="KinesisStreamAsSource",
                kinesis_stream_source_configuration=firehose.CfnDeliveryStream.KinesisStreamSourceConfigurationProperty(
                    kinesis_stream_arn=kinesis_stream.stream_arn,
                    role_arn=firehose_role.role_arn,
                ),
                iceberg_destination_configuration=firehose.CfnDeliveryStream.IcebergDestinationConfigurationProperty(
                    role_arn=firehose_role.role_arn,
                    catalog_configuration=firehose.CfnDeliveryStream.CatalogConfigurationProperty(
                        catalog_arn=federated_catalog_arn,
                    ),
                    destination_table_configuration_list=[
                        firehose.CfnDeliveryStream.DestinationTableConfigurationProperty(
                            destination_database_name=S3_TABLES_NAMESPACE,
                            destination_table_name=S3_TABLES_TABLE,
                            unique_keys=["trade_id"],
                            s3_error_output_prefix="error/",
                        )
                    ],
                    buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                        size_in_m_bs=5,
                        interval_in_seconds=300,
                    ),
                    cloud_watch_logging_options=firehose.CfnDeliveryStream.CloudWatchLoggingOptionsProperty(
                        enabled=True,
                        log_group_name=firehose_log_group.log_group_name,
                        log_stream_name=firehose_destination_log_stream.log_stream_name,
                    ),
                    s3_backup_mode="FailedDataOnly",
                    retry_options=firehose.CfnDeliveryStream.RetryOptionsProperty(
                        duration_in_seconds=300,
                    ),
                    s3_configuration=firehose.CfnDeliveryStream.S3DestinationConfigurationProperty(
                        bucket_arn=backup_bucket.bucket_arn,
                        role_arn=firehose_role.role_arn,
                        prefix="firehose-backup/",
                        error_output_prefix="firehose-errors/",
                    ),
                ),
            )
            # Firehose validates IAM at create time. Without this dep, CFN can
            # race and submit the Firehose request before the inline policy
            # attaches.
            firehose_role_policy = firehose_role.node.try_find_child("DefaultPolicy")
            if firehose_role_policy is not None:
                delivery_stream.node.add_dependency(firehose_role_policy)

        lambda_dlq = sqs.Queue(
            self,
            "TrendAnalyzerDLQ",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaKinesisExecutionRole"
                ),
            ],
        )

        # Nova Micro is only available via cross-region inference profiles —
        # foundation-model invocation returns ValidationException ("on-demand
        # throughput isn't supported"). Use the us.* inference profile id and
        # grant InvokeModel on both the profile and the underlying foundation
        # model (the runtime fans out to whichever region serves the request).
        bedrock_model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        bedrock_foundation_model_arn = (
            f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
        )
        bedrock_inference_profile_arn = (
            f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{bedrock_model_id}"
        )
        bedrock_model_arn = bedrock_inference_profile_arn  # legacy alias used downstream
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    bedrock_inference_profile_arn,
                    bedrock_foundation_model_arn,
                    # Cross-region profiles route to other regions, so widen
                    # the foundation-model ARN match across all regions.
                    f"arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                ],
            )
        )

        sns_topic.grant_publish(lambda_role)
        lambda_dlq.grant_send_messages(lambda_role)

        trend_analyzer = lambda_.Function(
            self,
            "TrendAnalyzerFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="trend_analyzer.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            timeout=Duration.seconds(60),
            memory_size=256,
            role=lambda_role,
            dead_letter_queue_enabled=True,
            dead_letter_queue=lambda_dlq,
            environment={
                "SNS_TOPIC_ARN": sns_topic.topic_arn,
                "BEDROCK_MODEL_ID": bedrock_model_id,
            },
        )

        trend_analyzer.add_event_source(
            lambda_event_sources.KinesisEventSource(
                stream=kinesis_stream,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=100,
                max_batching_window=Duration.seconds(5),
                bisect_batch_on_error=True,
                retry_attempts=3,
                on_failure=lambda_event_sources.SqsDlq(lambda_dlq),
            )
        )

        market_summarizer = self._build_market_summarizer(
            backup_bucket=backup_bucket,
            sns_topic=sns_topic,
            bedrock_model_id=bedrock_model_id,
            bedrock_inference_profile_arn=bedrock_inference_profile_arn,
            bedrock_foundation_model_arn=bedrock_foundation_model_arn,
        )

        self._build_dashboard_and_alarms(
            kinesis_stream=kinesis_stream,
            firehose_stream_name=FIREHOSE_STREAM_NAME,
            trend_analyzer=trend_analyzer,
            sns_topic=sns_topic,
            market_summarizer=market_summarizer,
        )

        CfnOutput(self, "KinesisStreamName", value=kinesis_stream.stream_name)
        CfnOutput(self, "KinesisStreamArn", value=kinesis_stream.stream_arn)
        CfnOutput(self, "S3TablesBucketArn", value=s3_tables_bucket_arn)
        CfnOutput(self, "S3TablesBucketName", value=S3_TABLES_BUCKET_NAME)
        CfnOutput(self, "S3TablesNamespace", value=S3_TABLES_NAMESPACE)
        CfnOutput(self, "S3TablesTableName", value=S3_TABLES_TABLE)
        CfnOutput(self, "FirehoseStreamName", value=FIREHOSE_STREAM_NAME)
        CfnOutput(self, "FirehoseRoleArn", value=firehose_role.role_arn)
        CfnOutput(self, "TrendAnalyzerFunctionName", value=trend_analyzer.function_name)
        CfnOutput(self, "TrendAnalyzerFunctionArn", value=trend_analyzer.function_arn)
        CfnOutput(self, "SNSTopicARN", value=sns_topic.topic_arn)
        CfnOutput(self, "S3BucketName", value=backup_bucket.bucket_name)
        CfnOutput(self, "DLQUrl", value=lambda_dlq.queue_url)
        CfnOutput(
            self,
            "MarketSummarizerFunctionName",
            value=market_summarizer.function_name,
        )

    def _build_market_summarizer(
        self,
        *,
        backup_bucket,
        sns_topic,
        bedrock_model_id,
        bedrock_inference_profile_arn,
        bedrock_foundation_model_arn,
    ):
        """Hourly Athena → Bedrock → SNS summary of trading.trades."""
        role = iam.Role(
            self,
            "MarketSummarizerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Athena needs to start queries, read state, and read results from the
        # workgroup. We scope to the primary workgroup since that's the default
        # the Lambda uses.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                ],
                resources=[
                    f"arn:aws:athena:{self.region}:{self.account}:workgroup/primary",
                ],
            )
        )

        # Athena needs to look up the DataCatalog binding (GLUE-type) that
        # fronts the federated s3tablescatalog.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["athena:GetDataCatalog"],
                resources=[
                    f"arn:aws:athena:{self.region}:{self.account}:datacatalog/{ATHENA_DATA_CATALOG_NAME}",
                ],
            )
        )

        # Glue catalog access for the Athena query planner. Athena reads the
        # federated S3 Tables catalog (`s3tablescatalog/<bucket>`). Lake
        # Formation is the real authorization layer; the IAM scope here is
        # broad on Glue resources because the federated ARN scheme isn't
        # supported uniformly across services yet.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetCatalog",
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                ],
                resources=["*"],
            )
        )

        # Lake Formation gate for reading the Iceberg table.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["lakeformation:GetDataAccess"],
                resources=["*"],
            )
        )

        # S3 Tables read for the Iceberg metadata pointers used during query.
        # Athena's federation source enumerates the bucket → namespace → table
        # hierarchy each query, so it requires the bucket-level Get/List actions
        # in addition to the table-level ones — these aren't documented but are
        # enforced (same gotcha hit by the Firehose role).
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3tables:GetTableBucket",
                    "s3tables:GetNamespace",
                    "s3tables:GetTable",
                    "s3tables:GetTableData",
                    "s3tables:GetTableMetadataLocation",
                    "s3tables:ListTables",
                    "s3tables:ListNamespaces",
                ],
                resources=[
                    f"arn:aws:s3tables:{self.region}:{self.account}:bucket/{S3_TABLES_BUCKET_NAME}",
                    f"arn:aws:s3tables:{self.region}:{self.account}:bucket/{S3_TABLES_BUCKET_NAME}/*",
                ],
            )
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    bedrock_inference_profile_arn,
                    bedrock_foundation_model_arn,
                    f"arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0",
                ],
            )
        )

        sns_topic.grant_publish(role)
        backup_bucket.grant_read_write(role)

        function = lambda_.Function(
            self,
            "MarketSummarizerFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="market_summarizer.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            timeout=Duration.minutes(5),
            memory_size=512,
            role=role,
            environment={
                "GLUE_DATABASE": S3_TABLES_NAMESPACE,
                "ATHENA_CATALOG": ATHENA_DATA_CATALOG_NAME,
                "ATHENA_OUTPUT_BUCKET": backup_bucket.bucket_name,
                "ATHENA_WORKGROUP": "primary",
                "BEDROCK_MODEL_ID": bedrock_model_id,
                "SNS_TOPIC_ARN": sns_topic.topic_arn,
            },
        )

        events.Rule(
            self,
            "MarketSummarizerSchedule",
            description="Trigger the hourly market summary Lambda",
            schedule=events.Schedule.rate(Duration.hours(1)),
            targets=[events_targets.LambdaFunction(function)],
        )

        cloudwatch.Alarm(
            self,
            "MarketSummarizerErrorAlarm",
            metric=function.metric_errors(period=Duration.minutes(15)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Hourly market summarizer Lambda errored",
        )

        return function

    def _build_dashboard_and_alarms(
        self,
        *,
        kinesis_stream,
        firehose_stream_name,
        trend_analyzer,
        sns_topic,
        market_summarizer,
    ):
        kinesis_iterator_age = cloudwatch.Metric(
            namespace="AWS/Kinesis",
            metric_name="GetRecords.IteratorAgeMilliseconds",
            dimensions_map={"StreamName": kinesis_stream.stream_name},
            statistic="Maximum",
            period=Duration.minutes(1),
        )
        kinesis_incoming = cloudwatch.Metric(
            namespace="AWS/Kinesis",
            metric_name="IncomingRecords",
            dimensions_map={"StreamName": kinesis_stream.stream_name},
            statistic="Sum",
            period=Duration.minutes(1),
        )
        firehose_freshness = cloudwatch.Metric(
            namespace="AWS/Firehose",
            metric_name="DeliveryToS3.DataFreshness",
            dimensions_map={"DeliveryStreamName": firehose_stream_name},
            statistic="Maximum",
            period=Duration.minutes(1),
        )
        firehose_records = cloudwatch.Metric(
            namespace="AWS/Firehose",
            metric_name="IncomingRecords",
            dimensions_map={"DeliveryStreamName": firehose_stream_name},
            statistic="Sum",
            period=Duration.minutes(1),
        )

        cloudwatch.Alarm(
            self,
            "LambdaErrorAlarm",
            metric=trend_analyzer.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Trend analyzer Lambda is throwing errors",
        )
        cloudwatch.Alarm(
            self,
            "FirehoseFreshnessAlarm",
            metric=firehose_freshness,
            threshold=900,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Firehose data freshness > 15 minutes — backlog growing",
        )
        cloudwatch.Alarm(
            self,
            "KinesisIteratorAgeAlarm",
            metric=kinesis_iterator_age,
            threshold=60_000,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Kinesis consumer falling > 60s behind",
        )

        cloudwatch.Dashboard(
            self,
            "StockTradingDashboard",
            dashboard_name="StockTradingDemo",
            widgets=[
                [
                    cloudwatch.GraphWidget(
                        title="Kinesis throughput",
                        left=[kinesis_incoming],
                        right=[kinesis_iterator_age],
                        width=12,
                    ),
                    cloudwatch.GraphWidget(
                        title="Firehose to S3 Tables",
                        left=[firehose_records],
                        right=[firehose_freshness],
                        width=12,
                    ),
                ],
                [
                    cloudwatch.GraphWidget(
                        title="Trend analyzer Lambda",
                        left=[
                            trend_analyzer.metric_invocations(),
                            trend_analyzer.metric_errors(),
                            trend_analyzer.metric_throttles(),
                        ],
                        right=[trend_analyzer.metric_duration()],
                        width=24,
                    ),
                ],
                [
                    cloudwatch.SingleValueWidget(
                        title="SNS notifications published",
                        metrics=[
                            cloudwatch.Metric(
                                namespace="AWS/SNS",
                                metric_name="NumberOfMessagesPublished",
                                dimensions_map={"TopicName": sns_topic.topic_name},
                                statistic="Sum",
                                period=Duration.hours(1),
                            )
                        ],
                        width=24,
                    ),
                ],
                [
                    cloudwatch.GraphWidget(
                        title="Hourly market summarizer",
                        left=[
                            market_summarizer.metric_invocations(),
                            market_summarizer.metric_errors(),
                        ],
                        right=[market_summarizer.metric_duration()],
                        width=24,
                    ),
                ],
            ],
        )


app = App()
StockTradingStack(
    app,
    "StockTradingStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    ),
)
app.synth()
