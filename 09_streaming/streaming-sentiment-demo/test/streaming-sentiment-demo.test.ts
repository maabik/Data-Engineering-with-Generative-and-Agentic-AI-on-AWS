import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { StreamingSentimentDemoPythonStack } from '../lib/streaming-sentiment-demo-stack';

function synth() {
  const app = new cdk.App();
  const stack = new StreamingSentimentDemoPythonStack(app, 'TestStack');
  return Template.fromStack(stack);
}

test('Kinesis stream is provisioned with the expected name', () => {
  const template = synth();
  template.hasResourceProperties('AWS::Kinesis::Stream', {
    Name: 'social-media-posts-stream',
    ShardCount: 1,
  });
});

test('Both Lambdas use Python 3.12', () => {
  const template = synth();
  const fns = template.findResources('AWS::Lambda::Function', {
    Properties: { Runtime: 'python3.12' },
  });
  // Two function-typed resources we own + the bucket auto-delete custom resource
  // Lambda is a Node runtime, so this asserts at least the two we authored.
  expect(Object.keys(fns).length).toBeGreaterThanOrEqual(2);
});

test('Processor Lambda has a DLQ and Kinesis event source', () => {
  const template = synth();
  template.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
    StartingPosition: 'LATEST',
    BisectBatchOnFunctionError: true,
  });
  template.resourceCountIs('AWS::SQS::Queue', 1);
});

test('Sentiment Lambda IAM is scoped to the Bedrock model ARN, not *', () => {
  const template = synth();
  const rendered = JSON.stringify(template.toJSON());
  // Foundation-model ARN should appear and the policy must not allow bedrock on "*".
  expect(rendered).toContain('foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0');
  expect(rendered).not.toMatch(/"bedrock:InvokeModel"[^}]*"Resource":\s*"\*"/);
});

test('Sentiment crisis alarm exists', () => {
  const template = synth();
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    MetricName: 'AverageSentiment',
    ComparisonOperator: 'LessThanThreshold',
    Threshold: 2.5,
  });
});
