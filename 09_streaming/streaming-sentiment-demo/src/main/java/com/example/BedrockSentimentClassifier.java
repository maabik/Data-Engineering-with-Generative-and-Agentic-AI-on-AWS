package com.example;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.flink.streaming.api.functions.async.ResultFuture;
import org.apache.flink.streaming.api.functions.async.RichAsyncFunction;
import org.apache.flink.configuration.Configuration;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.bedrockruntime.BedrockRuntimeAsyncClient;
import software.amazon.awssdk.services.bedrockruntime.model.InvokeModelRequest;
import software.amazon.awssdk.services.bedrockruntime.model.InvokeModelResponse;

import java.time.Instant;
import java.util.Collections;

/**
 * Async per-record sentiment scoring against Amazon Bedrock.
 *
 * <p>Each input record is expected to be a JSON object with at least a
 * {@code content} field. We send the content to Claude via the Bedrock Runtime
 * messages API, parse a 1–5 score and a one-word label, and emit the original
 * JSON enriched with {@code sentiment_score}, {@code sentiment_label}, and
 * {@code sentiment_processed_at}.
 *
 * <p>If Bedrock fails (throttling, malformed JSON, model errors), we still emit
 * the original record with a neutral score so the pipeline never drops data.
 */
public final class BedrockSentimentClassifier extends RichAsyncFunction<String, String> {

    private static final long serialVersionUID = 1L;

    private final String regionId;
    private final String modelId;

    private transient BedrockRuntimeAsyncClient client;
    private transient ObjectMapper mapper;

    public BedrockSentimentClassifier(Region region, String modelId) {
        this.regionId = region.id();
        this.modelId = modelId;
    }

    @Override
    public void open(Configuration parameters) {
        this.client = BedrockRuntimeAsyncClient.builder()
                .region(Region.of(regionId))
                .build();
        this.mapper = new ObjectMapper();
    }

    @Override
    public void close() {
        if (client != null) {
            client.close();
        }
    }

    @Override
    public void asyncInvoke(String input, ResultFuture<String> resultFuture) {
        ObjectNode original;
        String content;
        try {
            JsonNode parsed = mapper.readTree(input);
            if (!parsed.isObject()) {
                resultFuture.complete(Collections.singletonList(input));
                return;
            }
            original = (ObjectNode) parsed;
            content = original.path("content").asText("");
        } catch (Exception e) {
            // If we can't parse, emit untouched and keep moving.
            resultFuture.complete(Collections.singletonList(input));
            return;
        }

        if (content.isBlank()) {
            resultFuture.complete(Collections.singletonList(
                    enrich(original, 3, "neutral").toString()));
            return;
        }

        InvokeModelRequest request = buildRequest(content);
        client.invokeModel(request).whenComplete((response, error) -> {
            if (error != null || response == null) {
                resultFuture.complete(Collections.singletonList(
                        enrich(original, 3, "neutral").toString()));
                return;
            }
            try {
                Score score = parseResponse(response);
                resultFuture.complete(Collections.singletonList(
                        enrich(original, score.value, score.label).toString()));
            } catch (Exception parseError) {
                resultFuture.complete(Collections.singletonList(
                        enrich(original, 3, "neutral").toString()));
            }
        });
    }

    @Override
    public void timeout(String input, ResultFuture<String> resultFuture) {
        // On timeout, emit the original record with a neutral score.
        try {
            JsonNode parsed = mapper.readTree(input);
            if (parsed.isObject()) {
                resultFuture.complete(Collections.singletonList(
                        enrich((ObjectNode) parsed, 3, "neutral").toString()));
                return;
            }
        } catch (Exception ignored) {
            // fall through
        }
        resultFuture.complete(Collections.singletonList(input));
    }

    private InvokeModelRequest buildRequest(String content) {
        ObjectNode body = mapper.createObjectNode();
        body.put("anthropic_version", "bedrock-2023-05-31");
        body.put("max_tokens", 80);
        body.put("temperature", 0.0);
        ObjectNode message = body.putArray("messages").addObject();
        message.put("role", "user");
        message.putArray("content").addObject()
                .put("type", "text")
                .put("text",
                        "Classify the sentiment of the following social media post. "
                                + "Respond with ONLY a JSON object on one line of the form "
                                + "{\"score\": <1-5 integer>, \"label\": \"<positive|negative|neutral|mixed>\"}. "
                                + "1 = very negative, 5 = very positive. No extra prose.\n\n"
                                + "Post: " + content);

        return InvokeModelRequest.builder()
                .modelId(modelId)
                .contentType("application/json")
                .accept("application/json")
                .body(SdkBytes.fromUtf8String(body.toString()))
                .build();
    }

    private Score parseResponse(InvokeModelResponse response) throws Exception {
        JsonNode payload = mapper.readTree(response.body().asUtf8String());
        JsonNode contentNodes = payload.path("content");
        StringBuilder text = new StringBuilder();
        if (contentNodes.isArray()) {
            for (JsonNode node : contentNodes) {
                if ("text".equals(node.path("type").asText())) {
                    text.append(node.path("text").asText());
                }
            }
        }
        String body = text.toString().trim();
        int start = body.indexOf('{');
        int end = body.lastIndexOf('}');
        if (start < 0 || end <= start) {
            return new Score(3, "neutral");
        }
        JsonNode parsed = mapper.readTree(body.substring(start, end + 1));
        int score = clamp(parsed.path("score").asInt(3));
        String label = parsed.path("label").asText("neutral").toLowerCase();
        return new Score(score, label);
    }

    private static int clamp(int value) {
        return Math.max(1, Math.min(5, value));
    }

    private ObjectNode enrich(ObjectNode original, int score, String label) {
        ObjectNode out = original.deepCopy();
        out.put("sentiment_score", score);
        out.put("sentiment_label", label);
        out.put("sentiment_processed_at", Instant.now().toString());
        return out;
    }

    private static final class Score {
        final int value;
        final String label;
        Score(int value, String label) {
            this.value = value;
            this.label = label;
        }
    }
}
