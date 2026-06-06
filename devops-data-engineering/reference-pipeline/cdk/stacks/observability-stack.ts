import * as cdk from "aws-cdk-lib";
import * as cw from "aws-cdk-lib/aws-cloudwatch";
import * as glue from "aws-cdk-lib/aws-glue";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import { Construct } from "constructs";

export interface ObservabilityStackProps extends cdk.StackProps {
  namePrefix: string;
  stateMachine: sfn.StateMachine;
  glueJobs: glue.CfnJob[];
  tags: Record<string, string>;
}

export class ObservabilityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    Object.entries(props.tags).forEach(([k, v]) => cdk.Tags.of(this).add(k, v));

    new logs.LogGroup(this, "log-group", {
      logGroupName: `/aws/data-pipeline/${props.namePrefix}`,
      retention: logs.RetentionDays.TWO_WEEKS,
    });

    const dashboard = new cw.Dashboard(this, "dashboard", {
      dashboardName: `${props.namePrefix}-orders-pipeline`,
    });
    dashboard.addWidgets(
      new cw.GraphWidget({
        title: "Step Functions executions",
        left: [
          props.stateMachine.metricSucceeded(),
          props.stateMachine.metricFailed(),
          props.stateMachine.metricTimedOut(),
        ],
      })
    );

    new cw.Alarm(this, "pipeline-failed", {
      metric: props.stateMachine.metricFailed({ period: cdk.Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    });
  }
}
