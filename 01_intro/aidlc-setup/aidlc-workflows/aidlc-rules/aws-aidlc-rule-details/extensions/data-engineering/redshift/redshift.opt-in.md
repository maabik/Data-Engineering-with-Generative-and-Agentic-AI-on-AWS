# Amazon Redshift — Opt-In

**Extension**: Amazon Redshift

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: Amazon Redshift Extension
Should Amazon Redshift rules be enforced for this project?

A) Yes — enforce all RSHIFT rules per their P0/P1/P2 severity tiers (recommended if Redshift provisioned or Serverless is a compute target)
B) No — skip all RSHIFT rules (suitable when Redshift is not used)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
