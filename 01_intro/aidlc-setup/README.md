# AI Coding Assistant Setup (CLI)

This guide walks you through installing and authenticating four terminal-based
AI coding assistants so you can use them against the sample projects in this
book:

- **Claude Code** (Anthropic)
- **Kiro CLI** (kiro.dev, AWS-backed, spec-driven)
- **Codex CLI** (OpenAI)
- **Cursor CLI** / `cursor-agent`

Each tool is run from the shell against a chapter directory. They all read and
write files in place, run commands with your approval, and keep their state in
the repo or your home directory — no IDE required.

---

## 1. Prerequisites

Install these once; every tool below assumes them.

| Requirement        | Why                                               | Check                |
|--------------------|---------------------------------------------------|----------------------|
| macOS / Linux / WSL| All four CLIs target Unix shells                  | `uname -a`           |
| Git                | Chapters are git repos; tools read git state      | `git --version`      |
| Node.js 18+        | Claude Code, Codex, and Cursor CLIs ship via npm  | `node -v`            |
| Python 3.11+       | Chapters 02, 07, 08, 09, 12 use Python/Jupyter    | `python3 --version`  |
| AWS CLI v2         | Every chapter hits AWS                            | `aws --version`      |
| jq                 | Handy for parsing CLI output in prompts           | `jq --version`       |

Configure AWS once:

```bash
aws configure
aws sts get-caller-identity   # confirm credentials resolve
```

If you use AWS SSO, prefer `aws configure sso` and set `AWS_PROFILE` in your
shell before invoking any assistant — they all inherit your environment.

---

## 2. Claude Code

Anthropic's official terminal agent. Best default for this book: strong tool
use, good at reading long CloudFormation and SQL, and it will ask before
running shell commands.

### Install

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

### Authenticate

```bash
claude            # opens the REPL
/login            # inside the REPL — opens a browser flow
```

Alternatives for AWS-centric workflows:

```bash
# Route through Amazon Bedrock instead of the Anthropic API
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_REGION=us-east-1     # any region with Claude on Bedrock
```

### First run against a chapter

```bash
cd 02_security/tls-load-balancer
claude
```

Useful inside the REPL:

- `/help` — list commands
- `/config` — pick model, theme, permission mode
- `/permissions` — allow `aws`, `jq`, etc. without prompting every time
- `/init` — generate a `CLAUDE.md` for the folder (chapter-specific memory)

Non-interactive (one-shot) mode for scripting:

```bash
claude -p "Validate tls-alb.yaml with aws cloudformation validate-template and summarise findings."
```

### Good starter prompt for this book

```text
Read README.md and any .yaml / .py / .sql in this folder. Summarise what this
chapter deploys, the AWS services involved, and the command I run to deploy it.
Don't execute anything yet.
```

---

## 3. Kiro CLI

Kiro is AWS's spec-driven AI IDE (kiro.dev). It ships a CLI companion that
drives the same spec → design → tasks → code loop from the terminal. Best fit
for the bigger chapters where you want explicit requirements and tasks written
to disk before any code is generated — e.g. `03_datalake/iceberg-maintenance-agent`,
`05_bigdata/kiro-data-pipeline`, `12_agentic/agent`.

### Install

Follow the current instructions at <https://kiro.dev/docs/cli> (install method
changes between releases — usually a signed installer or `brew install kiro`).
Verify:

```bash
kiro --version
```

### Authenticate

```bash
kiro login        # AWS Builder ID or IAM Identity Center
```

Kiro will cache credentials under `~/.kiro/`.

### First run against a chapter

```bash
cd 05_bigdata/kiro-data-pipeline
kiro              # launches the agent in the current directory
```

Kiro's value-add on this book is the **spec-driven** mode: point it at the
chapter `README.md` as the intent, and it writes:

```
.kiro/specs/<feature>/requirements.md
.kiro/specs/<feature>/design.md
.kiro/specs/<feature>/tasks.md
```

Then it executes tasks one at a time, checking each box as it goes. This is
the same pattern used by the AI-DLC workflow in `~/.aidlc/` — Kiro is one of
the supported runners. See section 6 below for an AI-DLC fork that adds a
data-engineering extension family on top of the upstream rules.

### Good starter prompt

```text
Using spec mode, read README.md in this directory as the product intent.
Generate requirements, design, and a task list for implementing the pipeline.
Do not write code yet — stop after tasks.md for my review.
```

---

## 4. Codex CLI (OpenAI)

OpenAI's terminal agent. Useful as a second opinion, or when you want a
different model family for the same chapter. Install and auth are independent
of Claude Code — you can run both side-by-side.

### Install

```bash
npm install -g @openai/codex
# or: brew install --cask codex
codex --version
```

### Authenticate

```bash
codex login        # browser flow, stores token under ~/.codex/
```

