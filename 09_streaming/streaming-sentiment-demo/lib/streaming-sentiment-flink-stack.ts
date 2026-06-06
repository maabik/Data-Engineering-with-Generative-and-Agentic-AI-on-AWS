import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import {
  CfnApplication,
  CfnApplicationCloudWatchLoggingOption,
} from 'aws-cdk-lib/aws-kinesisanalyticsv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3Assets from 'aws-cdk-lib/aws-s3-assets';
import * as fs from 'fs';
import * as path from 'path';

const KINESIS_STREAM_NAME = 'social-media-posts-stream';
const BEDROCK_MODEL_ID = 'us.anthropic.claude-3-5-haiku-20241022-v1:0';
const FLINK_RUNTIME = 'FLINK-2_0';
const JAR_PATH = path.join(__dirname, '..', 'target', 'social-media-sentiment-flink.jar');

/**
 * Alternate pipeline: Kinesis → Managed Service for Apache Flink (MSF) → S3.
 *
 * Sits next to the Lambda-based StreamingSentimentDemoPythonStack as a
 * higher-throughput option once the demo outgrows per-record Lambda invocation.
 * Run `mvn package` first so target/social-media-sentiment-flink.jar is fresh
 * before `cdk deploy` — the stack uploads it as an asset.
 */
export class StreamingSentimentFlinkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    if (!fs.existsSync(JAR_PATH)) {
      throw new Error(
        `Flink job jar not found at ${JAR_PATH}. Run 'mvn package' before deploying ` +
          `the StreamingSentimentFlinkStack. The Lambda-based stack does not require this.`,
      );
    }

    // The Lambda stack already creates this stream; the Flink stack runs
    // alongside it, so we look it up by name instead of recreating it.
    const streamArn = `arn:aws:kinesis:${this.region}:${this.account}:stream/${KINESIS_STREAM_NAME}`;
    const stream = kinesis.Stream.fromStreamArn(this, 'PostsStream', streamArn);

    const outputBucket = new s3.Bucket(this, 'FlinkSentimentOutputBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'expire-flink-output',
          enabled: true,
          expiration: cdk.Duration.days(90),
        },
      ],
    });

    const jarAsset = new s3Assets.Asset(this, 'FlinkJobJar', {
      path: JAR_PATH,
    });

    const logGroup = new logs.LogGroup(this, 'FlinkLogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const logStream = new logs.LogStream(this, 'FlinkLogStream', {
      logGroup,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const flinkRole = new iam.Role(this, 'FlinkServiceRole', {
      assumedBy: new iam.ServicePrincipal('kinesisanalytics.amazonaws.com'),
      description: 'MSF service role for the social-media sentiment Flink job',
    });

    jarAsset.grantRead(flinkRole);

    flinkRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'kinesis:DescribeStream',
          'kinesis:DescribeStreamSummary',
          'kinesis:GetRecords',
          'kinesis:GetShardIterator',
          'kinesis:ListShards',
          'kinesis:SubscribeToShard',
        ],
        resources: [stream.streamArn],
      }),
    );

    outputBucket.grantReadWrite(flinkRole);

    flinkRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0`,
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${BEDROCK_MODEL_ID}`,
        ],
      }),
    );

    flinkRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['logs:DescribeLogGroups', 'logs:DescribeLogStreams', 'logs:PutLogEvents'],
        resources: [logGroup.logGroupArn, `${logGroup.logGroupArn}:*`],
      }),
    );
    flinkRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: { StringEquals: { 'cloudwatch:namespace': 'AWS/KinesisAnalytics' } },
      }),
    );

    const application = new CfnApplication(this, 'SentimentFlinkApp', {
      applicationName: 'social-media-sentiment',
      runtimeEnvironment: FLINK_RUNTIME,
      serviceExecutionRole: flinkRole.roleArn,
      applicationConfiguration: {
        applicationCodeConfiguration: {
          codeContent: {
            s3ContentLocation: {
              bucketArn: `arn:aws:s3:::${jarAsset.s3BucketName}`,
              fileKey: jarAsset.s3ObjectKey,
            },
          },
          codeContentType: 'ZIPFILE',
        },
        environmentProperties: {
          propertyGroups: [
            {
              propertyGroupId: 'FlinkApplicationProperties',
              propertyMap: {
                'input.stream.arn': stream.streamArn,
                'aws.region': this.region,
                'output.s3.bucket': outputBucket.bucketName,
                'output.s3.prefix': 'sentiment/',
                'bedrock.model.id': BEDROCK_MODEL_ID,
              },
            },
          ],
        },
        flinkApplicationConfiguration: {
          checkpointConfiguration: {
            configurationType: 'CUSTOM',
            checkpointingEnabled: true,
            checkpointInterval: 60_000,
            minPauseBetweenCheckpoints: 5_000,
          },
          monitoringConfiguration: {
            configurationType: 'CUSTOM',
            metricsLevel: 'APPLICATION',
            logLevel: 'INFO',
          },
          parallelismConfiguration: {
            configurationType: 'CUSTOM',
            parallelism: 1,
            parallelismPerKpu: 1,
            autoScalingEnabled: false,
          },
        },
      },
    });

    const cwLogConfig = new CfnApplicationCloudWatchLoggingOption(
      this,
      'FlinkAppCwLogging',
      {
        applicationName: application.ref,
        cloudWatchLoggingOption: {
          logStreamArn: `arn:aws:logs:${this.region}:${this.account}:log-group:${logGroup.logGroupName}:log-stream:${logStream.logStreamName}`,
        },
      },
    );
    cwLogConfig.addDependency(application);

    new cloudwatch.Alarm(this, 'FlinkAppDownAlarm', {
      metric: new cloudwatch.Metric({
        namespace: 'AWS/KinesisAnalytics',
        metricName: 'uptime',
        dimensionsMap: { Application: application.ref },
        statistic: 'Minimum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 1,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.BREACHING,
      alarmDescription: 'MSF application has been down for >10 minutes',
    });

    new cdk.CfnOutput(this, 'FlinkApplicationName', { value: application.ref });
    new cdk.CfnOutput(this, 'FlinkOutputBucket', { value: outputBucket.bucketName });
    new cdk.CfnOutput(this, 'FlinkLogGroupName', { value: logGroup.logGroupName });
  }
}
