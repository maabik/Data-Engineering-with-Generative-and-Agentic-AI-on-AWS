#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.router_eval_stack import ModelCascadeRouterEvalStack

app = cdk.App()
ModelCascadeRouterEvalStack(
    app, "ModelCascadeRouterEval",
    description="S3 + IAM infrastructure for Bedrock Model Evaluation (EvalHarness.run_managed)",
)
app.synth()
