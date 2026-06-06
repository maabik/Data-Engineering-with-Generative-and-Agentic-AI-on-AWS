# datamesh-maturity-assessment

Programmatic scoring of a live AWS DataZone deployment against
**Zhamak Dehghani's four data-mesh principles**, with a prioritized
remediation list.

| # | Principle | What this tool checks |
|---|---|---|
| 1 | **Domain ownership** | Number of domain units, projects parented to units (vs. all at root), explicit owners on the root unit |
| 2 | **Data as a product** | Asset count, asset description coverage, ratio of assets published as listings |
| 3 | **Self-serve data platform** | Per-project environments, per-project data sources, project membership beyond the creator |
| 4 | **Federated computational governance** | Glossary + term coverage, policy grants on the root unit (CREATE_PROJECT / CREATE_GLOSSARY), glossary terms attached to assets |

The output is a Markdown report (also written as JSON) with:
- An overall score 0-100 and a stage label (`Pre-mesh`, `Initial`, `Emerging`,
  `Operational`, `Mature`)
- A score per principle
- A prioritized remediation list (high -> medium -> low) where each finding
  links to the principle it weakens, the points it cost, and a concrete
  next step

## Why this exists

DataZone gives you a checklist of features but no opinion on whether your
deployment is *actually a data mesh*. It's easy to spin up a domain, create
one project, register a Glue table, and call it done -- which is exactly the
"centralized data lake with a different UI" anti-pattern Dehghani warned
against. This tool turns the four principles into pass/fail signals you can
read off the DataZone API, so the assessment is reproducible and runnable in
CI rather than living in someone's slide deck.

## Project layout

```
datamesh-maturity-assessment/
├── src/dmma/
│   ├── __main__.py
│   └── cli.py             # signal collection + scoring + Markdown/JSON render
├── scripts/
│   └── assess.sh          # convenience wrapper
├── requirements.txt
└── README.md
```

## Prerequisites

- AWS profile with at least `datazone:Get*`, `datazone:List*`, `datazone:Search*`
- Python 3.10+
- A DataZone domain in `AVAILABLE` status to assess

## Quick start

```bash
cd datamesh-maturity-assessment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Auto-pick the only domain in the region:
./scripts/assess.sh default

# Or name the domain explicitly:
./scripts/assess.sh default chocolate-mesh
```

The Markdown report is printed to stdout; both the Markdown and JSON
versions are written to `out/<domain-name>-assessment.{md,json}`.

## Example output (live test against `datazone-as-code` demo)

```
== assessing domain dzd-dp01yugy3pbhqo (region=us-west-2, profile=default) ==
  collected: units=2 projects=2 glossaries=1 terms=3 assets=0 listings=0

# Data Mesh Maturity Assessment -- chocolate-mesh

- Domain version: V1
- Overall score: 79.2 / 100  -- Operational

| # | Principle                              | Score | Stage      |
|---|----------------------------------------|------:|------------|
| 1 | Domain ownership                       | 100.0 | Mature     |
| 2 | Data as a product                      |  60.0 | Operational|
| 3 | Self-serve data platform               |  62.0 | Operational|
| 4 | Federated computational governance     |  95.0 | Mature     |

## Prioritized remediation list

### 1. [HIGH] (P2) No assets registered in the domain
- Detail: Without assets, there is nothing to treat as a product.
- Remediation: Create a DataZone data source on a producer project (Glue,
  Redshift, or S3) and run it to register inventory assets.
- Score impact: -40 points on principle 2

### 2. [HIGH] (P3) No project has a provisioned environment
- Detail: Without an environment, a project cannot run analytics or subscribe
  to data. Self-serve provisioning is the entire promise of principle 3.
- Remediation: Create at least one environment per active project (Athena,
  Redshift, or SageMaker blueprint).
- Score impact: -30 points on principle 3

### 3. [LOW] (P3) Every project has exactly one member
- Detail: Projects are personal sandboxes rather than shared team workspaces.
- Remediation: Add team members to each project; assign group ownership.
- Score impact: -8 points on principle 3

### 4. [LOW] (P4) Only 3 glossary term(s) defined
- Detail: A glossary with very few terms suggests early adoption.
- Remediation: Continue seeding terms; aim for >= 1 term per producer domain.
- Score impact: -5 points on principle 4
```

The breakdown is exactly what you'd expect for a fresh `datazone-as-code`
deployment: principles 1 and 4 (the structural ones the IaC tool covers --
units, projects, ownership, glossary, policy grants) score high; principles
2 and 3 (which require actual *data*: data sources, assets, listings,
environments) score lower because the IaC manifest doesn't create those yet.

## Scoring model

Each principle starts at 100. Findings deduct points based on severity:

| Severity | Points lost (typical) |
|---|---:|
| `high` | 25-40 |
| `medium` | 10-15 |
| `low` | 5-8 |

The stage label is derived from the score:

| Score range | Stage |
|---|---|
| 80-100 | Mature |
| 60-79  | Operational |
| 40-59  | Emerging |
| 20-39  | Initial |
| 0-19   | Pre-mesh |

The remediation list is sorted by severity first, then by points lost, so
you fix the biggest gaps first.

## Read-only

This tool **does not modify the DataZone domain** -- it only calls
`Get*`, `List*`, and `Search*`. Safe to run against production.

## Design decisions

- **Read-only by design**: assessment shouldn't mutate the thing being
  assessed. All API calls are idempotent reads.
- **No retry/backoff plumbing**: DataZone read APIs rarely throttle for the
  page sizes here (a few hundred items max). Adding retry would obscure the
  small number of tolerated `ValidationException`s (e.g., for V1-only policy
  types).
- **Findings have IDs (`P1-NO-UNITS` etc.)**: stable identifiers make it
  easy to grep the JSON in CI and assert that specific findings are no
  longer present after a change.
- **JSON + Markdown both written**: Markdown for humans / PR review, JSON
  for piping into dashboards or threshold checks.
