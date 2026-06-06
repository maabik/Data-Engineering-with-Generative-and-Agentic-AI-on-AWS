#!/usr/bin/env python3
"""Diff the MCP tool inventory between this branch and the base branch.

Fails on:
  - Tool removal (unless [breaking-mcp] in PR title via env var).
  - Parameter rename (same).
  - Parameter type narrowing (always — widening is fine).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def load(path: str | Path, ref: str | None = None) -> dict:
    if ref is None:
        return json.loads(Path(path).read_text())
    raw = subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True)
    return json.loads(raw)


def tools_by_name(spec: dict) -> dict[str, dict]:
    return {t["name"]: t for t in spec.get("tools", [])}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--path", default="labs/09-agentic-and-mcp-devops/tools/mcp_redshift_tools.json")
    p.add_argument("--base", default="origin/main")
    args = p.parse_args()

    breaking_ok = "[breaking-mcp]" in os.environ.get("PR_TITLE", "")

    before = tools_by_name(load(args.path, args.base))
    after = tools_by_name(load(args.path))

    failures = []

    removed = set(before) - set(after)
    if removed:
        msg = f"removed tools: {sorted(removed)}"
        (failures if not breaking_ok else print)(msg)

    for name in set(before) & set(after):
        b_props = before[name].get("input_schema", {}).get("properties", {})
        a_props = after[name].get("input_schema", {}).get("properties", {})

        renamed = (set(b_props) - set(a_props)) and (set(a_props) - set(b_props))
        if renamed and not breaking_ok:
            failures.append(f"{name}: parameter rename suspected: removed {set(b_props) - set(a_props)}, added {set(a_props) - set(b_props)}")

        for prop, b_def in b_props.items():
            a_def = a_props.get(prop)
            if a_def and a_def.get("type") and b_def.get("type") and a_def["type"] != b_def["type"]:
                if not (a_def["type"] in {"any", "object"}):
                    failures.append(f"{name}.{prop}: type narrowed from {b_def['type']} to {a_def['type']}")

    added = set(after) - set(before)
    if added:
        print(f"added tools (informational): {sorted(added)}")

    if failures:
        print("MCP tool-inventory diff failures:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("MCP tool-inventory diff OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
