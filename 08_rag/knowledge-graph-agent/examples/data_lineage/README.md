# Data Lineage — Engineering-Focused Example

Reads an OpenLineage event stream (JSON-lines) and projects it into the
knowledge graph as `Dataset` nodes connected by `DERIVED_FROM` edges.

## Why this example

The book threads lineage through every chapter — Iceberg manifests,
Glue jobs, Step Functions, Redshift Spectrum. By materializing lineage
in the same graph that holds business entities, an engineer can ask
questions that span both:

> "Which downstream datasets are affected if the Stripe payments API
> changes its schema?"

That's "trace `Stripe → MENTIONS ← Document → MENTIONS → DataSet → DERIVED_FROM*`."
Vector RAG can't do this; pure lineage tools (Marquez, OpenLineage UI)
don't know about the business entities.

## Run it

```bash
# Sample event stream is in lineage_events.jsonl.
python -m examples.data_lineage.project_lineage \
    examples/data_lineage/lineage_events.jsonl
```

## Status

This is a starter — the sample event stream is short. Plug a real
OpenLineage Marquez stream in by setting up an SQS subscription and
calling ``knowledge_graph_agent.lineage.lineage_collector.load_events`` on each
batch.
