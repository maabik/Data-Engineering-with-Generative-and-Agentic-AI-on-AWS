# bda-blueprint-library

A curated set of **Amazon Bedrock Data Automation** (BDA) custom blueprints
for the most common enterprise document types, with a labeled corpus and a
test harness that diffs BDA's inferred fields against ground truth.

## What ships

| Slug | Document | Schema fields | Origin |
| --- | --- | --- | --- |
| `invoice` | Commercial invoice | 14 fields + line-item array | curated |
| `bill_of_lading` | Carrier Bill of Lading | 16 fields + goods-line array | curated |
| `k1_1065` | IRS Schedule K-1 (Form 1065) | 24 fields | curated |
| `lease_abstract` | Commercial lease abstract | 15 fields + rent-step array | curated |
| `msa` | Master Services Agreement | 22 fields | curated |
| `purchase_order` | Standard purchase order | 17 fields + line-item array | **generated** |

Each blueprint is a JSON-schema-style document under `blueprints/` matching
BDA's custom-blueprint format (top-level `class`, `description`,
`properties` with `inferenceType: "explicit"`, plus reusable
`definitions` for line-item arrays). Curated blueprints were authored by
hand; generated blueprints came out of `scripts/generate_blueprint.py`
(see below).

## Layout

```
bda-blueprint-library/
├── blueprints/                # 5 blueprint schemas
├── corpus/                    # one labeled doc per blueprint
│   └── <slug>/
│       ├── <slug>.pdf         # synthesized PDF
│       └── expected.json      # ground-truth field values
├── scripts/
│   ├── build_corpus.py        # synthesize PDFs from ground truth
│   ├── generate_blueprint.py  # author a new blueprint from a doc or description
│   ├── deploy.py              # idempotent BDA + S3 deploy
│   └── teardown.py            # delete blueprints + bucket
├── tests/
│   └── run_tests.py           # invoke BDA per doc, diff vs expected
├── deploy.sh                  # build_corpus.py + deploy.py
└── teardown.sh                # teardown.py
```

`build/blueprint-arns.json` is written by `deploy.sh` and consumed by
`run_tests.py` and `teardown.sh`.

## Architecture

```
              corpus/<slug>/<slug>.pdf
                       │
                       ▼
        deploy.sh ───► S3 bucket
                       (bda-blueprint-library-<acct>-<region>)
                       │
                       ▼
        deploy.sh ───► BDA control plane
                       CreateBlueprint  × 5  →  blueprint ARNs
                       │
                       ▼
        run_tests.py ─► BDA runtime
                       InvokeDataAutomationAsync
                         inputConfiguration:  s3://.../corpus/<slug>/...
                         outputConfiguration: s3://.../runs/<slug>/...
                         blueprints: [<this slug's ARN>]
                         dataAutomationProfileArn: us.data-automation-v1
                       │
                       ▼
                       poll GetDataAutomationStatus
                       │
                       ▼
                       fetch result.json from S3
                       │
                       ▼
                       diff inference_result vs expected.json
                       (string ⊆-match, numeric tol, bool, array-equal)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install boto3 reportlab
```

Required AWS access:

- `bedrock:CreateBlueprint`, `UpdateBlueprint`, `DeleteBlueprint`,
  `ListBlueprints`, `GetBlueprint`
- `bedrock:InvokeDataAutomationAsync`, `GetDataAutomationStatus`
- `bedrock:InvokeModel` for `us.anthropic.claude-sonnet-4-6` — only
  needed if you use `scripts/generate_blueprint.py`
- `s3:CreateBucket`, `PutObject`, `GetObject`, `ListBucket`,
  `DeleteObject`, `DeleteBucket`
- BDA enabled in your region; default profile
  `us.data-automation-v1` is referenced by the test harness

## Deploy + test

```bash
./deploy.sh                     # synthesize corpus, push to S3, register blueprints
python3 tests/run_tests.py      # invoke BDA per doc, diff vs ground truth
./teardown.sh                   # delete blueprints + empty/delete bucket
```

`deploy.sh` is idempotent — re-running calls `UpdateBlueprint` on the
existing blueprint instead of recreating it.

## Live test results

Run `2026-05-21`, region `us-west-2`, account ending `…1797`,
default `us.data-automation-v1` profile:

| Document | Status | Fields matched | Match rate |
| --- | --- | --- | --- |
| `invoice` | Success | 15 / 15 | **100%** |
| `bill_of_lading` | Success | 17 / 17 | **100%** |
| `k1_1065` | Success | 23 / 24 | 96% |
| `lease_abstract` | Success | 16 / 16 | **100%** |
| `msa` | Success | 22 / 22 | **100%** |
| `purchase_order` (generated) | Success | 17 / 18 | 94% |
| **Overall** | | **110 / 112** | **98%** |

