# Streaming Social Media Sentiment Analysis Demo

This project demonstrates a real-time streaming data pipeline that analyzes sentiment of social media posts using AWS services and generative AI. The solution ingests simulated social media posts, processes them through a streaming pipeline, and uses Amazon Bedrock's Claude 3 Haiku model to perform sentiment analysis.

## Architecture Overview

Two pipelines are available — pick whichever fits the workload:

**1. Lambda pipeline (default)** — simple to operate, scales to demo volumes:

```
Posts → Kinesis → Processor Lambda → Sentiment Lambda → S3
                         ↓                  ↓
                  CloudWatch Metrics    Bedrock (Claude 3.5 Haiku)
```

**2. Managed Service for Apache Flink (opt-in)** — the same pipeline, but the
processor and sentiment-analysis hop run inside a single Flink 2.x job using
Async I/O against Bedrock and a checkpointed S3 file sink. Use this when
per-record Lambda invocation gets expensive or you need exactly-once delivery.

```
Posts → Kinesis → Flink AsyncFunction (Bedrock) → S3 FileSink
                       ↑
            Flink 2.0 job on MSF, checkpointed every 60s
```

## Key Components

### AWS Services Used
- **Amazon Kinesis Data Streams**: Real-time data ingestion
- **AWS Lambda**: Serverless compute for stream processing and sentiment analysis
- **Amazon Bedrock**: Generative AI service with Claude 3 Haiku model
- **Amazon S3**: Data lake storage with partitioning
- **Amazon CloudWatch**: Monitoring and metrics visualization

### Lambda Functions
1. **Sentiment Analysis Lambda** (`lambda/sentiment-analysis/`): Uses Bedrock to analyze sentiment and score posts 1-5
2. **Processor Lambda** (`lambda/processor/`): Orchestrates the pipeline, processes Kinesis records, and stores results

### Data Flow
1. Social media posts are ingested into Kinesis Data Stream
2. Processor Lambda reads from the stream in batches
3. Each post is sent to the Sentiment Analysis Lambda
4. Bedrock analyzes sentiment and returns a score (1=very negative, 5=very positive)
5. Enriched data is stored in S3 with time-based partitioning
6. Metrics are published to CloudWatch for monitoring

## Prerequisites

- AWS CLI configured with appropriate permissions
- Node.js 18+ and npm installed
- AWS CDK v2 installed (`npm install -g aws-cdk`)
- Python 3.9+ for running simulation scripts
- Access to Amazon Bedrock (Claude 3 Haiku model)

### Required AWS Permissions
- Kinesis Data Streams (read/write)
- Lambda (create, invoke, manage)
- S3 (create buckets, read/write objects)
- Bedrock (invoke model)
- CloudWatch (put metrics, create dashboards)
- IAM (create roles and policies)

## Quick Start

### 1. Deploy the Infrastructure

```bash
cd streaming-sentiment-demo
./deploy.sh        # installs deps, bootstraps, deploys, writes outputs.json
```

The Kinesis stream, both Lambdas, the S3 bucket, the DLQ, the CloudWatch
dashboard, and the alarms (including the avg-sentiment crisis alarm) are all
provisioned by the default CDK stack (`StreamingSentimentDemoPythonStack`).

#### Optional: deploy the Flink (MSF) stack

The Flink job source is in `src/main/java/com/example/`. To run the alternate
pipeline alongside the Lambda one:

```bash
# 1. Build the shaded jar (requires JDK 17 + Maven).
mvn package

# 2. Synth/deploy the second stack — opt in via context flag or env var.
DEPLOY_FLINK=1 npx cdk deploy StreamingSentimentFlinkStack
# or
npx cdk deploy --context deployFlink=true StreamingSentimentFlinkStack
```

The Flink stack reuses the Kinesis stream from the Lambda stack and writes
enriched JSON records to its own S3 bucket. It uses Bedrock cross-region
inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` so it works in
us-east-1, us-east-2, and us-west-2.

### 2. Run the Social Media Simulator

```bash
# Install Python dependencies
cd scripts
pip install -r requirements.txt