If you prefer an API key:

```bash
export OPENAI_API_KEY=sk-...
```

### First run against a chapter

```bash
cd 07_enrichment
codex
```

One-shot mode:

```bash
codex exec "Open Intelligent_Document_Processing.ipynb and list the AWS services each cell calls."
```

### Good starter prompt

```text
Read every .ipynb and .py in this folder. Produce a table of: filename,
service called, input shape, output shape. Don't modify files.
```

---

## 5. Cursor CLI (`cursor-agent`)

Cursor is best known as an IDE, but it ships a headless agent (`cursor-agent`)
you can run from the terminal. Handy when you already pay for Cursor and want
one subscription across IDE and CLI.

### Install

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent --version
```

The installer drops the binary in `~/.local/bin`; make sure that's on your
`PATH`.

### Authenticate

```bash
cursor-agent login
```

### First run against a chapter

```bash
cd 09_streaming/stock-trading-demo
cursor-agent
```

Non-interactive:

```bash
cursor-agent -p "Explain how the Kinesis stream feeds the Lambda in this folder."
```

### Good starter prompt

```text
Treat this folder as a standalone project. Trace data flow from producer to
consumer and draw it as an ASCII diagram. Cite file:line for every arrow.
```

---

## 6. AI-DLC workflow (with data-engineering extensions)

[AI-DLC](https://github.com/awslabs/aidlc-workflows) is AWS's adaptive,
spec-driven development workflow — requirements → user stories → workflow plan
→ design → code, with the agent waiting for explicit approval at each stage.
It runs on top of any of the four CLIs above (Claude Code, Kiro, Codex, Cursor)
by reading rule files from `~/.aidlc/aidlc-rules/`.

This book ships a `data-engineering` extension family for AI-DLC — opt-in rule
packs covering pipeline baselines, S3 lakehouse (Iceberg), Glue ETL, Redshift,
orchestration (MWAA / Step Functions), data catalog, and CI/CD. They plug into
the same opt-in mechanism used by the upstream `security` and `testing`
extensions. The extension lives at the same relative path the upstream repo
uses, so installing it is a clone + copy:

```
01_intro/aidlc-setup/aidlc-workflows/
└── aidlc-rules/aws-aidlc-rule-details/extensions/data-engineering/
    ├── baseline/        catalog/      cicd/
    ├── glue-etl/        orchestration/
    ├── redshift/        s3-lakehouse/
```

### Install

Clone the upstream AI-DLC repo, then copy this book's `data-engineering`
extension into the same relative path:

```bash
# 1. Clone upstream AI-DLC (the canonical source of the core workflow)
git clone https://github.com/awslabs/aidlc-workflows.git ~/src/aidlc-workflows

# 2. Copy this book's data-engineering extension into the cloned tree
#    (run from the repo root of data-engineering-with-aws-and-generative-ai)
cp -R 01_intro/aidlc-setup/aidlc-workflows/aidlc-rules/aws-aidlc-rule-details/extensions/data-engineering \
      ~/src/aidlc-workflows/aidlc-rules/aws-aidlc-rule-details/extensions/

# 3. Stage the combined rules where the AI-DLC core workflow expects them
mkdir -p ~/.aidlc
rm -rf ~/.aidlc/aidlc-rules
cp -R ~/src/aidlc-workflows/aidlc-rules ~/.aidlc/aidlc-rules

