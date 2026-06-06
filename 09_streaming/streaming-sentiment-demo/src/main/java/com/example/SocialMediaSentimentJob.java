package com.example;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringEncoder;
import org.apache.flink.connector.file.sink.FileSink;
import org.apache.flink.connector.kinesis.source.KinesisStreamsSource;
import org.apache.flink.connector.kinesis.source.KinesisStreamsSourceBuilder;
import org.apache.flink.connector.kinesis.source.enumerator.KinesisShardAssigner;
import org.apache.flink.connector.kinesis.source.enumerator.assigner.ShardAssignerFactory;
import org.apache.flink.core.fs.Path;
import org.apache.flink.streaming.api.datastream.AsyncDataStream;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.sink.filesystem.rollingpolicies.DefaultRollingPolicy;
import org.apache.flink.streaming.api.serialization.SimpleStringSchema;
import software.amazon.awssdk.regions.Region;

import java.time.Duration;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.TimeUnit;

/**
 * Apache Flink 2.x job for Amazon Managed Service for Apache Flink (MSF).
 *
 * <p>Pipeline: Kinesis → Bedrock (async sentiment scoring) → S3 (line-delimited JSON).
 *
 * <p>MSF supplies job configuration through the runtime properties group
 * "FlinkApplicationProperties". Required keys:
 * <ul>
 *   <li>{@code input.stream.arn} — Kinesis stream ARN to consume.</li>
 *   <li>{@code aws.region} — AWS region (defaults to us-east-1).</li>
 *   <li>{@code output.s3.bucket} — S3 bucket name for enriched output.</li>
 *   <li>{@code output.s3.prefix} — prefix under the bucket (default {@code sentiment/}).</li>
 *   <li>{@code bedrock.model.id} — Bedrock model id used for classification.</li>
 * </ul>
 */
public final class SocialMediaSentimentJob {

    private SocialMediaSentimentJob() {}

    public static void main(String[] args) throws Exception {
        Map<String, Properties> props = KinesisAnalyticsRuntime.getApplicationProperties();
        Properties flinkProps = props.getOrDefault(
                "FlinkApplicationProperties", new Properties());

        String streamArn = required(flinkProps, "input.stream.arn");
        String region = flinkProps.getProperty("aws.region", "us-east-1");
        String bucket = required(flinkProps, "output.s3.bucket");
        String prefix = flinkProps.getProperty("output.s3.prefix", "sentiment/");
        String modelId = flinkProps.getProperty(
                "bedrock.model.id", "us.anthropic.claude-3-5-haiku-20241022-v1:0");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(60_000L);

        DataStream<String> raw = env.fromSource(
                buildKinesisSource(streamArn, region),
                WatermarkStrategy.noWatermarks(),
                "kinesis-source");

        DataStream<String> enriched = AsyncDataStream
                .unorderedWait(
                        raw,
                        new BedrockSentimentClassifier(Region.of(region), modelId),
                        30,
                        TimeUnit.SECONDS,
                        100)
                .name("bedrock-sentiment");

        FileSink<String> s3Sink = FileSink
                .forRowFormat(
                        new Path(String.format("s3://%s/%s", bucket, prefix)),
                        new SimpleStringEncoder<String>("UTF-8"))
                .withRollingPolicy(
                        DefaultRollingPolicy.builder()
                                .withRolloverInterval(Duration.ofMinutes(5))
                                .withInactivityInterval(Duration.ofMinutes(2))
                                .withMaxPartSize(64L * 1024 * 1024)
                                .build())
                .build();

        enriched.sinkTo(s3Sink).name("s3-file-sink");

        env.execute("Social Media Sentiment (Flink 2.x)");
    }

    private static KinesisStreamsSource<String> buildKinesisSource(String streamArn, String region) {
        Properties consumerConfig = new Properties();
        consumerConfig.setProperty("aws.region", region);
        // Start at the tail so the demo reflects current PR-event traffic.
        consumerConfig.setProperty("flink.stream.initpos", "LATEST");

        KinesisShardAssigner assigner = ShardAssignerFactory.uniformShardAssigner();
        KinesisStreamsSourceBuilder<String> builder = KinesisStreamsSource.<String>builder()
                .setStreamArn(streamArn)
                .setSourceConfig(consumerConfig)
                .setDeserializationSchema(new SimpleStringSchema())
                .setKinesisShardAssigner(assigner);
        return builder.build();
    }

    private static String required(Properties props, String key) {
        String value = props.getProperty(key);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(
                    "Missing required runtime property '" + key + "'. "
                    + "Set it under the FlinkApplicationProperties group in the MSF console.");
        }
        return value;
    }
}
