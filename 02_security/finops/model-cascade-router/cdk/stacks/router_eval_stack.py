"""CDK stack: ModelCascadeRouterEval

Provisions the infrastructure required to run Bedrock Model Evaluation
(managed async eval jobs) from the EvalHarness.run_managed() path:

  - KMS CMK for all storage
  - S3 eval bucket  (input dataset JSONL + output results)
  - IAM eval role   (trusted by bedrock.amazonaws.com)
      • S3 read/write on the eval bucket
      • bedrock:InvokeModel / Converse on all models
      • bedrock:CreateEvaluationJob / GetEvaluationJob / ListEvaluationJobs

The bucket name and role ARN are emitted as CloudFormation Outputs
so EvalHarness can be configured without hard-coding resource names.
"""

from __future__ import annotations

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_iam as iam,
    aws_kms as kms,
    aws_s3 as s3,
)
from constructs import Construct


class ModelCascadeRouterEvalStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── KMS CMK ───────────────────────────────────────────────────────────
        cmk = kms.Key(
            self, "EvalCmk",
            description="CMK for ModelCascadeRouter eval bucket",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        cmk.add_alias("alias/model-cascade-router-eval-key")

        # ── S3 eval bucket ────────────────────────────────────────────────────
        eval_bucket = s3.Bucket(
            self, "EvalBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=cmk,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireEvalResults",
                    expiration=Duration.days(90),
                ),
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── IAM eval role (assumed by Bedrock service) ────────────────────────
        eval_role = iam.Role(
            self, "EvalRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Allows Bedrock Model Evaluation to read datasets and write results",
        )
        eval_bucket.grant_read_write(eval_role)
        cmk.grant_encrypt_decrypt(eval_role)

        # Bedrock needs to invoke models during the evaluation
        eval_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
            ],
            resources=["*"],
        ))

        # ── IAM caller role (for the harness process itself) ──────────────────
        # Least-privilege: submit + query eval jobs, upload dataset, read results
        caller_role = iam.Role(
            self, "CallerRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="Allows EvalHarness to submit Bedrock eval jobs",
        )
        eval_bucket.grant_read_write(caller_role)
        cmk.grant_encrypt_decrypt(caller_role)
        caller_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:CreateEvaluationJob",
                "bedrock:GetEvaluationJob",
                "bedrock:ListEvaluationJobs",
                "bedrock:StopEvaluationJob",
            ],
            resources=["*"],
        ))
        # Caller must be able to pass the eval role to Bedrock
        caller_role.add_to_policy(iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[eval_role.role_arn],
        ))

        # ── Outputs ───────────────────────────────────────────────────────────
        CfnOutput(self, "EvalBucketName",
                  value=eval_bucket.bucket_name,
                  export_name="ModelCascadeRouterEvalBucket")
        CfnOutput(self, "EvalRoleArn",
                  value=eval_role.role_arn,
                  export_name="ModelCascadeRouterEvalRoleArn")
        CfnOutput(self, "CallerRoleArn",
                  value=caller_role.role_arn,
                  export_name="ModelCascadeRouterCallerRoleArn")
        CfnOutput(self, "CmkArn",
                  value=cmk.key_arn)
