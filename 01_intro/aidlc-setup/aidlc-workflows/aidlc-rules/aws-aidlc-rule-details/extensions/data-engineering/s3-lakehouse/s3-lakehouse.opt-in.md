# S3 Lakehouse — Opt-In

**Extension**: S3 Lakehouse

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: S3 Lakehouse Extension
Should S3 Lakehouse rules be enforced for this project?

A) Yes — enforce all S3LH rules per their P0/P1/P2 severity tiers (recommended if any persisted dataset lives on Amazon S3 and is queried by name through Athena, EMR, Glue, Spark, Trino, Redshift Spectrum, or any SQL engine)
B) No — skip all S3LH rules (suitable when S3 is used only as a passthrough or staging buffer with no SQL-queryable tables)
X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
