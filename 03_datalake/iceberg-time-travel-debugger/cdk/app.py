#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.iceberg_debugger_stack import IcebergDebuggerStack

app = cdk.App()
IcebergDebuggerStack(app, "IcebergDebuggerStack")
app.synth()
