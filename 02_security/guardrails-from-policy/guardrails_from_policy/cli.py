"""CLI entry point: policy2guardrail"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="policy2guardrail",
        description=(
            "Ingest a corporate policy document (PDF/DOCX/TXT) and generate or deploy "
            "a matching Amazon Bedrock Guardrail."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run: show the Bedrock API config without deploying
  policy2guardrail policy.pdf --dry-run

  # Parse only: output the structured policy JSON
  policy2guardrail policy.pdf --parse-only

  # Deploy to Bedrock
  policy2guardrail policy.pdf --region us-east-1

  # Add KMS encryption and resource tags
  policy2guardrail policy.pdf \\
    --kms-key-id arn:aws:kms:us-west-2:123456789012:key/mrk-abc123 \\
    --tag Environment=prod --tag Team=security
""",
    )
    parser.add_argument("policy_file", help="Path to policy document (.pdf, .docx, or .txt)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--model-id",
        default="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        help="Bedrock model ID to use for policy extraction",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the Bedrock API config without creating the guardrail",
    )
    parser.add_argument(
        "--parse-only", action="store_true",
        help="Stop after parsing: print the structured policy JSON and exit",
    )
    parser.add_argument(
        "--json-out", metavar="PATH",
        help="Write the final Bedrock config JSON to this file",
    )
    parser.add_argument(
        "--kms-key-id", metavar="KEY_ID",
        help="KMS key ARN or ID for guardrail encryption at rest",
    )
    parser.add_argument(
        "--tag", metavar="KEY=VALUE", action="append", dest="tags",
        help="Tag to attach (repeat for multiple tags)",
    )
    parser.add_argument(
        "--name", metavar="NAME",
        help="Override the guardrail name (default: derived from policy)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print extracted policy fields before building the config",
    )

    args = parser.parse_args(argv)

    policy_path = Path(args.policy_file)
    if not policy_path.exists():
        print(f"Error: file not found: {policy_path}", file=sys.stderr)
        return 1

    # ── 1. Extract text ───────────────────────────────────────────────────
    print(f"Extracting text from {policy_path.name} ...", file=sys.stderr)
    from .extractor import extract_text
    try:
        policy_text = extract_text(policy_path)
    except Exception as e:
        print(f"Error extracting text: {e}", file=sys.stderr)
        return 1

    print(f"  {len(policy_text):,} characters extracted.", file=sys.stderr)

    # ── 2. Parse with Claude ──────────────────────────────────────────────
    print("Parsing policy with Claude ...", file=sys.stderr)
    from .policy_parser import PolicyParser
    try:
        parser_obj = PolicyParser(model_id=args.model_id, region=args.region)
        policy = parser_obj.parse(policy_text)
    except Exception as e:
        print(f"Error parsing policy: {e}", file=sys.stderr)
        return 1

    if args.name:
        import re
        policy.guardrail_name = re.sub(r"[^a-z0-9-]", "-", args.name.lower())[:50]

    if args.verbose or args.parse_only:
        print("\n── Parsed policy ────────────────────────────────────────────", file=sys.stderr)
        print(f"  name:        {policy.guardrail_name}", file=sys.stderr)
        print(f"  description: {policy.description}", file=sys.stderr)
        print(f"  tiers:       {[t.name for t in policy.classification_tiers]}", file=sys.stderr)
        print(f"  topics:      {[t.name for t in policy.denied_topics]}", file=sys.stderr)
        print(f"  pii_types:   {[p.type for p in policy.pii_entities]}", file=sys.stderr)
        print(f"  input_phrases:  {len(policy.blocked_input_phrases)}", file=sys.stderr)
        print(f"  output_phrases: {len(policy.blocked_output_phrases)}", file=sys.stderr)

    if args.parse_only:
        print(json.dumps(policy.raw, indent=2))
        return 0

    # ── 3. Build Guardrail config ────────────────────────────────────────
    from .guardrail_builder import build_guardrail_config
    config = build_guardrail_config(policy)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(config.as_dict(), indent=2), encoding="utf-8")
        print(f"Config written to {args.json_out}", file=sys.stderr)

    # ── 4. Deploy ────────────────────────────────────────────────────────
    tags: dict[str, str] = {}
    for tag in args.tags or []:
        if "=" not in tag:
            print(f"Error: tag must be KEY=VALUE, got: {tag!r}", file=sys.stderr)
            return 1
        k, _, v = tag.partition("=")
        tags[k] = v

    from .deployer import deploy_guardrail
    try:
        result = deploy_guardrail(
            config,
            region=args.region,
            kms_key_id=args.kms_key_id,
            tags=tags or None,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"Error deploying guardrail: {e}", file=sys.stderr)
        return 1

    action = "Created" if result.created else "Updated"
    print(f"\n{action} guardrail:")
    print(f"  ID:      {result.guardrail_id}")
    print(f"  ARN:     {result.guardrail_arn}")
    print(f"  Version: {result.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
