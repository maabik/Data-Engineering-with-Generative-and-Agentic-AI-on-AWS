import * as cdk from "aws-cdk-lib";
import { GluePipelineStack } from "../stacks/glue-pipeline-stack";
import { OrchestrationStack } from "../stacks/orchestration-stack";
import { ObservabilityStack } from "../stacks/observability-stack";

const app = new cdk.App();

const env = app.node.tryGetContext("env") ?? "sandbox";
const owner = app.node.tryGetContext("owner") ?? process.env.USER ?? "unknown";
const namePrefix = env === "prod" ? "prod" : `${owner}-ref`;

const tags: Record<string, string> = {
  Project: "devops-data-engineering",
  Environment: env,
  Owner: owner,
  ManagedBy: "cdk",
};

const pipeline = new GluePipelineStack(app, `${namePrefix}-glue`, {
  namePrefix,
  glueWorkers: env === "prod" ? 10 : 2,
  rawRetentionDays: env === "prod" ? 90 : 7,
  tags,
});

const orchestration = new OrchestrationStack(app, `${namePrefix}-sfn`, {
  namePrefix,
  ingestJob: pipeline.ingestJob,
  curateJob: pipeline.curateJob,
  dqJob: pipeline.dqJob,
  tags,
});

new ObservabilityStack(app, `${namePrefix}-obs`, {
  namePrefix,
  stateMachine: orchestration.stateMachine,
  glueJobs: [pipeline.ingestJob, pipeline.curateJob, pipeline.dqJob],
  tags,
});
