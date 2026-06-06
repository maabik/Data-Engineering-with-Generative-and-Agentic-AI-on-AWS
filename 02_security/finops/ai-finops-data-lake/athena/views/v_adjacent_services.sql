-- Adjacent AI service costs: Knowledge Base, OpenSearch, S3 Vectors, Guardrails.
-- Joins CUR-sourced line items with invocation counts for context.

CREATE OR REPLACE VIEW ai_finops.v_adjacent_services AS
SELECT
    date,
    account_id,
    region,
    service_category,
    product_code,
    usage_type,
    tenant_id,
    workload,
    COUNT(*)                                                    AS line_item_count,
    SUM(usage_amount)                                           AS total_usage_amount,
    SUM(COALESCE(actual_cost_usd, estimated_cost_usd))          AS total_cost_usd
FROM ai_finops.invocations
WHERE service_category IN ('bedrock_kb', 'opensearch', 's3_vectors', 'bedrock_guardrail')
GROUP BY date, account_id, region, service_category, product_code, usage_type, tenant_id, workload;
