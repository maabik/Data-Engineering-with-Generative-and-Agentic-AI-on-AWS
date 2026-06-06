# 04 — Data mesh on Amazon DataZone

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Data mesh examples using AWS DataZone, focused on closing the IaC and
governance gaps that the service ships with.

## Projects

| Project | Description |
|---|---|
| [datazone-as-code](datazone-as-code/) | Declarative YAML manifest -> DataZone domains, units, projects, owners, policy grants, glossaries, and terms. Idempotent apply + cascading teardown. Closes the IaC gap DataZone notoriously has. |
| [auto-cataloger](auto-cataloger/) | Bedrock-powered job that walks a Glue Data Catalog database, samples rows, and writes back column descriptions, business-glossary suggestions, and PII tags as Glue metadata that DataZone inherits automatically. |
| [datamesh-maturity-assessment](datamesh-maturity-assessment/) | Read-only scoring of a live DataZone deployment against Zhamak Dehghani's four data-mesh principles, with a prioritized remediation list and Markdown + JSON reports. |
| [cross-domain-lineage](cross-domain-lineage/) | Reads DataZone subscriptions and OpenLineage events and renders a domain-unit-to-domain-unit dependency graph (Mermaid + interactive HTML). Useful for federated governance reviews. |
| [data-product-validator](data-product-validator/) | Validates a DataZone asset against a YAML data contract and gates the publish step on breaking changes (column missing, type changed). Exits non-zero in CI and refuses to call `CreateListingChangeSet` when the diff is broken. |

## Default region

All projects default to **us-east-1**; CLIs and scripts honor
`AWS_REGION=…` to override.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| datazone-as-code | DataZone domain + IAM role | $0.0000 | $0.0014 | $0.10/asset/month once published; ~$0/hr at rest with no listings. |
| auto-cataloger | S3 + Glue catalog (3 tables) | $0.0000 | $0.0010 | Pay-per-GB / per-request. |
| auto-cataloger | Bedrock per row sampled | $0.0000 | $0.0020 | Claude Sonnet enrichment runs on demand. |
| cross-domain-lineage | (none, read-only by default) | $0.0000 | $0.0001 | `seed.sh` adds optional Glue/DataZone resources you tear down. |
| data-product-validator | (none) | $0.0000 | $0.0001 | CLI only. |
| datamesh-maturity-assessment | (none) | $0.0000 | $0.0001 | Read-only scanner. |
| **Chapter total** | | **$0.0000** | **~$0.0047** | Everything pay-per-use. |

DataZone itself has **no hourly fixed charge** — billing is per
published asset per month. Until you call `dzac apply` to create
listings, the chapter is effectively free at rest.

