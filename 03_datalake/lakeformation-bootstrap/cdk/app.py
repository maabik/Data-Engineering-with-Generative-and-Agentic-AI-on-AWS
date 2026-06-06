#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.lakeformation_bootstrap_stack import LakeFormationBootstrapStack

app = cdk.App()

consumer_account_id = app.node.try_get_context("consumerAccountId") or ""

LakeFormationBootstrapStack(
    app,
    "LakeFormationBootstrapStack",
    consumer_account_id=consumer_account_id.strip() or None,
)

app.synth()
