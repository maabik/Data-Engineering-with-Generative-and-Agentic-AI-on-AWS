# Data Engineering Baseline — Opt-In

**Extension**: Data Engineering Baseline

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: Data Engineering Baseline Extension
Should data engineering baseline rules be enforced for this project?

A) Yes — enforce all DATAENG rules per their P0/P1/P2 severity tiers (recommended for any pipeline producing a dataset consumed by another team, service, or analytical workload, or any pipeline running in production)
B) No — skip all DATAENG rules (suitable only for one-off exploratory analyses with no downstream consumers)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
