# SEC 10-K Filings — Secondary Example

Ingests a small set of SEC 10-K excerpts and lets the agent answer
questions about cross-company relationships:

> "Which of these companies disclose material reliance on AWS, and
> which of those have a CEO who was previously at AWS?"

## Run it

```bash
# Drop SEC 10-K excerpts as plain text into ./filings/
python -m examples.sec_10k_filings.ingest_filings ./filings/
python examples/pe_due_diligence/run_agent.py \
    "Which of the ingested companies disclose material reliance on AWS?"
```

## Why this example

10-K language is dense and standardized — vector RAG handles individual
queries fine but struggles to compare disclosures across filings. The
graph layer's `Organization ← MENTIONS ← Document → MENTIONS → Concept`
pattern makes "all companies that mention X" a single hop instead of an
LLM-side aggregation.

## Status

This example is intentionally a stub. The flagship `pe_due_diligence/`
example is the canonical entry point; this directory exists to show
how a second domain plugs into the same schema. To populate it, drop
plain-text excerpts of any 10-K filings into `filings/` and rerun.
