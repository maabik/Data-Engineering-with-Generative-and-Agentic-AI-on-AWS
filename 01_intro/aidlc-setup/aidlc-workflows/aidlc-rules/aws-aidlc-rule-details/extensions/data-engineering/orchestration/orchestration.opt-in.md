# Orchestration — Opt-In

**Extension**: Orchestration (MWAA + Step Functions)

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: Orchestration Extension
Should orchestration rules be enforced for this project?

A) Yes — enforce all ORCH rules per their P0/P1/P2 severity tiers (recommended if any production pipeline uses Amazon MWAA or AWS Step Functions for orchestration)
B) No — skip all ORCH rules (suitable when pipelines are triggered exclusively by native platform schedulers — Databricks Workflows, dbt Cloud, Snowflake Tasks — with no MWAA or Step Functions involvement)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
