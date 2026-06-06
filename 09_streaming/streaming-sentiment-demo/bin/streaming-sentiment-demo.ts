#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { StreamingSentimentDemoPythonStack } from '../lib/streaming-sentiment-demo-stack';
import { StreamingSentimentFlinkStack } from '../lib/streaming-sentiment-flink-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

new StreamingSentimentDemoPythonStack(app, 'StreamingSentimentDemoPythonStack', {
  env,
  description: 'Streaming social media sentiment analysis with Kinesis + Bedrock (Lambda)',
});

// Opt-in MSF/Flink stack. Build the jar with `mvn package` first, then either
// `cdk deploy --context deployFlink=true StreamingSentimentFlinkStack`
// or set DEPLOY_FLINK=1 in your shell.
const deployFlink =
  app.node.tryGetContext('deployFlink') === 'true' || process.env.DEPLOY_FLINK === '1';
if (deployFlink) {
  new StreamingSentimentFlinkStack(app, 'StreamingSentimentFlinkStack', {
    env,
    description: 'Sentiment analysis on Managed Service for Apache Flink (Flink 2.x)',
  });
}