# 4. Verify
cat ~/.aidlc/aidlc-rules/VERSION
ls  ~/.aidlc/aidlc-rules/aws-aidlc-rule-details/extensions/data-engineering
```

You should see seven extension directories: `baseline`, `catalog`, `cicd`,
`glue-etl`, `orchestration`, `redshift`, `s3-lakehouse`. Each contains a rules
file (`<name>.md`) and an opt-in prompt (`<name>.opt-in.md`).

For the latest updates to the data-engineering extension, refer to
<https://github.com/jleto/aidlc-workflows/tree/extensions-data-engineering>.

### Wire it into your CLI of choice

A user-level `~/.claude/CLAUDE.md` entry like this triggers AI-DLC when you
say "Using AI-DLC, …":

```text
When the user invokes AI-DLC (e.g., "Using AI-DLC, ..."), read and follow:
@~/.aidlc/aidlc-rules/aws-aidlc-rules/core-workflow.md
```

For Kiro, copy the rules into the project's `.kiro/steering/` directory; for
Cursor, Codex, or other agents, follow the per-tool setup in the upstream
[AI-DLC README](https://github.com/awslabs/aidlc-workflows/blob/main/README.md).

### How the data-engineering extensions activate

When you start an AI-DLC workflow, the agent scans `extensions/` and presents
each opt-in prompt during Requirements Analysis. For example, the baseline
extension asks:

```text
## Question: Data Engineering Baseline Extension
Should data engineering baseline rules be enforced for this project?
A) Yes — enforce all DATAENG rules per their P0/P1/P2 severity tiers
B) No — skip all DATAENG rules
```

Answer `A` for any chapter that produces a dataset consumed downstream
(`03_datalake`, `05_bigdata`, `06_orchestration`, `07_enrichment`,
`09_streaming`, `10_datamart`). Once enabled, the rules become **blocking
constraints** — the agent will refuse to advance a stage until verification
criteria are met (e.g., partitioning strategy declared, schema evolution
plan documented, IAM least-privilege role attached).

### Good starter prompt

```text
Using AI-DLC, treat README.md in this directory as the product intent. During
Requirements Analysis, opt IN to the data-engineering baseline, s3-lakehouse,
and orchestration extensions. Stop after Workflow Planning for my review.
```

### Choosing extensions per chapter

| Chapter                          | Recommended DE extensions                                |
|----------------------------------|----------------------------------------------------------|
| `03_datalake`                    | `baseline`, `s3-lakehouse`, `catalog`                    |
| `04_datamesh`                    | `baseline`, `catalog`, `s3-lakehouse`                    |
| `05_bigdata`                     | `baseline`, `glue-etl`, `s3-lakehouse`, `orchestration`  |
| `06_orchestration`               | `baseline`, `orchestration`, `cicd`                      |
| `07_enrichment`                  | `baseline`, `glue-etl`                                   |
| `09_streaming`                   | `baseline`, `glue-etl` (Streaming), `orchestration`      |
| `10_datamart/redshift_demo`      | `baseline`, `redshift`, `cicd`                           |

You can layer multiple extensions in the same workflow; they share the
P0/P1/P2 severity model so verification is consistent across them.

---

## 7. Choosing a tool per chapter

All four tools work on every chapter. These are pragmatic defaults based on
what each chapter needs:

| Chapter                              | Default tool     | Why                                               |
|--------------------------------------|------------------|---------------------------------------------------|
| `02_security/tls-load-balancer`      | Claude Code      | Walks CloudFormation params interactively         |
| `02_security`                        | Claude Code      | Careful with IAM/KMS; will ask before applying    |
| `03_datalake`                        | Kiro CLI         | Iceberg agent benefits from spec mode             |
| `04_datamesh`                        | Claude Code      | Lake Formation policy JSONs need close reading    |
| `05_bigdata/kiro-data-pipeline`      | Kiro CLI         | Folder is already structured for Kiro specs       |
| `06_orchestration`                   | Codex CLI        | Small Step Functions + Lambda; fast iteration     |
| `07_enrichment`                      | Codex or Claude  | Notebook-heavy; both handle .ipynb well           |
| `08_rag/s3_vectors`                  | Claude Code      | Long context for vector-store setup docs          |
| `09_streaming`                       | Cursor           | Multi-service traces across producer/consumer     |
| `10_datamart/redshift_demo`          | Claude Code      | SQL + MCP server; Claude has strong SQL reasoning |
| `11_quicksight`                      | Codex CLI        | Mostly dashboard config walkthroughs              |
| `12_agentic`                         | Claude Code      | Agent/MCP code is Anthropic-native                |

Substitute freely — the point of installing all four is to compare.

---

## 8. Cross-tool tips

**Feed both narrative and code.** Every chapter has a `README.md` (intent)
plus the actual `.yaml` / `.py` / `.sql` / `.ipynb` (ground truth). Without the
code, models hallucinate field names; without the README, they miss the goal.

**Validate before you deploy.** After any assistant edits IaC, run:

```bash
aws cloudformation validate-template --template-body file://<file>.yaml
```

or the equivalent `cdk synth` / `sam validate` / `terraform plan`.

**Review IAM and security-group diffs by hand.** These are the two places
assistants most commonly over-permit. A quick `git diff` before `aws
cloudformation deploy` has saved real money.

**Use a dedicated AWS profile for AI-driven sessions.** Scope it to the
services the chapter needs so a runaway prompt can't delete your production
Route 53 zone:

```bash
export AWS_PROFILE=book-sandbox
```

**Keep chapter-specific memory in `CLAUDE.md` / `AGENTS.md` / `.kiro/`.**
Each tool reads a different file; when you commit these, the next person (or
your next session) picks up the same context instead of rediscovering it.

**Don't mix tools in one session.** Pick one per chapter; their edit histories
don't reconcile. Start a new session when switching.

---

## 9. Verifying your setup

Run this from the repo root to confirm all four are installed and authed:

```bash
claude --version       && echo "claude ok"
kiro --version         && echo "kiro ok"
codex --version        && echo "codex ok"
cursor-agent --version && echo "cursor-agent ok"
aws sts get-caller-identity
```

If all five lines succeed, you're ready. Pick a chapter, `cd` into it, and
launch your tool of choice.