Per-doc BDA latency was ~20-26 s end-to-end (`InvokeDataAutomationAsync`
+ poll to `Success`).

### Misses

- `k1_1065.self_employment_earnings` — The Schedule K-1 PDF lists
  `14. Self-employment earnings (Code A)` as `$42,000.00`. BDA returned
  `""`. Honest behavior: line 14 is a code-tagged box and the model
  declined to attribute the dollar amount to a specific code letter.
- `purchase_order.vendor_contact` — Expected
  `"Marcus Yi  |  email  |  phone"`. BDA returned the same string with
  the `|` pipes read as capital `I`s. PDF rasterization quirk; both
  values describe the same contact.

The remaining fields, including trickier ones like `qualified_dividends`
(line 6b vs 6a disambiguation), the `is_foreign_partner` boolean, and
the multi-row rent schedule on the lease abstract, were extracted
correctly.

## Field-match semantics

`tests/run_tests.py` uses these comparators when diffing
`inference_result` against `expected.json`:

- **strings** — whitespace-collapsed, lowercased; passes if either side
  is a substring of the other (so "Net 30" matches "Net 30 days")
- **numbers** — `abs_tol=0.01` or `rel_tol=0.005`
- **booleans** — strict, with `"yes"/"no"` text variants accepted
- **arrays of objects** — same length and per-field comparator on each
  item

This is intentionally lenient on prose (use clauses, indemnity summary)
and strict on numerics (totals, capital accounts, rent steps).

## Generating a blueprint from a doc or description

`scripts/generate_blueprint.py` uses Claude Sonnet 4.6 on Bedrock with the
existing `invoice.json` and `msa.json` blueprints as few-shots, then
**validates** the model's output (snake_case fields, required
`inferenceType=explicit`, valid `$ref` targets, non-empty instructions).
On validation failure it feeds the errors back to the model and retries
up to three times.

```bash
# From a free-text description
python3 scripts/generate_blueprint.py \
  --from-description "Standard purchase order: PO number, dates, buyer, vendor, line items, totals, signer" \
  --name purchase_order

# From an actual sample document (PDF, PNG, or JPG)
python3 scripts/generate_blueprint.py \
  --from-document samples/some_invoice.pdf \
  --name custom_invoice

# Generate and immediately register with BDA
python3 scripts/generate_blueprint.py \
  --from-description "..." --name purchase_order --register
```

Output lands in `blueprints/<slug>.json`. With `--register`, the script
calls `CreateBlueprint` (or `UpdateBlueprint` on rerun) and appends the
ARN into `build/blueprint-arns.json` so `tests/run_tests.py` picks it up.

`scripts/deploy.py` auto-discovers any blueprint files not in the
curated set and deploys them as `BdaLib-Gen-<slug>`, so you can also
just commit a generated `*.json` and let the next `./deploy.sh` register
it.

### What I tested

| Mode | Input | Output | Notes |
| --- | --- | --- | --- |
| `--from-description` | `"Standard purchase order…"` | `purchase_order.json`, 18 fields, 1 array | First-attempt valid; deployed; **17/18** match rate live |
| `--from-document` | `corpus/bill_of_lading/bill_of_lading.pdf` | `UniformBillOfLading`, 17 fields, 1 array | First-attempt valid; instructions referenced specific labels visible on the page (e.g. *"Trailer / Seal field, the portion before the slash"*) |

To benchmark a generated blueprint against ground truth:

1. `python3 scripts/generate_blueprint.py --from-description "..." --name <slug> --register`
2. Synthesize or supply `corpus/<slug>/<slug>.pdf` + `corpus/<slug>/expected.json`
3. `./deploy.sh && python3 tests/run_tests.py`

## Adding a curated blueprint by hand

If you want a hand-authored blueprint (more control than the generator):

1. Drop a `<slug>.json` into `blueprints/` matching the BDA custom-
   blueprint format.
2. Optionally add it to `CURATED_NAMES` in `scripts/deploy.py` for a
   stable deployed name (`BdaLib-<...>`); otherwise it deploys as
   `BdaLib-Gen-<slug>`.
3. Add a `corpus/<slug>/<slug>.pdf` and `corpus/<slug>/expected.json` —
   easiest path is to write a `build_<slug>()` in `scripts/build_corpus.py`
   so the ground truth is the source of truth and the PDF is generated
   from it.
4. `./deploy.sh && python3 tests/run_tests.py`.

## What this is not

- Not a CDK / CloudFormation stack — straight boto3 deploy.
- Not training data — the corpus is one synthesized exemplar per type
  for smoke-testing the schemas, not for fine-tuning.
- Not a substitute for AWS-managed sample blueprints (`Invoice`,
  `Receipt`, `W2-Form`, …) when those fit your needs unmodified. This
  library covers the document types AWS doesn't ship, and gives you
  full control of field naming/normalization for the ones it does.
