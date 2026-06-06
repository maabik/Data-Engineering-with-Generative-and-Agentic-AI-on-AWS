# Lab 9 — Agentic and MCP DevOps

The patterns from
[`../../docs/agentic-devops-patterns.md`](../../docs/agentic-devops-patterns.md)
made operational. This lab is what changes when the pipeline you're
shipping has an LLM in the loop — either as the agent (Strands on
AgentCore) or as the tool-server (MCP).

This lab adds:

1. **Versioned prompts**, with reviewer requirements (CODEOWNERS hits
   `**/prompts/`).
2. **An eval suite** that runs in CI and gates merges.
3. **Prompt-injection regression** wired to
   [`../../02_security/prompt-injection-test-harness/`](../../02_security/prompt-injection-test-harness/).
4. **A guardrail-attachment precondition** in the agent's Terraform.
5. **MCP server deploy** patterns: tool-inventory diff, backward-compat
   gate.

## Layout

```
labs/09-agentic-and-mcp-devops/
├── prompts/
│   └── sales_reporter.system.md
├── tools/
│   └── mcp_redshift_tools.json
├── evals/
│   ├── sales_questions.jsonl
│   └── run_evals.py
├── injection-suite/
│   └── run_injection_regression.sh
├── terraform/
│   └── agent.tf            # with the guardrail precondition
└── ci/
    ├── eval.yml            # GitHub Actions workflow
    └── tool-inventory-diff.py
```

## 1. Versioned prompts

Each prompt is its own `.md` file, never inlined in Python. `CODEOWNERS`
in lab 1 routes any change under `**/prompts/` to `@your-org/agent-platform`.

[`prompts/sales_reporter.system.md`](prompts/sales_reporter.system.md)
is the system prompt for the reference pipeline's agent. Notice three
non-default things in it:

- An explicit `# Tools` section that lists which tools to call when —
  changing tools means changing the prompt.
- A `# Refusals` section that mirrors the guardrail's denied topics, so
  the agent doesn't try to answer something the guardrail will then
  block (better UX than guardrail-induced empty responses).
- A `# Output format` section with a concrete example. Without this,
  output formats drift across model versions and break downstream
  parsers.

## 2. Eval suite

The eval runner [`evals/run_evals.py`](evals/run_evals.py) reads
`sales_questions.jsonl`, runs each question N seeds against the
deployed agent, and emits a JSON report. Pass criteria:

- ≥ 90% of questions pass (per-question pass = majority of seeds answer
  within tolerance).
- p95 latency regression ≤ 50% vs the baseline checked into the repo.
- Average cost per question regression ≤ 20%.

CI runs this against a deployed sandbox agent on every PR that touches
`prompts/`, `tools/`, or `evals/`. See
[`ci/eval.yml`](ci/eval.yml).

[`evals/baseline.json`](evals/baseline.json) is the committed baseline.
Approving a regression is an explicit step (lab 6's release.yml asks for
the override before applying).

## 3. Prompt-injection regression

Every change to the system prompt or tool list re-runs the
210-payload injection suite from
[`../../02_security/prompt-injection-test-harness/`](../../02_security/prompt-injection-test-harness/).

[`injection-suite/run_injection_regression.sh`](injection-suite/run_injection_regression.sh)
runs the suite, compares the success rate against the previous deploy,
and fails the build if it regressed.

The success rate is captured in CloudWatch as a metric so it shows up on
the lab 8 dashboard.

## 4. Guardrail as a deploy precondition

[`terraform/agent.tf`](terraform/agent.tf) wires the guardrail attachment
as a `lifecycle.precondition` block:

```hcl
resource "aws_bedrockagent_agent" "sales_reporter" {
  # ...

  lifecycle {
    precondition {
      condition     = var.guardrail_id != null && var.guardrail_id != ""
      error_message = "Cannot deploy sales_reporter without a Bedrock Guardrail attached. See 02_security/guardrails-from-policy/."
    }
  }
}
```

Apply fails fast if the guardrail isn't there. This is the gate that
makes "production agent without a guardrail" a deploy error, not an
oversight.

## 5. MCP server: tool-inventory diff

The agent calls a Redshift-backed MCP server (the same one in
`12_agentic/mcp/02-hosting-MCP-server/`). The tool surface is part of
the agent's API.

[`ci/tool-inventory-diff.py`](ci/tool-inventory-diff.py) compares the
MCP server's exposed tool list before vs after a PR's changes. Output
shapes:

- Added tools — informational.
- Removed tools — fail unless `[breaking-mcp]` is in the PR title.
- Renamed parameters — fail unless `[breaking-mcp]` is in the PR title.
- Narrowed parameter types — fail unconditionally; widening is fine.

Wire as a CI step on PRs touching `mcp/` or the tool definitions.

## What "done" looks like

- A PR that edits `prompts/sales_reporter.system.md` triggers an eval
  run; failure blocks merge.
- A PR that adds a new tool to the MCP server appears in the tool
  inventory diff with reviewer call-out.
- A PR removing a tool requires a `[breaking-mcp]` title prefix.
- A `terraform apply` of the agent without a guardrail ARN fails fast.
- The injection-suite success rate over time is tracked in CloudWatch
  and reviewed during quarterly security reviews.

## Common pitfalls

- **Eval suites that are deterministic by accident.** If the LLM
  always returns the same answer, you're not testing the LLM, you're
  testing your fixture cache. Use a small `temperature` (0.2-ish) and
  ≥3 seeds per question.
- **Eval cost runaway.** A 200-question suite × 5 seeds × every PR =
  big bill. Two strategies: smoke-eval on PR (10–20 questions, 2
  seeds), full eval on a schedule. Or, gate the full eval on whether
  the PR actually touched prompt/tool files.
- **Guardrail attached but not invoked.** The agent's runtime config
  must invoke through the guardrail. Verify with a regression test
  that disallowed prompts get blocked — don't trust the attachment
  alone.
- **MCP tool descriptions edited without intent.** "Cleaning up" a
  description can change which tool the model picks for an ambiguous
  question. Treat description edits as semantically meaningful;
  re-run evals.
- **Agent code that swallows tool errors.** A failing tool call should
  surface to the deploy's structured logs (via the `deploy_id` /
  `trace_id` from lab 8), not silently fall back to "I'm sorry, I
  can't help with that" — which masks the bug.
