# data-product-validator

Validates an incoming **data product** against a YAML **data contract**
(dbt-flavored) and **gates the DataZone publish step** on whether the diff
contains any breaking changes. The producer runs `dpv publish` instead of
clicking the console "Publish" button -- if the live asset has drifted from
the contract in a way that would break subscribers, the publish call never
fires.

## Why this exists

DataZone's publish action takes whatever the asset currently looks like and
exposes it to subscribers. There's no built-in concept of an asset spec, so
a producer can quietly rename a column, change a type, or drop a field, and
the only signal subscribers get is their downstream pipeline blowing up.

A *data contract* fixes this by giving the asset a schema-level spec that
lives in version control. This validator is the enforcement piece: it
compares the contract against the live asset and refuses to publish when
something subscribers depend on has gone away.

## Contract format

```yaml
asset: cocoa_shipments
project: chocolate-producer
description: Raw cocoa shipment receipts from origin warehouses.

columns:
  - name: id
    type: string
    required: true
  - name: amount
    type: double
    required: true
  - name: ts
    type: timestamp
    required: true

glossary_terms:        # optional: terms expected to be attached to the asset
  - Cocoa

deprecated_columns: [] # optional: columns the contract has retired but
                       #           subscribers may still expect for a while
```

Type names are normalized: `varchar(255)` ~= `string`, `bigint` ~= `long`,
`bool` ~= `boolean`, etc, so the contract doesn't have to mirror Glue's
exact spelling.

## Severity rules

| Severity | Triggers | Effect |
|---|---|---|
| `breaking` | required column missing from live asset; column type changed | `validate` exits 1; `publish` blocks |
| `warning` | optional column missing; deprecated column still present; description missing; expected glossary term not attached | `validate` exits 0 (or 1 with `--strict`); `publish` blocks unless `--allow-warning` |
| `info` | live asset has a column the contract has not declared (additive, safe); descriptions drift | never blocks |

## Project layout

```
data-product-validator/
├── src/dpv/
│   ├── __main__.py
│   └── cli.py             # contract loader + diff engine + render + CLI
├── scripts/
│   ├── validate.sh        # read-only validation (CI gate)
│   └── publish.sh         # validate + create_listing_change_set if clean
├── contracts/             # example YAML contracts (track these in version control)
├── out/                   # generated reports (Markdown + JSON)
├── requirements.txt
└── README.md
```

## Prerequisites

- AWS profile with at least `datazone:Get*`, `datazone:List*`, `datazone:Search*`
  for `validate`; add `datazone:CreateListingChangeSet` for `publish`
- Python 3.10+
- A DataZone domain with at least one asset registered (the seed in
  [cross-domain-lineage](../cross-domain-lineage/) creates the chocolate-mesh
  demo assets used in the examples below)

## Quick start

```bash
cd data-product-validator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# read-only -- print report and exit 1 on breaking findings
./scripts/validate.sh default contracts/cocoa_shipments.yaml

# gated publish -- only calls CreateListingChangeSet if validate is clean
./scripts/publish.sh default contracts/monthly_revenue.yaml
```

Both subcommands accept the same flags:
`--profile`, `--region`, `--domain-id`, `--domain-name`, `--out-dir`.
`publish` adds `--allow-warning`. `validate` adds `--strict` (treat warnings
as failures, useful for tightening over time without rewriting rules).

## Example output

### 1. Clean contract -> exit 0

```
$ ./scripts/validate.sh default contracts/cocoa_shipments.yaml
# Data product validation report -- cocoa_shipments

- **Domain**: chocolate-mesh (`dzd-dcoigj8fhoucuo`)
- **Project**: chocolate-producer
- **Asset id**: `3owh0wrwk5j3ww`
- **Listing**: `cgtohix57yqbyo` (ACTIVE)
- **Validated at**: 2026-05-16T14:35:01+00:00

**Findings**: breaking=0 warning=1 info=0

### 1. [WARNING] Contract expects glossary terms (Cocoa) but none are attached
- **Detail**: No glossary terms are attached to the live asset.
- **Remediation**: Attach the contracted terms via UpdateAsset / the DataZone console.
- **Finding id**: `GLOSSARY-NOT-ATTACHED`

-- wrote out/cocoa_shipments-validation.md and out/cocoa_shipments-validation.json --
exit=0
```

The warning is real (the seed in `cross-domain-lineage` doesn't attach the
glossary term), but it's not a breaking change, so the gate stays open.

### 2. Breaking contract -> exit 1, two breaking findings

