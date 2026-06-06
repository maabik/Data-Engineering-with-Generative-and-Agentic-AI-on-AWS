#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.lakeformation_filters_stack import LakeFormationFiltersStack

app = cdk.App()
LakeFormationFiltersStack(app, "LakeFormationFiltersStack")
app.synth()
