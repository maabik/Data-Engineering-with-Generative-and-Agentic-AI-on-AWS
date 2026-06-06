# DevOps for agentic and MCP components

The other docs in this folder mostly transfer from "shipping a web app" to
"shipping a data pipeline" with adjustments. This one covers what's
genuinely new: the parts of the system where an LLM is in the loop —
either as the agent that reads, plans, and acts, or as the tool-server (MCP)
behind it.

The reference pipeline includes a Strands agent on AgentCore Runtime
fronting a Redshift-backed MCP server, mirroring `12_agentic/`. Everything
below is grounded in that example.

## Why agents break the standard DevOps loop

Three properties:

1. **Non-determinism.** Same input, different output across runs. A diff
   between two deploys can't be reduced to "code changed → behavior
   changed", because behavior changes between runs of identical code.
2. **The prompt is the most important file in the repo, and it's a string.**
   A one-character edit to a system prompt can change cost, latency, and
   correctness more than a 200-line refactor. Reviewers don't always treat
   it that way.
3. **The blast radius of a wrong action is unbounded.** A traditional
   service that accidentally returns the wrong row is a bug. An agent that
   accidentally drops a Redshift table because the system prompt didn't
   forbid it is an incident.

## The DevOps practices that change

### Version everything an LLM sees

Treat each of these as source code under version control, with PR review:

- **System prompts** — one file per prompt, never inlined in Python. Diff
  reviews of prompts catch things like "an instruction was deleted by
  accident."
- **Tool definitions** — the JSON Schema for each MCP tool, including
  descriptions, parameter docs, and the underlying SQL templates. The LLM
  uses tool descriptions to choose which tool to call; "small description
  edit" can mean "wrong tool gets called."
- **Few-shot examples** — when you give the agent example questions and
  answers in the system prompt, those examples shape behavior more than
  most parameters do.
- **Eval datasets** — input/expected-output pairs. Anyone editing them
  needs reviewer approval, same as production code.
- **Guardrail configs** — Bedrock Guardrails attached to model
  invocations. See `02_security/guardrails-from-policy/`.

The reference pipeline keeps these in:

```
reference-pipeline/agent/
├── prompts/
│   ├── sales_reporter.system.md
│   └── sql_evaluator.system.md
├── tools/
│   └── mcp_redshift_tools.json
├── evals/
│   ├── sales_questions.jsonl
│   └── expected_answers.jsonl
└── guardrails/
    └── data_pii.tfvars.json
```

A prompt change without an eval-dataset update should be a code review
yellow flag. A *new* tool without an eval that exercises it should be a
red one.

### Replace "tests pass" with "tests pass + evals pass"

Lab 9 sets this up. A simplified version:

```yaml
# .github/workflows/agent-ci.yaml
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run agent evals against sandbox
        run: |
          python evals/run.py \
            --agent-arn ${{ steps.tf.outputs.agent_arn }} \
            --dataset reference-pipeline/agent/evals/sales_questions.jsonl \
            --pass-threshold 0.9 \
            --report eval-report.json
      - uses: actions/upload-artifact@v4
        with: { path: eval-report.json }
```

Three properties to enforce in the eval runner:

- **Multiple seeds per question.** Run each question N times (e.g., 5).
  An agent that's right 4/5 isn't a regression unless the baseline was
  5/5. Pass/fail at the question level uses majority; the eval as a whole
  passes if ≥90% of questions pass.
- **Cost and latency budgets.** Fail the eval if the average cost per
  question regressed >20% or p95 latency regressed >50%, even if accuracy
  is fine. "It works, but it costs 4× more" is a regression.
- **Prompt-injection re-run.** On any change to the system prompt or tool
  list, run `02_security/prompt-injection-test-harness/`. Block deploy if
  the injection success rate increased.

### Treat the model upgrade as a deploy

Bedrock model IDs are not stable forever, and even when they are, model
behavior shifts when the underlying weights are updated. This means **a
"no-code" deploy can change behavior**.

Two practices:

- **Pin model IDs explicitly.** `claude-opus-4-7` not "claude-opus" and
  not "the latest". The Terraform variable defaults to a specific ID;
  upgrading is a PR.
