"""KGObservability — CloudWatch dashboards.

Surfaces the metrics the book repeatedly recommends watching:

  - Bedrock token usage (extraction + agent)
  - Neptune Analytics query latency / error rate
  - AgentCore Runtime invocations + latency
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cw,
)
from constructs import Construct


class ObservabilityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        graph_id_param: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        dashboard = cw.Dashboard(
            self,
            "Dashboard",
            dashboard_name="knowledge-graph-agent",
            period_override=cw.PeriodOverride.AUTO,
        )

        bedrock_tokens = cw.Metric(
            namespace="AWS/Bedrock",
            metric_name="InputTokenCount",
            statistic="Sum",
            period=Duration.minutes(5),
        )
        bedrock_output = cw.Metric(
            namespace="AWS/Bedrock",
            metric_name="OutputTokenCount",
            statistic="Sum",
            period=Duration.minutes(5),
        )
        bedrock_invocations = cw.Metric(
            namespace="AWS/Bedrock",
            metric_name="Invocations",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        graph_query_latency = cw.Metric(
            namespace="AWS/Neptune",
            metric_name="QueryDuration",
            statistic="Average",
            period=Duration.minutes(5),
        )
        graph_query_errors = cw.Metric(
            namespace="AWS/Neptune",
            metric_name="GremlinErrors",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        dashboard.add_widgets(
            cw.GraphWidget(
                title="Bedrock token usage",
                left=[bedrock_tokens, bedrock_output],
                width=12,
            ),
            cw.GraphWidget(
                title="Bedrock invocations",
                left=[bedrock_invocations],
                width=12,
            ),
            cw.GraphWidget(
                title="Neptune Analytics latency",
                left=[graph_query_latency],
                right=[graph_query_errors],
                width=24,
            ),
        )

        cdk.CfnOutput(self, "DashboardName", value="knowledge-graph-agent")
        cdk.CfnOutput(self, "GraphIdParam", value=graph_id_param)