# Run the basic social media producer
python social_media_producer.py
```

### 4. Monitor the Pipeline

The CloudWatch dashboard `SocialMediaSentimentDashboard` is created by CDK at
deploy time — open it directly from the CloudWatch console.

```bash
# Check S3 for processed data
aws s3 ls s3://$(cat outputs.json | jq -r '.StreamingSentimentDemoPythonStack.S3TablesBucketName')/sentiment-data/
```

## Advanced Usage

### PR Event Simulation

Simulate realistic PR events that affect sentiment:

```bash
cd scripts

# Run a full PR event simulation (baseline → positive → negative → baseline)
python pr_event_simulator.py --scenario full --duration 60 --intensity 0.8

# Run specific scenarios
python pr_event_simulator.py --scenario positive --duration 120
python pr_event_simulator.py --scenario negative --duration 90
```

### High Volume Testing

Test the pipeline with high-volume data:

```bash
# Run high-volume simulation
python high_volume_simulator.py --posts-per-second 10 --duration 300
```

### Extended Simulation

Run extended simulations with varying patterns:

```bash
# Timed simulation with different phases
python extended_pr_simulator_timed.py
```

## Monitoring and Observability

### CloudWatch Metrics

The solution automatically publishes these metrics:

- **AverageSentiment**: Average sentiment score (1-5)
- **PostCount**: Number of posts processed
- **SentimentScore1-5**: Distribution of sentiment scores
- **Platform_{platform}**: Posts by social media platform

### CloudWatch Dashboard

Create a comprehensive dashboard:

```bash
cd scripts
python create_cloudwatch_dashboard.py
```

The dashboard includes:
- Real-time sentiment trends
- Post volume over time
- Sentiment score distribution
- Platform breakdown
- Processing metrics

### Logs and Debugging

Monitor Lambda function logs:

```bash
# View processor Lambda logs
aws logs tail /aws/lambda/$(cat outputs.json | jq -r '.StreamingSentimentDemoPythonStack.ProcessorLambdaName')

# View sentiment analysis Lambda logs
aws logs tail /aws/lambda/$(cat outputs.json | jq -r '.StreamingSentimentDemoPythonStack.SentimentAnalysisFunctionName')
```

## Configuration

### Environment Variables

The Lambda functions use these environment variables:

**Sentiment Analysis Lambda:**
- `BEDROCK_INFERENCE_PROFILE_ID`: Bedrock inference profile (default: `us.anthropic.claude-haiku-4-5-20251001-v1:0`)
- `MODEL_ID`: Bedrock foundation model id, used as fallback (default: `anthropic.claude-haiku-4-5-20251001-v1:0`)
- `BEDROCK_REGION`: Bedrock service region (default: stack region)

**Processor Lambda:**
- `SENTIMENT_FUNCTION_NAME`: Name of the sentiment analysis function
- `S3_BUCKET_NAME`: S3 bucket for storing results

### Customization

#### Modify Sentiment Analysis
Edit `lambda/sentiment-analysis/lambda_function.py` to:
- Change the sentiment scoring scale
- Use different Bedrock models
- Add additional analysis (emotion, topics, etc.)

#### Adjust Processing Logic
Edit `lambda/processor/lambda_function.py` to:
- Change S3 partitioning scheme
- Add data transformations
- Modify batch processing logic

#### Simulation Parameters
Modify simulation scripts to:
- Add new social media platforms
- Change post content patterns
- Adjust timing and volume

## Data Schema

### Input Data (Kinesis)
```json
{
  "id": "unique-post-id",
  "platform": "Twitter|Facebook|Instagram|LinkedIn",
  "content": "Social media post content",
  "author": "username",
  "timestamp": "2024-01-01T12:00:00Z",
  "engagement": {
    "likes": 10,
    "shares": 5,
    "comments": 2
  }
}
```

### Output Data (S3)
```json
{
  "id": "unique-post-id",
  "platform": "Twitter",
  "content": "Social media post content",
  "author": "username",
  "timestamp": "2024-01-01T12:00:00Z",
  "engagement": {
    "likes": 10,
    "shares": 5,
    "comments": 2
  },
  "sentimentScore": 4,
  "timestamp": "2024-01-01T12:00:01Z"
}
```

### S3 Partitioning Structure
```
sentiment-data/
├── year=2024/
│   ├── month=01/
│   │   ├── day=15/
│   │   │   ├── hour=14/
│   │   │   │   ├── 1705327200000.json
│   │   │   │   └── 1705327201000.json
```

## Performance and Scaling

### Current Configuration
- **Kinesis**: 1 shard (1,000 records/second, 1 MB/second)
- **Lambda**: 
  - Processor: 256 MB memory, 5-minute timeout, batch size 10
  - Sentiment: 128 MB memory, 30-second timeout
- **Bedrock**: Claude 3 Haiku (fast, cost-effective)

### Scaling Considerations
- Increase Kinesis shards for higher throughput
- Adjust Lambda memory and timeout based on load
- Consider Lambda reserved concurrency for predictable performance
- Use Bedrock batch inference for higher volumes
- Implement DLQ (Dead Letter Queue) for error handling

## Cost Optimization

### Estimated Costs (us-east-1, per 1M posts)
- **Kinesis Data Streams**: ~$15/month (1 shard)
- **Lambda**: ~$5-10 (depending on execution time)
- **Bedrock**: ~$25-50 (Claude 3 Haiku)
- **S3**: ~$1-5 (depending on storage duration)
- **CloudWatch**: ~$1-3 (metrics and logs)

### Cost Optimization Tips
- Use Kinesis Data Firehose for batch processing if real-time isn't required
- Implement intelligent batching in Lambda
- Use S3 Intelligent Tiering for long-term storage
- Set up CloudWatch log retention policies
- Monitor and optimize Bedrock usage

## Troubleshooting

### Common Issues

0. **CFN `AWS::EarlyValidation::ResourceExistenceCheck` fails silently on redeploy**
   CDK's early-validation hook fails (often without a useful error in the
   CloudFormation events) when named resources already exist from a prior
   deploy. Before redeploying after a partial teardown, delete leftovers:

   ```bash
   aws cloudwatch delete-dashboards --dashboard-names SocialMediaSentimentDashboard
   aws kinesis delete-stream --stream-name social-media-posts-stream --enforce-consumer-deletion
   aws s3 ls | awk '/streamingsentimentdemopyt-/ {print $3}' | xargs -I{} aws s3 rb s3://{} --force
   ```

1. **Bedrock Access Denied**
   - Ensure Bedrock model access is enabled in your AWS account
   - Check IAM permissions for `bedrock:InvokeModel`
   - Claude Haiku 4.5 also requires the cross-region inference profile
     (`us.anthropic.claude-haiku-4-5-20251001-v1:0`); the foundation-model
     ARN alone returns `ValidationException: ... on-demand throughput isn't supported`

