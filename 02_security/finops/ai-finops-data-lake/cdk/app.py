#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.finops_lake_stack import AIFinOpsLakeStack

app = cdk.App()
AIFinOpsLakeStack(
    app, "AIFinOpsLake",
    description="AI FinOps data lake: unified Bedrock + CUR + AgentCore Iceberg substrate",
)
app.synth()
