"""End-to-end example: parse sample_policy.txt and deploy a Bedrock Guardrail.

Usage:
    cd guardrails-from-policy
    pip install -e ".[dev]"

    # Dry-run (no AWS calls except to Bedrock Converse for parsing):
    python examples/run_example.py --dry-run

    # Full deploy:
    python examples/run_example.py --region us-west-2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from guardrails_from_policy import (
    extract_text,
    PolicyParser,
    build_guardrail_config,
    deploy_guardrail,
)
from guardrails_from_policy.deployer import DeployResult


POLICY_FILE = Path(__file__).parent / "sample_policy.txt"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--model-id", default="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    # 1. Extract
    print(f"Extracting policy text from {POLICY_FILE.name} ...")
    policy_text = extract_text(POLICY_FILE)
    print(f"  {len(policy_text):,} characters")

    # 2. Parse
    print(f"Parsing with {args.model_id} ...")
    parsed = PolicyParser(model_id=args.model_id, region=args.region).parse(policy_text)

    print(f"\nParsed policy: {parsed.guardrail_name!r}")
    print(f"  Description: {parsed.description}")
    print(f"  Classification tiers: {[t.name for t in parsed.classification_tiers]}")
    print(f"  Denied topics ({len(parsed.denied_topics)}): {[t.name for t in parsed.denied_topics]}")
    print(f"  PII entities: {[p.type for p in parsed.pii_entities]}")
    print(f"  Blocked input phrases: {parsed.blocked_input_phrases}")
    print(f"  Grounding threshold: {parsed.grounding_threshold}")
    print(f"  Relevance threshold: {parsed.relevance_threshold}")

    # 3. Build config
    config = build_guardrail_config(parsed)
    config_dict = config.as_dict()

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(config_dict, indent=2))
        print(f"\nConfig written to {args.json_out}")
    else:
        print("\nGuardrail config (boto3 kwargs):")
        print(json.dumps(config_dict, indent=2))

    # 4. Deploy
    result: DeployResult = deploy_guardrail(
        config,
        region=args.region,
        tags={"CreatedBy": "guardrails-from-policy", "PolicyFile": POLICY_FILE.name},
        dry_run=args.dry_run,
    )

    action = "Created" if result.created else "Updated"
    print(f"\n{action} guardrail:")
    print(f"  ID:      {result.guardrail_id}")
    print(f"  ARN:     {result.guardrail_arn}")
    print(f"  Version: {result.version}")


if __name__ == "__main__":
    main()
