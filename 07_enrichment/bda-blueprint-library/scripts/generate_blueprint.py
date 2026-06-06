#!/usr/bin/env python3
"""Generate a BDA custom blueprint from a sample document or a text description.

Two input modes:

  --from-document <path>   PDF or PNG/JPG of a representative sample
  --from-description <text>  Free-text description of the doc type and
                             the fields you care about

Both produce blueprints/<slug>.json in the same JSON-schema-style format
BDA expects (top-level class/description/properties, every leaf with
inferenceType=explicit and an instruction). Then optionally register
the result with --register.

The model is Claude Sonnet 4.6 on Bedrock. We include the existing
invoice.json and msa.json blueprints as few-shots so the output stays
compatible with what the rest of this project already deploys.

Retries up to --max-retries times on JSON parse or schema-validation
failure, feeding the error back to the model each time.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT_DIR = ROOT / "blueprints"
BUILD_DIR = ROOT / "build"

MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MAX_TOKENS = 8000

SYSTEM_PROMPT = """You generate Amazon Bedrock Data Automation (BDA) custom blueprints.

A BDA custom blueprint is a JSON document with this shape:

{
  "class": "<PascalCaseClassName>",
  "description": "<one sentence explaining the document type>",
  "definitions": {                                  // optional
    "<ITEM_DEF_NAME>": {
      "properties": { "<field>": { "type": "...", "inferenceType": "explicit", "instruction": "..." }, ... }
    }
  },
  "properties": {
    "<scalar_field>": {
      "type": "string" | "number" | "boolean",
      "inferenceType": "explicit",
      "instruction": "<concrete extraction guidance>"
    },
    "<array_field>": {
      "type": "array",
      "instruction": "<what each item is>",
      "items": { "$ref": "#/definitions/<ITEM_DEF_NAME>" }
    }
  }
}