- **Run the eval suite on every model upgrade PR.** The PR description has
  the diff in the eval scores per question. Treating the model itself as
  versioned content is what makes upgrades safe.

### Make the agent's tool surface a deploy artifact

The MCP server's exposed tools are part of the agent's API. If a tool
appears, disappears, or changes its argument shape, every downstream agent
that uses it might silently misbehave.

Lab 9 wires:

- **Tool inventory diff in CI.** PRs that change `mcp_redshift_tools.json`
  show a clear diff: which tools were added, removed, renamed.
- **Backward-compat checks.** Removing a tool, narrowing a parameter,
  changing a return shape — each is breaking and requires an explicit
  override in the PR.
- **Coupled deploy.** When the MCP server's tool list changes, every
  agent that uses it has its eval suite re-run before that change can ship
  to prod.

### Ship guardrails as a deploy precondition, not as documentation

`02_security/guardrails-from-policy/` ingests an AUP and produces a Bedrock
Guardrail. The pattern this chapter codebase reinforces in lab 9:

- The agent's runtime always invokes through the guardrail. There's no
  "guardrail-disabled" code path in prod.
- The Terraform that deploys the agent fails apply if the guardrail
  attachment is missing — `lifecycle { precondition }` block.
- A regression test verifies that disallowed prompts (PII leakage, denied
  topics from the AUP) get blocked. Not optional.

### Memory and state are not stateless deploy targets

AgentCore memory persists across invocations. Two consequences:

- A deploy that changes the agent's "memory schema" (what it stores per
  user, per session) needs a migration plan, the same way an Iceberg
  schema change does.
- Rolling back the agent code to an older version doesn't roll back the
  memory. If the new version wrote things in a format the old version
  can't parse, you'll find out in a customer-visible way.

The reference pipeline's deploy script versions the memory schema and
fails fast if downgrade-incompatible memory exists.

### Replays are how you debug

When an agent does the wrong thing, you need:

- The system prompt, tool definitions, and model ID it ran with — pinned
  by deploy ID.
- The full conversation transcript: user input, every tool call, every
  intermediate model response, every tool result.
- The model invocation IDs so you can correlate to Bedrock invocation logs
  (the audit trail in `02_security/bedrock-audit-trail/`).

Lab 8 wires this together: every agent invocation gets a `trace_id` that
appears in CloudWatch, in Bedrock invocation logs, and in the
audit-trail Iceberg table — so an incident triage can pull "the exact
context the agent saw at 14:32 UTC last Thursday" in one Athena query.

## Patterns to avoid

These are easy to fall into and reliably regretted:

- **Inlining prompts in Python source files.** Reviews don't surface them
  cleanly. Diffs hide whitespace changes. Use one file per prompt.
- **"The eval suite is too expensive to run on every PR".** It usually
  is — but the right fix is a smaller smoke-eval (10 questions, 2 seeds)
  that runs on every PR, plus the full eval on a schedule. Not "no eval
  on PR".
- **Storing the LLM API key as a long-lived secret in CI.** Treat it as
  identity, scope it per environment, rotate it. Lab 7 does this.
- **Letting the agent write to prod resources from a sandbox eval.** The
  evals run in sandbox; the agent's tools resolve to sandbox endpoints in
  sandbox. If the agent is configured to read prod, the eval is a prod
  read, with whatever cost / data-exposure that implies.
- **Mocking the LLM in integration tests.** Mocked LLM tests catch only
  bugs in the harness around the LLM, not in the LLM's behavior. Use real
  LLM calls in integration / eval; use recorded transcripts only in unit
  tests where you're testing the harness itself.

## How this maps to the other labs

- Lab 4 (policy-as-code) blocks deploys that put a model invocation behind
  no guardrail.
- Lab 5 (testing strategy) covers the unit / contract / integration /
  smoke layers; lab 9 covers the eval layer that sits parallel to those.
- Lab 6 (CI/CD) gates prod deploys on full-eval pass.
- Lab 7 (secrets / OIDC) scopes Bedrock invocation permissions per
  environment.
- Lab 8 (observability) wires the trace IDs, audit trail, and
  rollback-on-DQ-breach.
