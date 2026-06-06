import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sqs from 'aws-cdk-lib/aws-sqs';

const KINESIS_STREAM_NAME = 'social-media-posts-stream';
// Claude 3.5 Haiku — current as of 2026; cheaper and faster than 3 Haiku.
const BEDROCK_MODEL_ID = 'anthropic.claude-3-5-haiku-20241022-v1:0';
// Bedrock cross-region inference profile for Claude 3.5 Haiku in us-east-1.
const BEDROCK_INFERENCE_PROFILE = 'us.anthropic.claude-3-5-haiku-20241022-v1:0';

export class StreamingSentimentDemoPythonStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const sentimentBucket = new s3.Bucket(this, 'SentimentS3TablesBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'expire-old-sentiment-data',
          enabled: true,
          expiration: cdk.Duration.days(90),
        },
      ],
    });

    const stream = new kinesis.Stream(this, 'SocialMediaPostsStream', {
      streamName: KINESIS_STREAM_NAME,
      shardCount: 1,
      retentionPeriod: cdk.Duration.hours(24),
    });

    const dlq = new sqs.Queue(this, 'ProcessorDLQ', {
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    const sentimentFn = new lambda.Function(this, 'SentimentAnalysisFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      code: lambda.Code.fromAsset('lambda/sentiment-analysis'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        MODEL_ID: BEDROCK_MODEL_ID,
        BEDROCK_INFERENCE_PROFILE_ID: BEDROCK_INFERENCE_PROFILE,
        BEDROCK_REGION: this.region,
      },
    });

    const bedrockModelArn = `arn:aws:bedrock:${this.region}::foundation-model/${BEDROCK_MODEL_ID}`;
    const bedrockProfileArn = `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${BEDROCK_INFERENCE_PROFILE}`;
    sentimentFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [bedrockModelArn, bedrockProfileArn],
      }),
    );

    const processorFn = new lambda.Function(this, 'KinesisProcessorFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      code: lambda.Code.fromAsset('lambda/processor'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        SENTIMENT_FUNCTION_NAME: sentimentFn.functionName,
        S3_BUCKET_NAME: sentimentBucket.bucketName,
      },
      deadLetterQueueEnabled: true,
      deadLetterQueue: dlq,
    });

    sentimentFn.grantInvoke(processorFn);
    sentimentBucket.grantWrite(processorFn);

    processorFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: {
          StringEquals: { 'cloudwatch:namespace': 'SocialMediaSentiment' },
        },
      }),
    );

    processorFn.addEventSource(
      new lambdaEventSources.KinesisEventSource(stream, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        maxBatchingWindow: cdk.Duration.seconds(5),
        bisectBatchOnError: true,
        retryAttempts: 3,
        onFailure: new lambdaEventSources.SqsDlq(dlq),
      }),
    );

    this.buildDashboardAndAlarms({ stream, processorFn, sentimentFn });

    new cdk.CfnOutput(this, 'S3TablesBucketName', { value: sentimentBucket.bucketName });
    new cdk.CfnOutput(this, 'ProcessorLambdaName', { value: processorFn.functionName });
    new cdk.CfnOutput(this, 'SentimentAnalysisFunctionName', {
      value: sentimentFn.functionName,
    });
    new cdk.CfnOutput(this, 'KinesisStreamName', { value: stream.streamName });
    new cdk.CfnOutput(this, 'DLQUrl', { value: dlq.queueUrl });
  }

  private buildDashboardAndAlarms(args: {
    stream: kinesis.Stream;
    processorFn: lambda.Function;
    sentimentFn: lambda.Function;
  }) {
    const { stream, processorFn, sentimentFn } = args;

    const ns = 'SocialMediaSentiment';
    const period = cdk.Duration.minutes(1);

    const avgSentiment = new cloudwatch.Metric({
      namespace: ns,
      metricName: 'AverageSentiment',
      statistic: 'Average',
      period,
    });
    const postCount = new cloudwatch.Metric({
      namespace: ns,
      metricName: 'PostCount',
      statistic: 'Sum',
      period,
    });
    const sentimentBuckets = [1, 2, 3, 4, 5].map(
      (n) =>
        new cloudwatch.Metric({
          namespace: ns,
          metricName: `SentimentScore${n}`,
          statistic: 'Sum',
          period,
        }),
    );
    const platforms = ['Twitter', 'Facebook', 'Instagram', 'LinkedIn'].map(
      (p) =>
        new cloudwatch.Metric({
          namespace: ns,
          metricName: `Platform_${p}`,
          statistic: 'Sum',
          period,
        }),
    );
    const iteratorAge = new cloudwatch.Metric({
      namespace: 'AWS/Kinesis',
      metricName: 'GetRecords.IteratorAgeMilliseconds',
      dimensionsMap: { StreamName: stream.streamName },
      statistic: 'Maximum',
      period,
    });

    new cloudwatch.Alarm(this, 'ProcessorErrorsAlarm', {
      metric: processorFn.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Processor Lambda errors',
    });
    new cloudwatch.Alarm(this, 'SentimentErrorsAlarm', {
      metric: sentimentFn.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Sentiment Lambda errors (often Bedrock throttling)',
    });
    new cloudwatch.Alarm(this, 'KinesisIteratorAgeAlarm', {
      metric: iteratorAge,
      threshold: 60_000,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Stream consumer falling > 60s behind',
    });
    // Negative-sentiment / "PR crisis" alarm — the demo's marquee teaching moment.
    new cloudwatch.Alarm(this, 'NegativeSentimentAlarm', {
      metric: avgSentiment.with({ period: cdk.Duration.minutes(5), statistic: 'Average' }),
      threshold: 2.5,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Avg sentiment < 2.5 over 10 min — possible PR crisis',
    });

    new cloudwatch.Dashboard(this, 'SentimentDashboard', {
      dashboardName: 'SocialMediaSentimentDashboard',
      widgets: [
        [
          new cloudwatch.GraphWidget({
            title: 'Average sentiment (1-5)',
            left: [avgSentiment],
            width: 12,
            leftYAxis: { min: 1, max: 5 },
          }),
          new cloudwatch.GraphWidget({
            title: 'Post volume',
            left: [postCount],
            width: 12,
          }),
        ],
        [
          new cloudwatch.GraphWidget({
            title: 'Sentiment score distribution',
            left: sentimentBuckets,
            stacked: true,
            width: 24,
          }),
        ],
        [
          new cloudwatch.GraphWidget({
            title: 'Posts by platform',
            left: platforms,
            stacked: true,
            width: 12,
          }),
          new cloudwatch.GraphWidget({
            title: 'Lambda health',
            left: [
              processorFn.metricInvocations(),
              processorFn.metricErrors(),
              sentimentFn.metricErrors(),
            ],
            right: [iteratorAge],
            width: 12,
          }),
        ],
      ],
    });
  }
}
