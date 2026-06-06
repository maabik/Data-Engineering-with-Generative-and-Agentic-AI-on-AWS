#!/usr/bin/env python3
"""SQL Evaluations Agent.

Reviews SQL submitted by another agent for three concerns:
  1. Correctness — likely runtime errors (missing FROM, ambiguous columns,
     parser-fragile syntax).
  2. Optimization — anti-patterns (SELECT *, leading-wildcard LIKE, missing
     LIMIT on exploratory queries, full table scans, cross joins).
  3. Security — injection vectors, destructive statements, broken role
     hygiene, secrets-in-SQL.

Returns a verdict plus a `revised_sql` if the agent rewrote it. After every
invocation the agent appends a one-line lesson to a steering doc on S3 so
later runs continue learning.

The agent is hosted on Bedrock AgentCore Runtime and is also exposed as an
MCP tool through AgentCore Gateway via a Lambda target.
"""

import json
import os
import re
from datetime import datetime, timezone

import boto3
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
STEERING_BUCKET = os.environ["STEERING_BUCKET"]
STEERING_KEY = os.environ.get("STEERING_KEY", "steering-doc.md")
MAX_STEERING_BYTES = 64 * 1024

s3 = boto3.client("s3", region_name=REGION)


def _read_steering_doc() -> str:
    try:
        body = s3.get_object(Bucket=STEERING_BUCKET, Key=STEERING_KEY)["Body"].read()
        return body.decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return ""


def _append_steering_lesson(lesson: str) -> None:
    lesson = lesson.strip()
    if not lesson:
        return
    current = _read_steering_doc()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    appended = f"{current.rstrip()}\n- [{stamp}] {lesson}\n"
    if len(appended.encode("utf-8")) > MAX_STEERING_BYTES:
        # Keep header + last ~500 lines so the doc stays bounded.
        lines = appended.splitlines()
        header, body = [], lines
        for i, line in enumerate(lines):
            if line.startswith("- ["):
                header, body = lines[:i], lines[i:]
                break
        body = body[-500:]
        appended = "\n".join(header + body) + "\n"
    s3.put_object(
        Bucket=STEERING_BUCKET,
        Key=STEERING_KEY,
        Body=appended.encode("utf-8"),
        ContentType="text/markdown",
    )


@tool
def get_steering_doc() -> str:
    """Return the cumulative steering doc the agent has written for itself."""
    doc = _read_steering_doc()
    return doc or "(steering doc empty)"


@tool
def record_lesson(lesson: str) -> str:
    """Append a one-line lesson to the steering doc. Use this at the END of
    every evaluation to capture a non-obvious finding worth remembering for
    the next invocation. Keep it under 200 characters and actionable."""
    _append_steering_lesson(lesson)
    return "lesson recorded"


SYSTEM_PROMPT = """You are a SQL Evaluations Agent. Another agent generated a
SQL statement and is asking you for a separation-of-concerns review. You must
analyze it for THREE distinct concerns and decide whether to APPROVE the SQL
as-is or REVISE it.

## Concerns
1. **errors** — syntax or runtime issues likely to fail (missing FROM,
   ambiguous joins, parser-fragile constructs, type mismatches you can
   reasonably infer from the SQL alone).
2. **optimizations** — anti-patterns hurting performance or cost: `SELECT *`
   when columns are known, leading-wildcard LIKE, missing LIMIT on
   exploratory queries, cartesian/cross joins, unfiltered aggregates over
   wide partitioned tables, redundant subqueries, missing predicate
   pushdown.
3. **security** — destructive statements outside the requested operation
   (DROP/TRUNCATE/DELETE without WHERE, DDL when DML was asked for),
   apparent injection vectors (string concatenation of unparameterized
   values, comments terminating WHERE), credential or PII leakage, missing
   schema qualification leading to search-path attacks.

## Workflow
1. Call `get_steering_doc` ONCE at the start to load lessons learned from
   prior invocations. Treat each entry as guidance, not as a hard rule.
2. Analyze the SQL. Decide a status: `approved`, `revised`, or `rejected`.
   - `approved`: SQL is fine; revised_sql is null.
   - `revised`: you fixed real issues; revised_sql is the rewritten SQL.
   - `rejected`: SQL is unsafe or unfixable; revised_sql is null and the
     reasoning explains why.
3. Call `record_lesson` exactly ONCE at the end with a short, actionable
   takeaway if the review surfaced anything non-obvious. Skip if there is
   nothing new to learn.
4. Return ONLY a single JSON object on the final assistant turn with this
   exact shape:

```json
{
  "status": "approved | revised | rejected",
  "findings": {
    "errors":        [{"severity": "low|medium|high", "issue": "...", "fix": "..."}],
    "optimizations": [{"severity": "low|medium|high", "issue": "...", "fix": "..."}],
    "security":      [{"severity": "low|medium|high", "issue": "...", "fix": "..."}]
  },
  "revised_sql": "string or null",
  "reasoning": "1-3 sentence summary of the decision"
}
```

No prose outside the JSON. No markdown fences. Findings arrays may be empty.
If you cannot evaluate (e.g. input is not SQL) return status `rejected` with
a single `errors` finding explaining why.
"""

app = BedrockAgentCoreApp()


def create_agent() -> Agent:
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(
        model=model,
        tools=[get_steering_doc, record_lesson],
        system_prompt=SYSTEM_PROMPT,
    )


def _extract_json(text: str) -> dict:
    """Find the outermost balanced JSON object in `text` and parse it.

    The agent is instructed to return *only* a JSON object on its final turn,
    but markdown fences or stray prose can still leak in. We scan for the
    first `{`, then walk forward tracking brace depth (ignoring braces inside
    string literals) until the matching `}` closes it.
    """
    start = text.find("{")
    if start == -1:
        return _bad("agent returned no JSON", text[:200])
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as e:
                    return _bad(f"JSON parse error: {e}", text[start : i + 1][:400])
    return _bad("unterminated JSON object", text[start : start + 200])


def _bad(issue: str, sample: str = "") -> dict:
    finding = {"severity": "high", "issue": issue, "fix": "retry"}
    if sample:
        finding["sample"] = sample
    return {
        "status": "rejected",
        "findings": {"errors": [finding], "optimizations": [], "security": []},
        "revised_sql": None,
        "reasoning": "agent output was not valid JSON",
    }


@app.entrypoint
def invoke(payload):
    sql = payload.get("sql") or payload.get("prompt") or ""
    context = payload.get("context") or ""
    dialect = payload.get("dialect") or "ansi"

    if not sql.strip():
        return {
            "status": "rejected",
            "findings": {"errors": [{"severity": "high", "issue": "no SQL provided", "fix": "include `sql` field in payload"}],
                         "optimizations": [], "security": []},
            "revised_sql": None,
            "reasoning": "empty input",
        }

    user_msg = (
        f"Dialect: {dialect}\n"
        f"Context (optional): {context}\n\n"
        f"SQL to review:\n```sql\n{sql}\n```"
    )
    agent = create_agent()
    response = agent(user_msg)
    text = response.message["content"][-1].get("text", "")
    return _extract_json(text)


if __name__ == "__main__":
    app.run()
