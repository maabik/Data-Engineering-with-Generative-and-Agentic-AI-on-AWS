# datazone-as-code

Declarative **YAML manifest → AWS DataZone**. Closes the IaC gap that DataZone
notoriously has: CloudFormation/CDK only cover a thin slice (mainly `Domain`
and `Project`), leaving the bulk of mesh setup -- ownership, policy grants,
glossaries, terms -- as click-ops.

This project applies a single YAML file end-to-end: it bootstraps the IAM
domain execution role, creates the domain, registers the caller as a domain
unit owner, grants the minimum policy grants needed to provision children,
then walks the manifest to create domain units, projects, glossaries, and
glossary terms. Every step is **idempotent** -- existing resources are
re-used, conflicts are swallowed, and partial state can always be reconciled
by re-running `apply`.

## What this solves

```
Without dzac                          With dzac
─────────────                         ─────────
1. Click 'Create domain' in console   1. Edit manifest
2. Click 'Create role' for domain     2. ./scripts/apply.sh
3. Click 'Create user profile'
4. Click 'Add owner' on root unit
5. Click 'Add policy grant' x N
6. Click 'Create domain unit' x N
7. Click 'Create project' x N
8. Click 'Add policy grant' for       Re-runnable. Reviewable in PR.
   each owning project                Tearable down with one command.
9. Click 'Create glossary'
10. Click 'Create term' x N
```

## Manifest format

```yaml
domain:
  name: chocolate-mesh           # required
  description: ...               # optional
  version: V1                    # V1 (default) | V2 (also requires service_role)
  service_role: arn:aws:iam::...:role/...   # only for V2 domains
  execution_role: arn:aws:iam::...:role/... # optional; auto-created if omitted
  tags:
    project: datazone-as-code

domain_units:                    # optional, all parented to root
  - key: production              # local handle used by 'projects[].domain_unit'
    name: production

projects:
  - key: producer                # local handle used by 'glossaries[].owning_project'
    name: chocolate-producer
    description: ...
    domain_unit: production      # optional; defaults to root unit
  - key: consumer
    name: chocolate-consumer
    domain_unit: analytics

glossaries:
  - key: business-terms
    name: BusinessTerms
    description: ...
    owning_project: producer     # must reference a project key from above
    terms:
      - name: Revenue
        short_description: ...
        long_description: ...    # optional
      - name: Customer
        short_description: ...
```

## Project Layout

```
datazone-as-code/
├── manifests/
│   ├── example.yaml             # producer-side demo manifest
│   └── example-consumer.yaml    # consumer-side demo manifest (separate AWS account)
├── src/dzac/
│   ├── __main__.py
│   └── cli.py                   # apply / destroy
├── scripts/
│   ├── apply.sh
│   └── teardown.sh
├── requirements.txt
└── README.md
```

## Prerequisites

- AWS CLI profile(s) with `AmazonDataZoneFullAccess` (or equivalent)
- IAM permissions to create the domain execution role
- Python 3.10+
- A region that supports DataZone (this project defaults to `us-west-2`)

## Quick start

```bash
cd datazone-as-code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# producer side -- in 'default' profile
./scripts/apply.sh default

# consumer side -- in 'default-consumer' profile
./scripts/apply.sh default-consumer manifests/example-consumer.yaml
```

`apply` is fully idempotent: re-run it and every step prints `reuse` instead
of `create`.

## What gets created

For the example manifest applied against `default`:

| Resource | Identifier |
|---|---|
| IAM role `DZACDomainExecutionRole` | trust: `datazone.amazonaws.com`; managed policy `AmazonDataZoneDomainExecutionRolePolicy` |
| DataZone domain `chocolate-mesh` | V1, with the `project` and `env` tags from the manifest |
| DataZone user profile | for the calling IAM principal |
| Owner association | caller is `OWNER` of the root domain unit |
| Policy grants on root unit | `CREATE_PROJECT`, `CREATE_DOMAIN_UNIT` (inherited by children) |
| Domain units | `production`, `analytics` |
| Projects | `chocolate-producer` (under `production`), `chocolate-consumer` (under `analytics`) |
| Per-project policy grant | `CREATE_GLOSSARY` granted to the owning project |
| Glossary | `BusinessTerms` (owned by `chocolate-producer`) |
| Glossary terms | `Revenue`, `Customer`, `Cocoa` |

For the consumer manifest, you get an analogous tree in the consumer account
with a separate domain (`chocolate-mesh-consumer`) and its own projects and
glossary.

## Teardown

```bash
./scripts/teardown.sh default
./scripts/teardown.sh default-consumer manifests/example-consumer.yaml
```

`destroy` calls `delete_domain(skipDeletionCheck=true)`, which **cascades**
through projects, domain units, glossaries, terms, environments, owner
associations, and policy grants. The auto-created IAM role is removed last.
Pass `--keep-role` (via the underlying `python -m dzac destroy ...`) to leave
the role in place.

## Verification

After `apply`, list resources directly from the API:

```bash
aws datazone list-domains --profile default --region us-west-2
aws datazone list-projects --domain-identifier <domain-id> --profile default --region us-west-2
```

After `destroy`, both should return an empty `items` list.

## Design decisions

- **V1 default**: V1 only requires the domain execution role; V2 additionally
  requires a service role and is configured for project-profile-based project
  creation. V1 is sufficient for the manifest schema this tool exposes.
- **Caller-as-owner**: the IAM principal that runs `apply` is registered as a
  domain user profile and granted ownership of the root domain unit. This
  mirrors what the AWS console does behind the scenes when you click "Create
  domain" with the same principal.
- **Policy grants are scoped to the root unit with `includeChildDomainUnits=true`**:
  one grant covers every nested unit, instead of repeating the grant per unit.
- **`CREATE_GLOSSARY` is per-project**: DataZone only accepts a `project`
  principal for glossary policy grants, so the tool issues one grant per
  owning project.
- **No `delete_project` / `delete_glossary` calls in destroy**: the cascading
  `delete_domain(skipDeletionCheck=true)` removes everything in one call;
  attempting individual deletes first just slows teardown and adds error
  surface for partial-deletion races.
