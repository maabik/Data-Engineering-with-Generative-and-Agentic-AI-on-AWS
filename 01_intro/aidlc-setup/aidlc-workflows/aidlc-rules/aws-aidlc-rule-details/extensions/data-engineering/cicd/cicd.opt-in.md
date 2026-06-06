# CI/CD for Data Assets — Opt-In

**Extension**: CI/CD for Data Assets

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: CI/CD for Data Assets Extension
Should CI/CD rules for data assets be enforced for this project?

A) Yes — enforce all CICD rules per their P0/P1/P2 severity tiers (recommended for any project deploying data pipelines, schemas, or orchestration definitions to a production environment)
B) No — skip all CICD rules (suitable only for purely exploratory or sandbox work with no production consumers)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
