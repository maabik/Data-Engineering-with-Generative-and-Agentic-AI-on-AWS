"""Example: test a deployed Bedrock Agent.

Usage:
    pip install -e ".[dev]"
    export AGENT_ID=ABCDEF1234
    export AGENT_ALIAS_ID=TSTALIASID
    python examples/bedrock_agent_example.py \
        --canary "MY_SECRET_CODE_XYZ" \
        --category bedrock_specific \
        --category system_prompt_exfil
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import BedrockAgentTarget, load_payloads, run_suite
from harness.reporter import print_summary, write_junit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_ID"))
    parser.add_argument("--agent-alias-id", default=os.environ.get("AGENT_ALIAS_ID"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--canary", action="append", dest="canaries",
                        help="Canary string already in the agent's instructions (repeat for multiple)")
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument("--severity", action="append", dest="severities")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--junit-out", default=None)
    args = parser.parse_args()

    if not args.agent_id or not args.agent_alias_id:
        print(
            "Set AGENT_ID and AGENT_ALIAS_ID environment variables or pass --agent-id/--agent-alias-id",
            file=sys.stderr,
        )
        return 2

    target = BedrockAgentTarget(
        agent_id=args.agent_id,
        agent_alias_id=args.agent_alias_id,
        region=args.region,
        canaries=list(args.canaries or []),
        name=f"bedrock-agent-{args.agent_id[:8]}",
    )

    payloads = load_payloads(
        categories=args.categories,
        tags=args.tags,
        severities=args.severities,
    )
    print(f"Loaded {len(payloads)} payloads against agent {args.agent_id}")
    print("Running", end="", flush=True)

    suite = run_suite(
        target,
        payloads,
        max_workers=args.workers,
        on_result=lambda r: print("." if r.passed else "F", end="", flush=True),
    )
    print()

    print_summary(suite, sys.stdout, verbose=True)

    if args.junit_out:
        write_junit(suite, args.junit_out)
        print(f"JUnit XML written to {args.junit_out}")

    return 0 if (suite.failed == 0 and suite.errored == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
