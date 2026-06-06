# auto-cataloger

A Bedrock-powered job that walks an AWS Glue Data Catalog database, samples a
few rows from each table, and asks Claude (via Bedrock) to write back:

- a one-sentence **table description**
- a **business-glossary** suggestion list (terms a data steward should
  consider adding to DataZone)
- a one-sentence **description per column**
- a **PII flag + category** per column (`EMAIL`, `PHONE`, `SSN`, `NAME`,
  `ADDRESS`, `DOB`, `OTHER`)

Everything lands in the Glue catalog as native metadata: column `Comment`
fields, table `Description`, and table `Parameters` (`table_description_ai`,
`glossary_suggestions`, `pii_columns`, `autocat_enriched_at`). Glue is the
metadata store DataZone reads from -- when you attach a DataZone domain to
this account/region, every asset created from these tables inherits the
AI-generated descriptions and PII flags automatically.

## Why this exists

DataZone's auto-generated descriptions are limited and don't run column-level
PII analysis. Manually filling in descriptions and tagging PII for hundreds
of tables is the single biggest blocker for getting a real data catalog
adopted. This tool turns that into a one-command operation that costs a few
cents and runs in seconds per table.

```
Glue table  ─┐                                ┌─►  column Comment
             │                                │
S3 sample ───┼─►  Bedrock (Claude Sonnet 4.6) ─┼─►  table Parameters:
             │       structured JSON           │      pii_columns
table name ──┘                                │      glossary_suggestions
                                              │      table_description_ai
                                              └─►  column Parameters.pii / pii_category
```

## Project layout

```
auto-cataloger/
├── seed_data/
│   ├── customers.csv          # 10 rows, includes synthetic SSN/email/phone PII
│   ├── orders.csv             # 10 rows, no PII
│   └── products.csv           # 5 rows, no PII
├── src/autocat/
│   ├── __main__.py
│   └── cli.py                 # seed | catalog | destroy
├── scripts/
│   ├── seed.sh                # upload CSVs to S3, register Glue tables
│   ├── catalog.sh             # run Bedrock enrichment over the database
│   └── teardown.sh            # drop Glue DB + S3 bucket
├── requirements.txt
└── README.md
```

## Prerequisites

- AWS profile with `glue:*`, `s3:*` (scoped to the demo bucket), and
  `bedrock:InvokeModel` for the chosen inference profile
- Bedrock model access enabled for **Claude Sonnet 4.6** in `us-west-2`
  (`us.anthropic.claude-sonnet-4-6`). If access is not enabled, request it
  from the Bedrock console -> Model access page.
- Python 3.10+

## Quick start

```bash
cd auto-cataloger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

./scripts/seed.sh                # uploads CSVs + registers Glue tables
./scripts/catalog.sh             # asks Bedrock to enrich each table
aws glue get-table \
  --database-name autocat_demo --name customers \
  --query 'Table.{desc:Description,params:Parameters,cols:StorageDescriptor.Columns}'
./scripts/teardown.sh            # drops Glue DB + bucket
```

`seed` and `destroy` are idempotent. `catalog` is also safe to re-run -- it
overwrites the same metadata fields each time.

## What gets created

| Resource | Identifier |
|---|---|
| S3 bucket | `autocat-demo-{account}-{region}` |
| S3 prefixes | `customers/`, `orders/`, `products/` (each with one CSV) |
| Glue database | `autocat_demo` |
| Glue tables | `customers`, `orders`, `products` (EXTERNAL_TABLE, CSV SerDe) |

## What `catalog` writes back

For each table:

```jsonc
// Glue table-level
{
  "Description": "Core customer registry storing personal identifiers ...",
  "Parameters": {
    "table_description_ai": "...",
    "glossary_suggestions": "lifetime_value, customer_id, signup_date, zip_code",
    "pii_columns": "full_name, email, phone, ssn, birth_year",
    "autocat_enriched_at": "2026-05-15T18:06:51Z"
  }
}

// Glue per-column (StorageDescriptor.Columns[i])
{
  "Name": "ssn",
  "Comment": "The customer's Social Security Number used for identity verification.",
  "Parameters": { "pii": "true", "pii_category": "SSN" }
}
```

## Tested output

The example seed data was deliberately constructed so that Bedrock should
flag exactly the columns a human reviewer would. On the live test run:

| Table | Columns | PII flagged | Glossary terms suggested |
|---|---:|---|---|
| `customers` | 10 | `full_name` (NAME), `email` (EMAIL), `phone` (PHONE), `ssn` (SSN), `birth_year` (DOB) | 4 |
| `orders` | 10 | none | 6 |
| `products` | 7 | none | 7 |

## Connecting to DataZone

The Glue catalog is DataZone's source of truth: when you publish an asset to
DataZone from one of these tables, the column comments and table description
flow through automatically. The `pii_columns` table parameter is the cue for
your governance pipeline to apply Lake Formation column filters (see
`../../03_datalake/lakeformation-data-filters/`) or DataZone metadata forms
to mask the flagged columns from non-privileged consumers.

If you want a DataZone domain to consume these assets, deploy
`../datazone-as-code` first, then create a DataZone data source pointing at
this Glue database -- the next subscription cycle will surface every table
with its AI-generated metadata pre-populated.

## Cost

Per `catalog` run (3 tables, 5 sample rows each, Sonnet 4.6 at us-west-2 list
prices):

- ~3 prompts of ~600 input tokens, ~800 output tokens each
- well under \$0.05 total

## Teardown

```bash
./scripts/teardown.sh
```

Drops the Glue database (which cascades to its tables) and empties + deletes
the S3 bucket. Re-runnable on an already-empty environment.

## Design decisions

- **Glue is the integration point**: writing AI-generated metadata into Glue
  Parameters / Comments means *every* downstream consumer (Athena, EMR,
  DataZone, Redshift Spectrum) inherits it for free. We do not write to
  DataZone APIs directly because the metadata model differs by domain
  configuration; Glue is the lowest common denominator and is what DataZone
  syncs from anyway.
- **Bedrock Converse API + `temperature=0`**: catalog metadata should be
  deterministic across re-runs. The system prompt asks for strict JSON only,
  with a tolerant code-fence stripper as a safety net.
- **Sample size = 5**: enough for Claude to recognize patterns (SSN format,
  email shape, ISO dates) without bloating the prompt for wide tables. Tune
  with `--sample-size`.
- **Per-column `pii=false` even when not PII**: makes the Glue catalog
  searchable by `pii=false` to find safe columns for analyst-facing views,
  not just by `pii=true` for the masked set.
- **No retry logic on Bedrock**: failures should surface fast in a
  human-driven workflow. If you need retries, wrap `cmd_catalog` in
  `--tables foo` invocations and re-run only the failures.
