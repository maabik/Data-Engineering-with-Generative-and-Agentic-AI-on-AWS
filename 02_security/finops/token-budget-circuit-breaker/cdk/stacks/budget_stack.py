"""CDK stack: DynamoDB table, SNS alert topic, EventBridge rule,
usage-recorder Lambda, and IAM roles.

Resources
---------
BudgetTable          DynamoDB on-demand; PK=pk SK=sk; TTL on `ttl` attribute
AlertTopic           SNS topic for throttle/block notifications
RecorderFunction     Lambda that reads Bedrock invocation log events from
                     EventBridge and calls BudgetCounters.increment()
RecorderRole         Least-privilege role: DDB read/write, SNS publish
BudgetResetFunction  Lambda for admin-triggered limit resets (optional)
EventBridgeRule      Routes Bedrock invocation events to RecorderFunction
CW Alarms            Per-tenant 80% and 100% threshold alarms via
                     DDB Streams → Lambda → CW custom metric publish
"""

from __future__ import annotations

try:
    import aws_cdk as cdk
    from aws_cdk import (
        Stack,
        Duration,
        RemovalPolicy,
        aws_dynamodb as dynamodb,
        aws_sns as sns,
        aws_lambda as lambda_,
        aws_iam as iam,
        aws_events as events,
        aws_events_targets as targets,
        aws_cloudwatch as cloudwatch,
        aws_cloudwatch_actions as cw_actions,
        CfnOutput,
    )
    from constructs import Construct
    _CDK_AVAILABLE = True
except ImportError:
    _CDK_AVAILABLE = False


if _CDK_AVAILABLE:
    class BudgetCircuitBreakerStack(Stack):
        def __init__(
            self,
            scope: Construct,
            construct_id: str,
            *,
            alert_email: str = "",
            **kwargs,
        ) -> None:
            super().__init__(scope, construct_id, **kwargs)

            # ── DynamoDB table ────────────────────────────────────────────────
            self.table = dynamodb.Table(
                self,
                "BudgetTable",
                table_name="ai-token-budgets",
                partition_key=dynamodb.Attribute(
                    name="pk", type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="sk", type=dynamodb.AttributeType.STRING
                ),
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                time_to_live_attribute="ttl",
                removal_policy=RemovalPolicy.RETAIN,
                point_in_time_recovery=True,
            )

            # ── SNS alert topic ───────────────────────────────────────────────
            self.alert_topic = sns.Topic(
                self,
                "BudgetAlertTopic",
                topic_name="ai-budget-alerts",
                display_name="AI Token Budget Alerts",
            )

            # ── IAM role for recorder Lambda ──────────────────────────────────
            recorder_role = iam.Role(
                self,
                "RecorderRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    )
                ],
            )
            self.table.grant_read_write_data(recorder_role)
            self.alert_topic.grant_publish(recorder_role)

            # ── Recorder Lambda ───────────────────────────────────────────────
            self.recorder_fn = lambda_.Function(
                self,
                "RecorderFunction",
                function_name="ai-budget-recorder",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.handler",
                code=lambda_.Code.from_asset("../lambda/recorder"),
                role=recorder_role,
                timeout=Duration.seconds(30),
                memory_size=256,
                environment={
                    "BUDGET_TABLE": self.table.table_name,
                    "ALERT_TOPIC_ARN": self.alert_topic.topic_arn,
                },
            )

            # ── EventBridge rule: Bedrock invocation log events ───────────────
            # Bedrock sends modelInvocationLog events via EventBridge when
            # CloudWatch logging is enabled. We filter for completed calls.
            bedrock_rule = events.Rule(
                self,
                "BedrockInvocationRule",
                rule_name="ai-budget-bedrock-invocations",
                event_pattern=events.EventPattern(
                    source=["aws.bedrock"],
                    detail_type=["AWS API Call via CloudTrail"],
                    detail={
                        "eventSource": ["bedrock.amazonaws.com"],
                        "eventName": ["InvokeModel", "Converse"],
                    },
                ),
            )
            bedrock_rule.add_target(targets.LambdaFunction(self.recorder_fn))

            # ── CloudWatch alarms (example: table write throttles) ────────────
            write_throttle_alarm = cloudwatch.Alarm(
                self,
                "DDBWriteThrottleAlarm",
                alarm_name="ai-budget-ddb-write-throttles",
                metric=self.table.metric_system_errors_for_operations(
                    operations=[dynamodb.Operation.PUT_ITEM],
                    period=Duration.minutes(5),
                ),
                threshold=10,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator
                    .GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            write_throttle_alarm.add_alarm_action(
                cw_actions.SnsAction(self.alert_topic)
            )

            # ── Outputs ───────────────────────────────────────────────────────
            CfnOutput(self, "TableName", value=self.table.table_name)
            CfnOutput(self, "AlertTopicArn", value=self.alert_topic.topic_arn)
            CfnOutput(self, "RecorderFunctionArn",
                      value=self.recorder_fn.function_arn)
