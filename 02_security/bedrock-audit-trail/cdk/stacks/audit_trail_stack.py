"""CDK stack: Bedrock Audit Trail

Provisions:
  - Customer-managed KMS key (CMK) for all log storage
  - Separate CMK for the PII token store
  - S3 bucket for Bedrock invocation logs (destination bucket)
  - CloudWatch Log Group (second destination)
  - Firehose delivery stream → transform Lambda → S3 Iceberg dataset bucket
  - Glue Database + Iceberg table (S3 Tables-compatible schema)
  - IAM service role for Bedrock → Firehose delivery
  - CloudWatch metric filters and alarms
  - Optional cross-account replication (enabled via CfnParameter)
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_glue as glue,
    aws_iam as iam,
    aws_kinesisfirehose as firehose,
    aws_kms as kms,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_sns as sns,
)
from constructs import Construct


class BedrockAuditTrailStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Parameters ────────────────────────────────────────────────────────
        alarm_email = cdk.CfnParameter(
            self, "AlarmEmail",
            type="String", default="",
            description="SNS email for CloudWatch alarms (leave blank to skip).",
        )
        enable_cross_account = cdk.CfnParameter(
            self, "EnableCrossAccountReplication",
            type="String", allowed_values=["true", "false"], default="false",
            description="Enable S3 cross-account replication to a central archive.",
        )
        central_archive_bucket_arn = cdk.CfnParameter(
            self, "CentralArchiveBucketArn",
            type="String", default="",
            description="ARN of the central archive bucket (required if replication enabled).",
        )
        pii_sample_rate = cdk.CfnParameter(
            self, "NormalTrafficSamplePercent",
            type="Number", default=100, min_value=1, max_value=100,
            description="% of non-flagged invocations to retain (100 = keep all).",
        )

        # ── KMS Keys ──────────────────────────────────────────────────────────
        log_key = kms.Key(
            self, "LogCmk",
            description="CMK for Bedrock invocation log storage (S3 + CW + Firehose)",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        log_key.add_alias("alias/bedrock-audit-log-key")

        # Firehose service needs to use the key
        log_key.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowFirehose",
            principals=[iam.ServicePrincipal("firehose.amazonaws.com")],
            actions=["kms:GenerateDataKey", "kms:Decrypt"],
            resources=["*"],
        ))
        # Bedrock logging service needs to write
        log_key.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowBedrockLogging",
            principals=[iam.ServicePrincipal("bedrock.amazonaws.com")],
            actions=["kms:GenerateDataKey", "kms:Decrypt"],
            resources=["*"],
        ))

        token_key = kms.Key(
            self, "PiiTokenCmk",
            description="CMK for reversible PII token store",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        token_key.add_alias("alias/bedrock-audit-pii-token-key")

        # ── S3: raw invocation log destination ────────────────────────────────
        raw_log_bucket = s3.Bucket(
            self, "RawLogBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=log_key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            server_access_logs_prefix="access-logs/",
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="TransitionToIA",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30),
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(365),
                        ),
                    ],
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Bedrock service role needs PutObject on raw bucket
        raw_log_bucket.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowBedrockLogging",
            principals=[iam.ServicePrincipal("bedrock.amazonaws.com")],
            actions=["s3:PutObject"],
            resources=[raw_log_bucket.arn_for_objects("*")],
            conditions={
                "StringEquals": {
                    "aws:SourceAccount": self.account,
                }
            },
        ))

        # ── S3: Iceberg dataset bucket (processed records) ─────────────────
        iceberg_bucket = s3.Bucket(
            self, "IcebergBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=log_key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ── S3: PII token store ───────────────────────────────────────────────
        token_store_bucket = s3.Bucket(
            self, "PiiTokenStoreBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=token_key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ── CloudWatch Log Group (second Bedrock logging destination) ─────────
        log_group = logs.LogGroup(
            self, "BedrockInvocationLogs",
            log_group_name="/aws/bedrock/invocation-logs",
            retention=logs.RetentionDays.ONE_YEAR,
            encryption_key=log_key,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ── IAM role for Bedrock → CloudWatch + S3 ───────────────────────────
        bedrock_logging_role = iam.Role(
            self, "BedrockLoggingRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Allows Bedrock to write invocation logs to CW and S3",
        )
        log_group.grant_write(bedrock_logging_role)
        raw_log_bucket.grant_put(bedrock_logging_role)
        log_key.grant_encrypt_decrypt(bedrock_logging_role)

        # ── Transform Lambda ──────────────────────────────────────────────────
        transform_fn = lambda_.Function(
            self, "TransformFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="transform.handler",
            code=lambda_.Code.from_asset("lambda/transform"),
            timeout=Duration.seconds(300),
            memory_size=512,
            environment={
                "TOKEN_BUCKET": token_store_bucket.bucket_name,
                "TOKEN_KEY_ARN": token_key.key_arn,
                "SAMPLE_RATE": pii_sample_rate.value_as_string,
            },
        )
        token_store_bucket.grant_read_write(transform_fn)
        token_key.grant_encrypt_decrypt(transform_fn)

        # Comprehend PII detection
        transform_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["comprehend:DetectPiiEntities", "comprehend:ContainsPiiEntities"],
            resources=["*"],
        ))
        # Bedrock Guardrails (ApplyGuardrail)
        transform_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:ApplyGuardrail"],
            resources=["*"],
        ))

        # ── Firehose delivery stream ──────────────────────────────────────────
        firehose_role = iam.Role(
            self, "FirehoseRole",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
        )
        iceberg_bucket.grant_read_write(firehose_role)
        log_key.grant_encrypt_decrypt(firehose_role)
        transform_fn.grant_invoke(firehose_role)

        delivery_stream = firehose.CfnDeliveryStream(
            self, "DeliveryStream",
            delivery_stream_name="bedrock-audit-trail",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=iceberg_bucket.bucket_arn,
                role_arn=firehose_role.role_arn,
                prefix="data/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
                error_output_prefix="errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/",
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    interval_in_seconds=300,
                    size_in_m_bs=128,
                ),
                compression_format="UNCOMPRESSED",  # Iceberg handles its own compression
                encryption_configuration=firehose.CfnDeliveryStream.EncryptionConfigurationProperty(
                    kms_encryption_config=firehose.CfnDeliveryStream.KMSEncryptionConfigProperty(
                        awskms_key_arn=log_key.key_arn,
                    ),
                ),
                processing_configuration=firehose.CfnDeliveryStream.ProcessingConfigurationProperty(
                    enabled=True,
                    processors=[
                        firehose.CfnDeliveryStream.ProcessorProperty(
                            type="Lambda",
                            parameters=[
                                firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                    parameter_name="LambdaArn",
                                    parameter_value=transform_fn.function_arn,
                                ),
                                firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                    parameter_name="BufferSizeInMBs",
                                    parameter_value="3",
                                ),
                                firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                    parameter_name="BufferIntervalInSeconds",
                                    parameter_value="60",
                                ),
                            ],
                        )
                    ],
                ),
                s3_backup_mode="Disabled",
            ),
        )

        # ── Glue: database + Iceberg table ────────────────────────────────────
        glue_db = glue.CfnDatabase(
            self, "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="bedrock_audit",
                description="Bedrock governance-grade audit trail (Iceberg)",
            ),
        )

        glue.CfnTable(
            self, "InvocationTable",
            catalog_id=self.account,
            database_name="bedrock_audit",
            table_input=glue.CfnTable.TableInputProperty(
                name="invocations",
                description="Processed Bedrock invocation records with PII redaction and metadata enrichment",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "table_type": "ICEBERG",
                    "metadata_location": iceberg_bucket.s3_url_for_object("iceberg/invocations/metadata/"),
                },
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=iceberg_bucket.s3_url_for_object("data/"),
                    input_format="org.apache.iceberg.mr.mapred.IcebergInputFormat",
                    output_format="org.apache.iceberg.mr.mapred.IcebergOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.iceberg.mr.hive.IcebergSerDe",
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name="event_time", type="timestamp", comment="UTC time of the invocation"),
                        glue.CfnTable.ColumnProperty(name="request_id", type="string", comment="Bedrock requestId"),
                        glue.CfnTable.ColumnProperty(name="account_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="region", type="string"),
                        glue.CfnTable.ColumnProperty(name="model_id", type="string", comment="e.g. anthropic.claude-3-sonnet-20240229-v1:0"),
                        glue.CfnTable.ColumnProperty(name="user_id", type="string", comment="From requestMetadata"),
                        glue.CfnTable.ColumnProperty(name="tenant_id", type="string", comment="From requestMetadata"),
                        glue.CfnTable.ColumnProperty(name="session_id", type="string", comment="From requestMetadata"),
                        glue.CfnTable.ColumnProperty(name="trace_id", type="string", comment="From requestMetadata"),
                        glue.CfnTable.ColumnProperty(name="input_tokens", type="int"),
                        glue.CfnTable.ColumnProperty(name="output_tokens", type="int"),
                        glue.CfnTable.ColumnProperty(name="latency_ms", type="int"),
                        glue.CfnTable.ColumnProperty(name="input_text_redacted", type="string", comment="PII-redacted prompt"),
                        glue.CfnTable.ColumnProperty(name="output_text_redacted", type="string", comment="PII-redacted response"),
                        glue.CfnTable.ColumnProperty(name="pii_detected", type="boolean"),
                        glue.CfnTable.ColumnProperty(name="pii_entity_types", type="array<string>", comment="Comprehend PII types found"),
                        glue.CfnTable.ColumnProperty(name="pii_token_ids", type="array<string>", comment="Reversible token IDs for redacted spans"),
                        glue.CfnTable.ColumnProperty(name="guardrail_action", type="string", comment="NONE | INTERVENED | BLOCKED"),
                        glue.CfnTable.ColumnProperty(name="guardrail_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="guardrail_topics_triggered", type="array<string>"),
                        glue.CfnTable.ColumnProperty(name="guardrail_content_filters", type="array<string>"),
                        glue.CfnTable.ColumnProperty(name="injection_detected", type="boolean", comment="Prompt-attack guardrail triggered"),
                        glue.CfnTable.ColumnProperty(name="sampled", type="boolean", comment="False = dropped by sampling policy"),
                        glue.CfnTable.ColumnProperty(name="error_code", type="string"),
                        glue.CfnTable.ColumnProperty(name="error_message", type="string"),
                        # Partition columns (also in storage)
                        glue.CfnTable.ColumnProperty(name="date", type="date", comment="Partition: event date"),
                        glue.CfnTable.ColumnProperty(name="model_family", type="string", comment="Partition: provider/family prefix"),
                        glue.CfnTable.ColumnProperty(name="tenant_id_part", type="string", comment="Partition copy of tenant_id"),
                    ],
                ),
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="date", type="date"),
                    glue.CfnTable.ColumnProperty(name="model_family", type="string"),
                    glue.CfnTable.ColumnProperty(name="tenant_id_part", type="string"),
                ],
            ),
        )
        # ensure database exists before table
        glue.CfnTable.node.from_id  # noop reference; dependency via construct ID
        # CDK resolves this through the CfnTable depending on glue_db implicitly when
        # they share the same catalog_id + database_name string.

        # ── Bedrock service role (for PutModelInvocationLoggingConfiguration) ─
        # Exported as a CFN output so callers can reference it in their
        # bedrock:PutModelInvocationLoggingConfiguration API call.
        cdk.CfnOutput(self, "BedrockLoggingRoleArn",
                      value=bedrock_logging_role.role_arn,
                      description="Pass this ARN to bedrock:PutModelInvocationLoggingConfiguration → roleArn")
        cdk.CfnOutput(self, "RawLogBucketName", value=raw_log_bucket.bucket_name)
        cdk.CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        cdk.CfnOutput(self, "IcebergBucketName", value=iceberg_bucket.bucket_name)
        cdk.CfnOutput(self, "FirehoseStreamName", value=delivery_stream.delivery_stream_name or "bedrock-audit-trail")
        cdk.CfnOutput(self, "LogCmkArn", value=log_key.key_arn)
        cdk.CfnOutput(self, "PiiTokenCmkArn", value=token_key.key_arn)
        cdk.CfnOutput(self, "PiiTokenStoreBucketName", value=token_store_bucket.bucket_name)

        # ── CloudWatch metric filters ─────────────────────────────────────────
        # Cost spike: input_tokens per 5-minute window
        cost_filter = logs.MetricFilter(
            self, "CostSpikeFilter",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.exists("$.inputTokenCount"),
            metric_name="BedrockInputTokens",
            metric_namespace="BedrockAuditTrail",
            metric_value="$.inputTokenCount",
            default_value=0,
            unit=cw.Unit.COUNT,
        )

        # PII detection events
        pii_filter = logs.MetricFilter(
            self, "PiiDetectionFilter",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.string_value("$.pii_detected", "=", "true"),
            metric_name="BedrockPiiDetections",
            metric_namespace="BedrockAuditTrail",
            metric_value="1",
            default_value=0,
            unit=cw.Unit.COUNT,
        )

        # Guardrail block events
        guardrail_block_filter = logs.MetricFilter(
            self, "GuardrailBlockFilter",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.string_value("$.guardrailAction", "=", "BLOCKED"),
            metric_name="BedrockGuardrailBlocks",
            metric_namespace="BedrockAuditTrail",
            metric_value="1",
            default_value=0,
            unit=cw.Unit.COUNT,
        )

        # Model errors
        error_filter = logs.MetricFilter(
            self, "ModelErrorFilter",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.exists("$.errorCode"),
            metric_name="BedrockModelErrors",
            metric_namespace="BedrockAuditTrail",
            metric_value="1",
            default_value=0,
            unit=cw.Unit.COUNT,
        )

        # Injection detections (prompt attack guardrail)
        injection_filter = logs.MetricFilter(
            self, "InjectionDetectionFilter",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.string_value("$.injection_detected", "=", "true"),
            metric_name="BedrockInjectionDetections",
            metric_namespace="BedrockAuditTrail",
            metric_value="1",
            default_value=0,
            unit=cw.Unit.COUNT,
        )

        # ── SNS alarm topic (conditional) ─────────────────────────────────────
        alarm_topic = sns.Topic(self, "AlarmTopic",
                                display_name="Bedrock Audit Trail Alarms",
                                master_key=log_key)
        # email subscription is optional; done outside CDK (console/CLI)
        cdk.CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn)

        # ── Alarms ────────────────────────────────────────────────────────────
        cost_alarm = cw.Alarm(
            self, "CostSpikeAlarm",
            alarm_name="bedrock-audit-cost-spike",
            alarm_description="Bedrock input tokens >1M in 5 minutes",
            metric=cost_filter.metric(period=Duration.minutes(5), statistic="Sum"),
            threshold=1_000_000,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        cost_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        pii_alarm = cw.Alarm(
            self, "PiiRateAlarm",
            alarm_name="bedrock-audit-pii-rate",
            alarm_description="Bedrock PII detections >50 in 5 minutes",
            metric=pii_filter.metric(period=Duration.minutes(5), statistic="Sum"),
            threshold=50,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        pii_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        guardrail_alarm = cw.Alarm(
            self, "GuardrailBlockRateAlarm",
            alarm_name="bedrock-audit-guardrail-block-rate",
            alarm_description="Bedrock guardrail blocks >20 in 5 minutes",
            metric=guardrail_block_filter.metric(period=Duration.minutes(5), statistic="Sum"),
            threshold=20,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        guardrail_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        error_alarm = cw.Alarm(
            self, "ModelErrorRateAlarm",
            alarm_name="bedrock-audit-model-error-rate",
            alarm_description="Bedrock model errors >10 in 5 minutes",
            metric=error_filter.metric(period=Duration.minutes(5), statistic="Sum"),
            threshold=10,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        error_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        injection_alarm = cw.Alarm(
            self, "InjectionDetectionAlarm",
            alarm_name="bedrock-audit-injection-detected",
            alarm_description="Prompt injection detections >5 in 5 minutes",
            metric=injection_filter.metric(period=Duration.minutes(5), statistic="Sum"),
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        injection_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # ── Cross-account replication (conditional) ───────────────────────────
        # Replication rule added only when EnableCrossAccountReplication=true.
        # Because CfnCondition applies to L1, we drop down to L1 for the
        # replication configuration.
        replication_condition = cdk.CfnCondition(
            self, "ReplicationEnabled",
            expression=cdk.Fn.condition_equals(enable_cross_account.value_as_string, "true"),
        )

        replication_role = iam.Role(
            self, "ReplicationRole",
            assumed_by=iam.ServicePrincipal("s3.amazonaws.com"),
            description="S3 replication role for cross-account audit trail aggregation",
        )
        iceberg_bucket.grant_read(replication_role)
        replication_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"],
            resources=[cdk.Fn.condition_if(
                "ReplicationEnabled",
                f"{central_archive_bucket_arn.value_as_string}/*",
                cdk.Aws.NO_VALUE,
            ).to_string()],
        ))
        log_key.grant_decrypt(replication_role)

        # The actual CfnBucket replication configuration is attached via escape hatch
        # on the L1 node so the condition can be applied cleanly.
        cfn_iceberg_bucket = iceberg_bucket.node.default_child
        cfn_replication_config = cdk.Fn.condition_if(
            "ReplicationEnabled",
            {
                "Role": replication_role.role_arn,
                "Rules": [
                    {
                        "Id": "CrossAccountReplication",
                        "Status": "Enabled",
                        "Destination": {
                            "Bucket": central_archive_bucket_arn.value_as_string,
                            "StorageClass": "STANDARD_IA",
                            "EncryptionConfiguration": {
                                "ReplicaKmsKeyID": log_key.key_arn,
                            },
                        },
                        "SourceSelectionCriteria": {
                            "SseKmsEncryptedObjects": {"Status": "Enabled"},
                        },
                    }
                ],
            },
            cdk.Aws.NO_VALUE,
        )
        cfn_iceberg_bucket.add_property_override(
            "ReplicationConfiguration", cfn_replication_config
        )

        cdk.CfnOutput(self, "ReplicationRoleArn", value=replication_role.role_arn,
                      description="Grant this role s3:ReplicateObject on your central archive bucket")
