#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.budget_stack import BudgetCircuitBreakerStack

app = cdk.App()
BudgetCircuitBreakerStack(
    app, "BudgetCircuitBreaker",
    description="AI token/dollar budget enforcement: DynamoDB counters + SNS alerts",
)
app.synth()