```
$ ./scripts/validate.sh default contracts/cocoa_shipments_breaking.yaml
FAIL: breaking changes detected.

**Findings**: breaking=2 warning=2 info=0

### 1. [BREAKING] Column 'shipment_origin' is in the contract but missing from the live asset
- **Detail**: Contract declares column 'shipment_origin' (type=string, required=True),
  but the live asset has no such column.
- **Remediation**: Either add the column to the underlying source table and re-run the
  data source, or remove the column from the contract.
- **Finding id**: `COL-MISSING-shipment_origin`

### 2. [BREAKING] Column 'amount' type changed: contract=long, live=double
- **Detail**: Contract expects normalized type 'long'; live asset has 'double'.
  Subscribers who joined on the previous type will break.
- **Remediation**: Cast the column back to the contracted type in the source query, or
  bump the contract version after coordinating with subscribers.
- **Finding id**: `COL-TYPE-amount`

### 3. [WARNING] Column 'amount' is marked deprecated but still present
- **Detail**: Contract lists it under deprecated_columns; the live asset still ships it.

### 4. [WARNING] Contract expects glossary terms (Cocoa) but none are attached
- **Detail**: No glossary terms are attached to the live asset.

exit=1
```

### 3. Gated publish blocks the bad contract

```
$ ./scripts/publish.sh default contracts/monthly_revenue_bad.yaml
PUBLISH BLOCKED: breaking changes detected. Fix the diff or bump the contract.

**Findings**: breaking=1 warning=0 info=1

### 1. [BREAKING] Column 'amount' type changed: contract=long, live=double
...

exit=1
```

The `CreateListingChangeSet` API is **never called** in this case. The
listing on DataZone is unchanged.

### 4. Clean contract publishes the listing

```
$ ./scripts/publish.sh default contracts/monthly_revenue.yaml
**Findings**: breaking=0 warning=0 info=1

### 1. [INFO] Contract and live asset descriptions differ
...

-- published asset as listing crovj3heesqqds (rev 1) --
exit=0
```

## How it hooks into the DataZone publish lifecycle

DataZone's "publish" action is `CreateListingChangeSet(action=PUBLISH,
entityType=ASSET, entityIdentifier=<asset_id>)`. The producer normally calls
this from the console or via boto3 directly. This tool wraps that call:

```
                       ┌─────────────────────────┐
   contracts/foo.yaml ─►   load_contract()      │
                       │                         │
   GetAsset / Search ──►   fetch_live_asset()   │
                       │                         │
                       │   diff(contract, live)  │
                       │      │                  │
                       │      ▼                  │
                       │  any breaking?          │
                       │   ┌───┴───┐             │
                       │  yes      no            │
                       │   │        │            │
                       │   ▼        ▼            │
                       │  EXIT 1   CreateListingChangeSet(PUBLISH)
                       └─────────────────────────┘
```

In CI, point `validate` at every contract under `contracts/` on every PR;
in production, replace the producer's `aws datazone create-listing-change-set`
call with `dpv publish`. Same outcome on the happy path, but the gate is now
tied to a versioned spec rather than the producer's good intentions.

## Read-only by default

`validate` only calls `Get*`, `List*`, and `Search*` on DataZone -- safe to
run against any domain, including production. Only `publish` mutates state
(via `CreateListingChangeSet`), and only after the diff is clean.

## Design decisions

- **Contracts live in version control, not DataZone**: DataZone has no
  asset-schema-as-code primitive. Keeping the contract in a YAML file lets
  the team review breaking changes in a PR before they hit the catalog.
- **Type normalization is conservative**: `varchar(255)` collapses to
  `string`, `bigint` to `long`, but unknown types are kept as-is so a
  contract typo is flagged instead of being silently accepted.
- **Diff is bounded**: only column shape, types, descriptions, and glossary
  attachment. Nullable / partition / format checks would belong in a quality
  framework (Great Expectations, dbt tests) -- this validator is about the
  *contract*, not data quality.
- **Three severities, not five**: every project that grows a custom severity
  ladder ends up arguing about exactly which rule is which level. `breaking`
  blocks publish; `warning` is reviewable; `info` is informational. That's
  enough.
- **`publish` is opt-in**: `validate` exists separately so CI can run it on
  every PR without needing publish permissions.
- **Finding ids are stable**: `COL-MISSING-<name>`, `COL-TYPE-<name>`, etc.
  Easy to grep the JSON in CI to assert "this finding is no longer present"
  after a fix.