2. **Kinesis Stream Not Found**
   - Create the stream: `aws kinesis create-stream --stream-name social-media-posts-stream --shard-count 1`
   - Wait for stream to become active

3. **Lambda Timeout**
   - Increase timeout in CDK stack
   - Reduce batch size for Kinesis event source mapping

4. **High Costs**
   - Monitor Bedrock usage in CloudWatch
   - Implement sampling for high-volume scenarios
   - Use cheaper models for non-critical analysis

### Debug Commands

```bash
# Check Kinesis stream status
aws kinesis describe-stream --stream-name social-media-posts-stream

# List Lambda functions
aws lambda list-functions --query 'Functions[?contains(FunctionName, `Sentiment`)].FunctionName'

# Check S3 bucket contents
aws s3 ls s3://YOUR-BUCKET-NAME/sentiment-data/ --recursive

# View recent CloudWatch metrics
aws cloudwatch get-metric-statistics \
  --namespace SocialMediaSentiment \
  --metric-name AverageSentiment \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average
```

## Cleanup

To avoid ongoing charges, clean up all resources:

```bash
./teardown.sh
```

The Kinesis stream, dashboard, alarms, and DLQ are all part of the CDK stack
and are removed by `cdk destroy`.

## Next Steps

### Enhancements
- Add real-time alerting for sentiment anomalies
- Implement data quality checks and validation
- Add support for multiple languages
- Create a web dashboard for real-time visualization
- Implement A/B testing for different models
- Add data lineage and governance features

### Integration Options
- Connect to Amazon QuickSight for advanced analytics
- Use Amazon Athena to query S3 data
- Integrate with Amazon EventBridge for event-driven workflows
- Add Amazon SQS for decoupling and reliability
- Implement Amazon API Gateway for external data ingestion

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For questions and support:
- Check the troubleshooting section above
- Review AWS documentation for each service
- Open an issue in the repository
- Consult AWS support for service-specific issues
