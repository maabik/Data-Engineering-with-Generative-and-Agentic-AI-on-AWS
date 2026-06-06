"""KGNeptune — Neptune Analytics graph + IAM + KMS + S3 buckets.

Uses ``CfnGraph`` because L2 constructs for Neptune Analytics aren't yet
in aws-cdk-lib. The data plane is reached with ``execute_query`` against
the ``neptune-graph`` boto3 client (see ``src/knowledge_graph_agent/ingestion/graph_loader.py``).
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_iam as iam,
    aws_kms as kms,
    aws_neptunegraph as neptune_graph,
    aws_s3 as s3,
    aws_ssm as ssm,
)
from constructs import Construct


class NeptuneStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        provisioned_capacity = cdk.CfnParameter(
            self,
            "ProvisionedMemory",
            type="Number",
            default=16,
            description="Neptune Analytics provisioned memory (m-NCU). 16 m-NCU is the smallest size — fine for the demo. Production starts at 128.",
        )
        graph_name = cdk.CfnParameter(
            self,
            "GraphName",
            type="String",
            default="knowledge-graph-agent-graph",
            description="Name for the Neptune Analytics graph",
        )
        vector_dimension = cdk.CfnParameter(
            self,
            "VectorDimension",
            type="Number",
            default=1024,
            description="Dimension of the embedding vectors stored on graph nodes (Nova default: 1024).",
        )

        # ── KMS for at-rest encryption ──────────────────────────────────
        kms_key = kms.Key(
            self,
            "GraphKey",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            description="Encryption key for the GraphRAG knowledge graph",
        )

        # ── S3 buckets ──────────────────────────────────────────────────
        documents_bucket = s3.Bucket(
            self,
            "DocumentsBucket",
            bucket_name=f"kg-docs-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
        )

        # NOTE: S3 Vectors uses its own bucket type provisioned via the
        # ``s3vectors`` API, not a classic S3 bucket. The CDK doesn't yet
        # have an L2 for it, so this stack just allocates a name and
        # writes it to SSM; ``scripts/create_vector_index.py`` creates
        # the actual vector bucket + index after deploy.
        vectors_bucket_name = f"kg-vectors-{self.account}-{self.region}"

        # ── Neptune Analytics graph ─────────────────────────────────────
        # public_connectivity=True so a developer laptop can reach the
        # data plane via SigV4 without setting up VPC endpoints. For
        # production, flip this to False and add a VPC endpoint per the
        # Neptune Analytics docs.
        graph = neptune_graph.CfnGraph(
            self,
            "Graph",
            graph_name=graph_name.value_as_string,
            provisioned_memory=provisioned_capacity.value_as_number,
            public_connectivity=True,
            replica_count=0,
            deletion_protection=False,
            vector_search_configuration=neptune_graph.CfnGraph.VectorSearchConfigurationProperty(
                vector_search_dimension=vector_dimension.value_as_number,
            ),
        )

        graph_arn = cdk.Stack.of(self).format_arn(
            service="neptune-graph",
            resource="graph",
            resource_name=graph.attr_graph_id,
        )

        # ── IAM role for ingestion / agent runtime ──────────────────────
        agent_role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("lambda.amazonaws.com"),
                iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
                iam.ServicePrincipal("states.amazonaws.com"),
            ),
            description="Used by ingestion Lambdas, the MCP runtime, and Step Functions to read and write the KG",
        )

        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "neptune-graph:ReadDataViaQuery",
                    "neptune-graph:WriteDataViaQuery",
                    "neptune-graph:DeleteDataViaQuery",
                    "neptune-graph:GetGraph",
                    "neptune-graph:ListGraphs",
                ],
                resources=[graph_arn],
            )
        )
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/*",
                    f"arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                ],
            )
        )
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                ],
                resources=[
                    documents_bucket.bucket_arn,
                    f"{documents_bucket.bucket_arn}/*",
                ],
            )
        )
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:DeleteVectors",
                    "s3vectors:CreateIndex",
                    "s3vectors:DeleteIndex",
                    "s3vectors:GetIndex",
                ],
                resources=["*"],
            )
        )
        kms_key.grant_encrypt_decrypt(agent_role)
        agent_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # ── SSM Parameter Store entries ─────────────────────────────────
        graph_id_param = ssm.StringParameter(
            self,
            "GraphIdParam",
            parameter_name="/kg/graph_id",
            string_value=graph.attr_graph_id,
            description="Neptune Analytics graph identifier for the KG agent",
        )
        ssm.StringParameter(
            self,
            "DocumentsBucketParam",
            parameter_name="/kg/documents_bucket",
            string_value=documents_bucket.bucket_name,
        )
        ssm.StringParameter(
            self,
            "VectorsBucketParam",
            parameter_name="/kg/vectors_bucket",
            string_value=vectors_bucket_name,
        )
        ssm.StringParameter(
            self,
            "AgentRoleArnParam",
            parameter_name="/kg/agent_role_arn",
            string_value=agent_role.role_arn,
        )

        # Outputs
        cdk.CfnOutput(self, "GraphId", value=graph.attr_graph_id)
        cdk.CfnOutput(self, "GraphArn", value=graph_arn)
        cdk.CfnOutput(self, "DocumentsBucketName", value=documents_bucket.bucket_name)
        cdk.CfnOutput(self, "VectorsBucketName", value=vectors_bucket_name)
        cdk.CfnOutput(self, "AgentRoleArn", value=agent_role.role_arn)

        # Public attributes for cross-stack wiring
        self.graph = graph
        self.graph_arn = graph_arn
        self.kms_key = kms_key
        self.agent_role = agent_role
        self.documents_bucket = documents_bucket
        self.vectors_bucket_name = vectors_bucket_name
        self.graph_id_param_name = graph_id_param.parameter_name
