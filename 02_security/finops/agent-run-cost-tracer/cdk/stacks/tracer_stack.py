"""CDK stack: AgentRunCostTracer

Provisions the emit infrastructure for RunTracer:
  - KMS CMK for all storage
  - S3 spans bucket  (Firehose landing zone + nightly stitcher output)
  - Kinesis Firehose delivery stream → S3 (NDJSON, partitioned by date)
  - CloudWatch log group  (alternative emit path)
  - IAM role  (least-privilege: Firehose PutRecord + CW PutLogEvents)

Environment variables emitted as CloudFormation Outputs so callers can
configure RunTracer without hard-coding resource names:
  FINOPS_FIREHOSE_STREAM
  FINOPS_CW_LOG_GROUP
  FINOPS_SPANS_BUCKET
"""

from __future__ import annotations

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_s3 as s3,
)
from aws_cdk import aws_kinesisfirehose as firehose
from constructs import Construct


class AgentRunCostTracerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── KMS CMK ───────────────────────────────────────────────────────────
        cmk = kms.Key(
            self, "TracerCmk",
            description="CMK for AgentRunCostTracer spans storage",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        cmk.add_alias("alias/agent-run-cost-tracer-key")

        # ── S3 spans bucket ───────────────────────────────────────────────────
        spans_bucket = s3.Bucket(
            self, "SpansBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=cmk,
            bucket_key_enabled=True,
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
                ),
                s3.LifecycleRule(
                    id="ExpireOld",
                    expiration=Duration.days(365),
                ),
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ── IAM role for Firehose ─────────────────────────────────────────────
        firehose_role = iam.Role(
            self, "FirehoseRole",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
        )
        spans_bucket.grant_read_write(firehose_role)
        cmk.grant_encrypt_decrypt(firehose_role)

        # ── Kinesis Firehose → S3 ─────────────────────────────────────────────
        # Partitions by year/month/day to match the loader.py prefix logic
        delivery_stream = firehose.CfnDeliveryStream(
            self, "SpansDeliveryStream",
            delivery_stream_name="agent-run-cost-spans",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=firehose.CfnDeliveryStream\
                .ExtendedS3DestinationConfigurationProperty(
                    bucket_arn=spans_bucket.bucket_arn,
                    role_arn=firehose_role.role_arn,
                    prefix="spans/!{timestamp:yyyy}/!{timestamp:MM}/!{timestamp:dd}/",
                    error_output_prefix="errors/!{firehose:error-output-type}/!{timestamp:yyyy}/!{timestamp:MM}/",
                    buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                        interval_in_seconds=60,
                        size_in_m_bs=5,
                    ),
                    compression_format="UNCOMPRESSED",
                    encryption_configuration=firehose.CfnDeliveryStream\
                        .EncryptionConfigurationProperty(
                            kms_encryption_config=firehose.CfnDeliveryStream\
                                .KMSEncryptionConfigProperty(
                                    awskms_key_arn=cmk.key_arn,
                                ),
                        ),
                ),
        )

        # ── CloudWatch log group ──────────────────────────────────────────────
        cw_log_group = logs.LogGroup(
            self, "SpansLogGroup",
            log_group_name="/agent-run-cost-tracer/spans",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=cmk,
            removal_policy=RemovalPolicy.DESTROY,
        )
        # Grant CW the right to use the CMK for log encryption
        cmk.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudWatchLogs",
            principals=[iam.ServicePrincipal(
                f"logs.{self.region}.amazonaws.com"
            )],
            actions=["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey",
                     "kms:DescribeKey"],
            resources=["*"],
        ))

        # ── IAM role for application callers ─────────────────────────────────
        # Least-privilege role an application assumes to emit spans
        tracer_role = iam.Role(
            self, "TracerRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="Allows RunTracer to PUT to Firehose and CW",
        )
        tracer_role.add_to_policy(iam.PolicyStatement(
            actions=["firehose:PutRecord", "firehose:PutRecordBatch"],
            resources=[
                f"arn:aws:firehose:{self.region}:{self.account}"
                f":deliverystream/agent-run-cost-spans"
            ],
        ))
        tracer_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:DescribeLogStreams",
            ],
            resources=[cw_log_group.log_group_arn + ":*"],
        ))
        cmk.grant_encrypt_decrypt(tracer_role)

        # ── Outputs ───────────────────────────────────────────────────────────
        CfnOutput(self, "FirehoseStreamName",
                  value=delivery_stream.delivery_stream_name or "agent-run-cost-spans",
                  export_name="AgentRunCostTracerFirehoseStream")
        CfnOutput(self, "CwLogGroupName",
                  value=cw_log_group.log_group_name,
                  export_name="AgentRunCostTracerCwLogGroup")
        CfnOutput(self, "SpansBucketName",
                  value=spans_bucket.bucket_name,
                  export_name="AgentRunCostTracerSpansBucket")
        CfnOutput(self, "TracerRoleArn",
                  value=tracer_role.role_arn,
                  export_name="AgentRunCostTracerRoleArn")
        CfnOutput(self, "CmkArn",
                  value=cmk.key_arn)
