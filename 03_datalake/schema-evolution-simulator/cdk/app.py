#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.schema_sim_stack import SchemaSimStack

app = cdk.App()
SchemaSimStack(app, "SchemaSimStack")
app.synth()
