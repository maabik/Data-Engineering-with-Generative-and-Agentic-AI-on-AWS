# AWS Glue ETL — Opt-In

**Extension**: AWS Glue ETL

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: AWS Glue ETL Extension
Should AWS Glue ETL rules be enforced for this project?

A) Yes — enforce all GLUE rules per their P0/P1/P2 severity tiers (recommended if Glue Spark, Spark Streaming, Ray, Python shell, interactive sessions, Glue Studio, or DataBrew is a compute target)
B) No — skip all GLUE rules (suitable when Glue is not used as a compute surface; note this does not exempt Glue Data Catalog usage governed by `data-engineering/catalog` and `data-engineering/s3-lakehouse`)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
