import * as cdk from "aws-cdk-lib";
import * as glue from "aws-cdk-lib/aws-glue";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";

export interface OrchestrationStackProps extends cdk.StackProps {
  namePrefix: string;
  ingestJob: glue.CfnJob;
  curateJob: glue.CfnJob;
  dqJob: glue.CfnJob;
  tags: Record<string, string>;
}

export class OrchestrationStack extends cdk.Stack {
  public readonly stateMachine: sfn.StateMachine;

  constructor(scope: Construct, id: string, props: OrchestrationStackProps) {
    super(scope, id, props);
    Object.entries(props.tags).forEach(([k, v]) => cdk.Tags.of(this).add(k, v));

    const startJob = (jobName: string, name: string) =>
      new tasks.GlueStartJobRun(this, name, {
        glueJobName: jobName,
        integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      });

    const definition = startJob(props.ingestJob.name!, "Ingest")
      .next(startJob(props.curateJob.name!, "Curate"))
      .next(startJob(props.dqJob.name!, "DataQuality"));

    this.stateMachine = new sfn.StateMachine(this, "pipeline", {
      stateMachineName: `${props.namePrefix}-orders-pipeline`,
      definition,
    });

    [props.ingestJob, props.curateJob, props.dqJob].forEach((j) =>
      this.stateMachine.role.addToPrincipalPolicy(
        new iam.PolicyStatement({
          actions: ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"],
          resources: ["*"],
        })
      )
    );
  }
}
