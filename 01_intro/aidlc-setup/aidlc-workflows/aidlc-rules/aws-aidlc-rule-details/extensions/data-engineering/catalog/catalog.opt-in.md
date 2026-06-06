# Data Catalog and Ownership — Opt-In

**Extension**: Data Catalog and Ownership

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: Data Catalog and Ownership Extension
Should data catalog and ownership rules be enforced for this project?

A) Yes — enforce all CAT rules per their P0/P1/P2 severity tiers (recommended for any project producing datasets consumed by another team, service, or analytical workload)
B) No — skip all CAT rules (suitable only for fully self-contained pipelines whose outputs are never shared outside the producing team and have no external consumers)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
