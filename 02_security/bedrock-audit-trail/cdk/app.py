#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.audit_trail_stack import BedrockAuditTrailStack

app = cdk.App()
BedrockAuditTrailStack(
    app, "BedrockAuditTrail",
    description="Governance-grade Bedrock invocation audit trail (Chapter 2/3/11)",
)
app.synth()
