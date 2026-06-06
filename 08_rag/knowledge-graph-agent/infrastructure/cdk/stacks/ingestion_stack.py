"""KGIngestion — Step Functions + container-image Lambda.

The Lambda is a Docker image that bakes in the ``knowledge_graph_agent`` package
plus its runtime dependencies (pydantic, tenacity, boto3). The handler
delegates to ``knowledge_graph_agent.ingestion.batch_loader.process_document``, so
ingest from Step Functions and ingest from a laptop run identical code.

Workflow:

    Step Functions (Standard) Map (concurrency=4)
        → Lambda(handler) per document
            → extract entities/relationships
            → embed
            → load to Neptune + S3 Vectors

The state machine input is::

    {"documents": [{"s3_uri": "s3://...", "document_type": "...", "title": "..."}]}
"""

from __future__ import annotations

import shutil
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


def _stage_lambda_package(stack_dir: Path) -> Path:
    """Copy the knowledge_graph_agent source tree into the Lambda build dir.

    Idempotent — wipes the staged copy on each synth so a stale package
    can never make it into a deployed image.
    """
    project_root = stack_dir.parent.parent.parent  # …/knowledge-graph-agent
    lambda_dir = stack_dir.parent / "lambda_extraction"
    src_pkg = project_root / "src" / "knowledge_graph_agent"
    staged_pkg = lambda_dir / "knowledge_graph_agent"
    if staged_pkg.exists():
        shutil.rmtree(staged_pkg)
    shutil.copytree(
        src_pkg,
        staged_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return lambda_dir


class IngestionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        graph_arn: str,
        documents_bucket_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_dir = _stage_lambda_package(Path(__file__).resolve().parent)

        extraction_log_group = logs.LogGroup(
            self,
            "ExtractionLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        extraction_fn = lambda_.DockerImageFunction(
            self,
            "ExtractionFn",
            code=lambda_.DockerImageCode.from_image_asset(
                str(lambda_dir),
                # Build the Lambda image for the same architecture the
                # function will run on. Default is x86_64; setting both
                # ``platform`` (build-time) and ``architecture`` (runtime)
                # to arm64 avoids the silent ProcessSpawnFailed you get
                # when an arm64 image runs on an x86_64 Lambda.
                platform=cdk.aws_ecr_assets.Platform.LINUX_ARM64,
            ),
            architecture=lambda_.Architecture.ARM_64,
            timeout=Duration.minutes(15),
            memory_size=2048,
            log_group=extraction_log_group,
            environment={
                "KG_REGION": self.region,
                "KG_DOCUMENTS_BUCKET": documents_bucket_name,
                # Graph + vector bucket come from /kg/* SSM parameters
                # at runtime via the knowledge_graph_agent package's settings hook.
            },
        )

        # The Lambda needs to read its own knowledge_graph_agent settings, which read
        # SSM. Granting ssm:GetParameter on the /kg/* prefix is enough.
        extraction_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/kg/*"],
            )
        )
        extraction_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "neptune-graph:ReadDataViaQuery",
                    "neptune-graph:WriteDataViaQuery",
                    # MERGE ... SET inside openCypher exercises Delete
                    # authority on overwrites — counterintuitive but
                    # documented in the Neptune Analytics IAM reference.
                    "neptune-graph:DeleteDataViaQuery",
                    "neptune-graph:GetGraph",
                ],
                resources=[graph_arn],
            )
        )
        extraction_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:Converse"],
                resources=["*"],
            )
        )
        extraction_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:ListBucket",
                ],
                resources=[
                    f"arn:aws:s3:::{documents_bucket_name}",
                    f"arn:aws:s3:::{documents_bucket_name}/*",
                ],
            )
        )
        # The documents bucket is KMS-encrypted with the KGNeptune
        # customer-managed key. Granting Decrypt on the entire account's
        # KMS keys is too broad; in production, pass the key ARN through
        # from the Neptune stack and scope this statement to that ARN.
        extraction_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["kms:Decrypt", "kms:DescribeKey"],
                resources=[f"arn:aws:kms:{self.region}:{self.account}:key/*"],
            )
        )
        extraction_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:GetIndex",
                    "s3vectors:ListVectors",
                ],
                resources=["*"],
            )
        )

        # Express Step Functions workflow.
        per_doc_state = (
            tasks.LambdaInvoke(
                self,
                "ExtractAndLoad",
                lambda_function=extraction_fn,
                payload_response_only=True,
                retry_on_service_exceptions=True,
            )
            .add_retry(
                errors=["Lambda.TooManyRequestsException", "ThrottlingException"],
                interval=Duration.seconds(2),
                backoff_rate=2.0,
                max_attempts=3,
            )
        )

        # Lower concurrency than the spec's 8 — Bedrock per-account
        # token-per-minute caps make 4 a safer default; raise once you
        # know your quota.
        per_doc_map = sfn.Map(
            self,
            "PerDocument",
            max_concurrency=4,
            items_path=sfn.JsonPath.string_at("$.documents"),
        ).item_processor(per_doc_state)

        workflow = sfn.StateMachine(
            self,
            "ExtractionWorkflow",
            definition_body=sfn.DefinitionBody.from_chainable(per_doc_map),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.hours(2),
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=logs.LogGroup(
                    self,
                    "WorkflowLogs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
                level=sfn.LogLevel.ALL,
            ),
        )

        cdk.CfnOutput(self, "ExtractionStateMachineArn", value=workflow.state_machine_arn)
        cdk.CfnOutput(self, "ExtractionFunctionName", value=extraction_fn.function_name)

        self.workflow = workflow
        self.extraction_fn = extraction_fn
