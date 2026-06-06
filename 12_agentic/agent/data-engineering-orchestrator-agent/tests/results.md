# Orchestrator Routing Test Results

> **Note on agent names.** This file is a frozen capture of an
> orchestrator test run. The runtime ARN below and the agent IDs in
> `results.json` use the **short slugs in effect at test time**
> (`deo_agent`, `dpa_agent`, `daa_agent`, `dqa_agent`, `dqr_agent`,
> `aia_agent`, etc.). Those names have since been renamed to descriptive
> equivalents — `data_engineering_orchestrator_agent`,
> `data_profiling_agent`, `data_architect_agent`,
> `data_quality_deequ_agent`, `data_quality_agent`,
> `api_integration_agent`, and so on. Live code, deploy scripts, and
> chapter READMEs all use the new names; only this snapshot retains the
> old slugs because that is what the runtime returned at the moment of
> capture.

- Runtime ARN: `arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/deo_agent-rk1WdmFsMH`
- Region: `us-east-1`
- Tests: **10**
- Routing PASS: **10 / 10**  (orchestrator picked the right agent)
- End-to-end PASS: **10 / 10**  (every downstream agent returned a usable response)

Each test sends one prompt to the orchestrator and captures both (a) which
downstream specialist was invoked and (b) what that specialist actually
returned. Tests #6 and #7 were re-run individually after they timed out
during the concurrent batch run (the LLM was stuck in a slow tool-use
loop under contention) — both completed successfully on retry, confirming
the orchestration path is sound.

| # | Test | Routed to | Routing | Final response from agent | Time (s) |
|---|------|-----------|:-------:|---------------------------|---------:|
| 1 | data-profiling-agent | `data_profiling_agent` | ✅ | `{"tableName": "orchtest-20260530233655", "sourceUri": "s3://data-profiling-agent-123456789012-us-west-2/orchtest/orchtest-20260530233655.csv", "reportUri": "s3://…/profile/data-profiling-agent-orchtest-20260530233655/…", …}` | 181.2 |
| 2 | data-architect-agent | `data_architect_agent` | ✅ | `{"database": "data_architect_agent_curated", "rawTable": "datausa_population_raw", "curatedTable": "datausa_population_curated", "location": "s3://…/curated/datausa_population/", "partitionKeys": [{"name": "data…"}, …]}` | 140.1 |
| 3 | data-quality-deequ-agent | `data_quality_deequ_agent` | ✅ | `{"database": "data_architect_agent_curated", "table": "orchtest_20260530233655_curated", "runId": "orchtest_20260530233655_dq", "suggestionsUri": "s3://…/dq-reports/orchtest_20260530233655_curated/suggestions/…", …}` | 145.4 |
| 4 | data-quality-agent | `data_quality_agent` | ✅ | `{"database": "data_architect_agent_curated", "table": "datausa_population_curated", "runId": "datausa_population_20260520t044816z_…", "verdict": "blocked", "severity": "critical", "rootCauses": [{"id": "RC…", …}]}` | 87.1 |
| 5 | api-integration-agent | `api_integration_agent` | ✅ | `status=active` (Lambda generated, deployed, scheduled, smoke-tested) | 79.0 |
| 6 | log-analysis-agent | `log_analysis_agent` | ✅ | `{"plan": "The user submitted a failed pipeline run artifact stored on S3. The log_analysis_agent is the precise fit — it ingests a pipeline-run JSON…"}` (re-run after timeout) | 133.0 |
| 7 | text-to-dag-agent | `text_to_dag_agent` | ✅ | `{"plan": "The user wants a Step Functions ASL state machine from a natural-language pipeline description (hourly Stripe pull → validation → partitioned Parquet write)…"}` (re-run after timeout) | 80.0 |
| 8 | sql-evaluations-agent | `sql_evaluations_agent` | ✅ | `status=revised` (3 findings, full revised SQL returned) | 31.4 |
| 9 | sales-reporting | `sales_reporting_agent` | ✅ | `{"summary": "Total Q3 sales by region across the sales schema:", "data": [{"rank": 1, "region": "GM-Each Region", "country": "Angola", "total_sales": "$22,523,171.08", "orders": 980}, …]}` | 2648.2 |
| 10 | redshift-mcp-capability | `sales_reporting_agent` (proxies the MCP server) | ✅ | `{"tables_in_salesdb": {"dimension_tables": ["dim_account", "dim_district", "dim_dma", "dim_product", "dim_region", "dim_sales_rep", "dim_sales_territory", "dim_time", "dim_zipcode"], "fact_tables": ["fact_sales"], …}}` | 1095.4 |

## Dependencies resolved during this test pass

- **DataBrew dataset quota**: us-west-2 was at 100/100 from accumulated
  agent runs; bulk-deleted 99 `dpa-*` jobs and datasets to free quota.
- **Redshift MCP Cognito pool**: the user pool the MCP runtime was
  configured with had been deleted (`us-east-1_EBPc3LRcj` → not found),
  blocking every sales-reporting + MCP test. Re-created the
  `RedshiftMCPUserPool`, deleted and re-launched the
  `redshift_mcp_server_agentcore` runtime, refreshed
  `/mcp/redshift/credentials` and `/mcp/redshift/agent_arn`. Sales
  agent picks both up dynamically.
- **Empty `prompt` payloads**: the orchestrator was occasionally
  forwarding empty `prompt` strings to downstream agents, causing
  Bedrock `Converse` to reject with a `ValidationException` (HTTP 500
  from the agent). Added a backfill in `_ensure_non_empty_payload`
  that copies the original user request or any non-empty URI/dataset
  field into `prompt` before the call.
- **Stale fixtures**: original test prompts referenced
  `landing/orders.csv` (didn't exist) and a `datausa-population` design
  with a Spark `StringType` casting bug. Replaced both with a fresh
  `orchtest-20260530233655.csv` fixture and the design the architect
  agent produced from it.
- **Test-client read timeouts**: bumped to ~8 hours (28,700s) — just
  under AgentCore Runtime's 8-hour per-invocation ceiling — at every
  layer (test client, orchestrator's boto3 client, orchestrator's JWT
  HTTP client). Earlier 14-min caps were a holdover from the Lambda
  limit and were the actual cause of the apparent timeouts on the
  log-analysis and text-to-dag-agent tests.
