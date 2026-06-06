#!/usr/bin/env python3
"""
Backwards-compatible entry point.

The original single-file demo has been replaced by the `s3vectors_rag`
package under src/. This shim runs the package's CLI against the bundled
samples/ corpus so existing automation (run_demo.sh, course materials)
keeps working.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from s3vectors_rag.cli import main as cli_main  # noqa: E402


def main() -> int:
    samples = HERE / "samples"
    queries = [
        "vector search capabilities",
        "embedding dimensions tradeoff",
    ]
    rc = cli_main(["ingest", str(samples)])
    if rc != 0:
        return rc
    for q in queries:
        print(f"\n=== query: {q} ===")
        rc = cli_main(["query", q, "--top-k", "3"])
        if rc != 0:
            return rc
    if os.environ.get("CLEAN", "0") == "1":
        cli_main(["clean", "--yes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
