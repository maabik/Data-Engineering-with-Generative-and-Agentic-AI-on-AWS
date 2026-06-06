#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.medallion_stack import MedallionStack

app = cdk.App()
MedallionStack(app, "ProgressiveMedallionStack")
app.synth()