Hard rules:
- Output ONLY the blueprint JSON. No prose, no markdown fences, no commentary.
- Every leaf scalar property MUST have type, inferenceType="explicit", and a non-empty instruction.
- snake_case field names; PascalCase class name; ALL_CAPS definition names.
- Prefer 12-25 well-chosen scalar fields; only add an array property when the document genuinely contains a repeating row structure (line items, schedules, parties).
- Each instruction should describe WHERE on the document the field appears and WHAT format it takes (date format, currency, etc.).
- Do not invent fields that wouldn't plausibly be on the document. Quality > coverage.
"""

FEW_SHOT_FILES = ["invoice.json", "msa.json"]


def _load_few_shots() -> str:
    examples = []
    for fn in FEW_SHOT_FILES:
        body = (BLUEPRINT_DIR / fn).read_text().strip()
        examples.append(f"<example name='{fn}'>\n{body}\n</example>")
    return "\n\n".join(examples)


def _validate(blueprint: dict) -> list[str]:
    errs: list[str] = []
    for k in ("class", "description", "properties"):
        if k not in blueprint:
            errs.append(f"missing top-level key: {k}")
    cls = blueprint.get("class", "")
    if not isinstance(cls, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", cls):
        errs.append(f"invalid class name: {cls!r}")
    props = blueprint.get("properties", {})
    if not isinstance(props, dict) or not props:
        errs.append("properties must be a non-empty object")
        return errs
    for name, spec in props.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            errs.append(f"property {name!r} must be snake_case")
        if not isinstance(spec, dict):
            errs.append(f"property {name!r} must be an object")
            continue
        t = spec.get("type")
        if t not in ("string", "number", "boolean", "array"):
            errs.append(f"property {name!r} type must be string|number|boolean|array, got {t!r}")
            continue
        if t == "array":
            if "items" not in spec:
                errs.append(f"array property {name!r} missing items")
            ref = spec.get("items", {}).get("$ref", "")
            if ref and not ref.startswith("#/definitions/"):
                errs.append(f"array property {name!r} items.$ref must point at #/definitions/")
            if not spec.get("instruction"):
                errs.append(f"array property {name!r} missing instruction")
        else:
            if spec.get("inferenceType") != "explicit":
                errs.append(f"property {name!r} must set inferenceType='explicit'")
            if not spec.get("instruction"):
                errs.append(f"property {name!r} missing instruction")
    # Walk definitions if present
    for dname, dspec in (blueprint.get("definitions") or {}).items():
        for fname, fspec in (dspec.get("properties") or {}).items():
            if fspec.get("inferenceType") != "explicit":
                errs.append(f"definition {dname}.{fname}: inferenceType='explicit' required")
            if not fspec.get("instruction"):
                errs.append(f"definition {dname}.{fname}: instruction required")
    return errs


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = text.rstrip("`").rstrip()
        if text.endswith("```"):
            text = text[: -3].rstrip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def _build_user_blocks(args, attempt_feedback: str | None) -> list[dict]:
    blocks: list[dict] = []

    if args.from_document:
        path = Path(args.from_document).expanduser().resolve()
        suffix = path.suffix.lower()
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        if suffix == ".pdf":
            blocks.append(
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                }
            )
        else:
            mt = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "gif": "image/gif", "webp": "image/webp"}.get(suffix.lstrip("."), "image/jpeg")
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}})

    intent_lines = [
        "Generate a BDA custom blueprint for the document above." if args.from_document
        else f"Generate a BDA custom blueprint for: {args.from_description}",
        "",
        f"Use class name {args.class_name!r}." if args.class_name else "",
        "Here are two example blueprints in the exact format you must produce:",
        "",
        _load_few_shots(),
        "",
        "Return ONLY the new blueprint JSON, no commentary, no markdown fences.",
    ]
    if attempt_feedback:
        intent_lines += [
            "",
            "Your previous attempt failed validation with these errors:",
            attempt_feedback,
            "Fix every issue and return the corrected JSON.",
        ]
    blocks.append({"type": "text", "text": "\n".join(l for l in intent_lines if l is not None)})
    return blocks


def _invoke(client, blocks: list[dict]) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": blocks}],
    }
    resp = client.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"]


def _register(blueprint_path: Path, slug: str, region: str) -> str:
    bda = boto3.client("bedrock-data-automation", region_name=region)
    name = f"BdaLib-Gen-{slug.replace('_', '-')}"
    schema_text = json.dumps(json.loads(blueprint_path.read_text()))
    paginator = bda.get_paginator("list_blueprints")
    existing: dict[str, str] = {}
    for page in paginator.paginate(resourceOwner="ACCOUNT"):
        for bp in page.get("blueprints", []):
            existing[bp["blueprintName"]] = bp["blueprintArn"]
    if name in existing:
        bda.update_blueprint(blueprintArn=existing[name], schema=schema_text, blueprintStage="LIVE")
        arn = existing[name]
        print(f"[bda] updated {name}")
    else:
        resp = bda.create_blueprint(blueprintName=name, type="DOCUMENT", blueprintStage="LIVE", schema=schema_text)
        arn = resp["blueprint"]["blueprintArn"]
        print(f"[bda] created {name}")
    return arn


def main() -> int:
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-document", help="Path to a sample PDF / PNG / JPG")
    src.add_argument("--from-description", help="Free-text description")
    p.add_argument("--name", required=True, help="Output slug; written to blueprints/<slug>.json")
    p.add_argument("--class-name", help="Override class name in the generated schema")
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--register", action="store_true", help="Register with BDA after generating")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.name):
        sys.exit(f"--name must be snake_case, got {args.name!r}")

    bedrock = boto3.client("bedrock-runtime", region_name=args.region)
    feedback: str | None = None
    blueprint: dict | None = None
    for attempt in range(1, args.max_retries + 1):
        print(f"[gen] attempt {attempt}")
        text = _invoke(bedrock, _build_user_blocks(args, feedback))
        try:
            candidate = _extract_json(text)
        except json.JSONDecodeError as e:
            feedback = f"Output was not valid JSON: {e}. First 200 chars: {text[:200]!r}"
            print(f"[gen]   {feedback}")
            continue
        errs = _validate(candidate)
        if errs:
            feedback = "\n".join(f"- {e}" for e in errs)
            print(f"[gen]   validation failures:\n{feedback}")
            continue
        blueprint = candidate
        break

    if blueprint is None:
        sys.exit("Failed to generate a valid blueprint after retries")

    out = BLUEPRINT_DIR / f"{args.name}.json"
    out.write_text(json.dumps(blueprint, indent=2) + "\n")
    print(f"[gen] wrote {out}  (class={blueprint['class']}, fields={len(blueprint['properties'])})")

    if args.register:
        arn = _register(out, args.name, args.region)
        # Append into build/blueprint-arns.json if it exists so run_tests.py sees it.
        cfg_path = BUILD_DIR / "blueprint-arns.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            cfg.setdefault("blueprints", {})[args.name] = arn
            cfg_path.write_text(json.dumps(cfg, indent=2))
            print(f"[gen] appended ARN to {cfg_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
