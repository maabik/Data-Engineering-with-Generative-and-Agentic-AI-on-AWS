# Lab 1 — Version-control foundations

The thing that derails most "let's adopt DevOps" pushes on data teams isn't
CI/CD configuration. It's the version-control hygiene underneath: which
branch is canonical, who owns which paths, what a clean commit looks like,
how Glue jobs and DAGs and Terraform get versioned together (or apart) when
they're stored in the same repo as notebooks.

This lab establishes the conventions every other lab assumes.

## What you'll set up

1. A **trunk-based** branching model tuned for data work.
2. A **monorepo vs polyrepo** decision (with the criteria, not just the
   pick).
3. **`CODEOWNERS`** that requires the right reviewer for IaC, DAGs, and
   prompts.
4. **Conventional commits** with a commitlint hook.
5. **Semantic versioning** of pipeline artifacts (Glue scripts, DAG bundles,
   agent prompts).

---

## 1. Trunk-based branching, adapted for data

The default rule: one long-lived branch (`main`), short-lived feature
branches off it, merged via PR. Releases tag `main`. No `develop`, no
`release/*`, no `hotfix/*`.

What's different for data:

- **Sandboxes are per-branch, not per-environment.** A feature branch
  spawns its own sandbox AWS environment (lab 3); merging to `main` deploys
  to staging; tagging a release deploys to prod. There's no "develop"
  pseudo-environment.
- **Schema migrations get their own commit.** A PR that changes both code
  and an Iceberg / Redshift schema is hard to review and harder to roll
  back. Split: schema migration in commit A, code that uses it in commit B,
  both in the same PR.
- **DAG / state-machine changes are not refactors.** A PR titled "refactor
  Step Functions" should be split into the behavior-preserving structure
  changes and the actual logic changes. State-machine diffs that mix both
  are how silent data duplication ships.

## 2. Monorepo vs polyrepo

The decision matrix that holds up in practice:

| Pick **monorepo** when... | Pick **polyrepo** when... |
|---|---|
| One platform team owns the data stack end-to-end | Multiple teams ship independently with different release cadences |
| Same Terraform state graph is referenced across services | Each service has its own state and IAM blast radius |
| You want one CI run to validate cross-service changes | CI cost / time is dominated by "running checks on unrelated code" |
| Refactors cross service boundaries often | Service interfaces are stable enough that cross-repo PRs are rare |

Most data orgs that say "polyrepo" actually have *3–5 repos pretending to
be polyrepo, with cross-repo PRs every week*. That's the worst of both. If
you can't commit to true polyrepo discipline (independent releases, stable
contracts), monorepo wins.

This chapter codebase is structured as a monorepo within
`devops-data-engineering/`. The lab applies either way.

## 3. CODEOWNERS

Drop [`CODEOWNERS`](CODEOWNERS) at the root of your repo (GitHub looks at
`.github/CODEOWNERS`, the root, or `docs/CODEOWNERS` — pick one and stick
with it).

The shape:

```
# Default — at least one platform engineer reviews any change
*                                      @your-org/data-platform

# IaC — explicit ownership, never the default
**/*.tf                                @your-org/data-platform-iac
**/*.tfvars                            @your-org/data-platform-iac
reference-pipeline/cdk/                @your-org/data-platform-iac

# Glue / PySpark — data engineers own the transforms
reference-pipeline/glue_jobs/          @your-org/data-eng

# DAGs / state machines — orchestration owners
reference-pipeline/terraform/modules/orchestration/  @your-org/data-eng @your-org/data-platform

# Prompts and agent configs — ML / agent reviewers required
**/prompts/                            @your-org/agent-platform
**/tools/                              @your-org/agent-platform
**/evals/                              @your-org/agent-platform

# Security-sensitive paths — security review required
**/iam/                                @your-org/data-platform @your-org/security
**/guardrails/                         @your-org/agent-platform @your-org/security
```

GitHub's branch protection lets you require CODEOWNERS approval on any PR
touching matched paths. Turn this on. Without it, CODEOWNERS is decorative.

## 4. Conventional commits

Adopt [Conventional Commits](https://www.conventionalcommits.org/) for
machine-parseable changelog generation and to gate PR merge titles. The
shape:

```
feat(curate-orders): exclude refunded line items from revenue
fix(sfn): retry curate-orders on transient Glue throttling
chore(terraform): bump aws provider to 5.50
docs(labs/06): clarify OIDC role naming
```

Commitlint config lives in [`commitlint.config.js`](commitlint.config.js).
Wire it as a pre-commit hook in lab 2.

## 5. Semantic versioning of pipeline artifacts

Glue scripts, Step Functions ASL, DAG bundles, and agent prompts deploy
as **versioned artifacts**, not as "whatever's at HEAD when CI ran." Two
practices:

- **Tag releases.** `v1.4.2` on `main` → CI builds and uploads versioned
  artifacts (Glue script to S3 with version key, AgentCore prompt with
  version label). Lab 6 wires the build.
- **Reference versions in IaC.** The Terraform `aws_glue_job` references
  `s3://scripts/curate_orders/v1.4.2.py`, not `curate_orders.py`. Rolling
  back is a Terraform diff that points at an older version key.

The reference pipeline currently uses `etag = filemd5(...)` so the script
re-uploads on change — that's the simple version. The "semantic versioning"
upgrade is: CI uploads to a versioned S3 prefix on tag, Terraform reads the
version from a tfvars value. Lab 6 includes the workflow.

---

## What "done" looks like

- Branch protection on `main` requires PR review, status checks, and
  CODEOWNERS approval.
- A PR touching `reference-pipeline/glue_jobs/` requires a `data-eng`
  reviewer; one touching `**/*.tf` requires a `data-platform-iac` reviewer.
- A commit titled `wip` is rejected by commitlint before push.
- The README in your repo's root explains where the trunk lives, where
  sandboxes spawn from, and which paths require which reviewers.

---

## Common pitfalls

- **CODEOWNERS without branch protection.** GitHub will assign reviewers
  but won't enforce them. Always pair the two.
- **Long-lived `develop` branch.** Inevitably gets out of sync with `main`,
  produces a backlog of merge conflicts, and fragments the deploy story.
  Don't add it.
- **Notebooks committed with outputs.** Diffs become unreviewable and
  rendered images bloat the repo. Lab 2 ships a hook to strip outputs.
- **Squash-merging schema migrations together with code.** Splits the
  bisect surface — when something breaks two weeks later, you can't tell
  whether the schema or the code is at fault. Keep them as separate
  commits within the PR.
