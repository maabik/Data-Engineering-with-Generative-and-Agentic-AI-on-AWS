#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.tracer_stack import AgentRunCostTracerStack

app = cdk.App()
AgentRunCostTracerStack(
    app, "AgentRunCostTracer",
    description="Kinesis Firehose + S3 + CloudWatch emit infrastructure for RunTracer",
)
app.synth()
