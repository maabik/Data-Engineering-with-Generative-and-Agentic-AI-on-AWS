-- Recent injection / jailbreak attempts with redacted prompts.

SELECT
    event_time,
    request_id,
    user_id,
    tenant_id,
    session_id,
    trace_id,
    model_id,
    guardrail_action,
    guardrail_topics_triggered,
    guardrail_content_filters,
    injection_detected,
    -- Show only the first 500 chars of the redacted input for review
    SUBSTR(input_text_redacted, 1, 500)     AS input_preview,
    SUBSTR(output_text_redacted, 1, 500)    AS output_preview
FROM bedrock_audit.invocations
WHERE date >= CURRENT_DATE - INTERVAL '7' DAY
  AND (
      injection_detected = true
      OR guardrail_action IN ('BLOCKED', 'INTERVENED')
  )
ORDER BY event_time DESC
LIMIT 200;
