# FinOps for Generative AI

Governance and cost-management projects for AI workloads on AWS. The four
projects form a layered stack: a shared data foundation, agent-level cost
visibility, a quality-preserving routing optimizer, and a hard enforcement
control that turns monitoring into action.

## Projects

| Project | What it does |
|---|---|
| [`ai-finops-data-lake/`](ai-finops-data-lake/) | Foundation: ingests Bedrock cost/usage signals into an Iceberg lake; everything else queries here |
| [`agent-run-cost-tracer/`](agent-run-cost-tracer/) | Closes the agentic AI visibility gap: per-run cost attribution across multi-step, multi-model agent executions |
| [`model-cascade-router/`](model-cascade-router/) | Biggest optimization lever: routes prompts to the cheapest model that meets quality bar, with eval harness to prove quality held |
| [`token-budget-circuit-breaker/`](token-budget-circuit-breaker/) | Enforcement: turns FinOps from monitoring into control — blocks or throttles workloads that exceed budget policy |
