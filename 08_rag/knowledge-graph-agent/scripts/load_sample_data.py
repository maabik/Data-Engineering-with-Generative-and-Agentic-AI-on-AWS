#!/usr/bin/env python3
"""Convenience wrapper — loads the PE dataroom into the configured graph.

Equivalent to running:
    python examples/pe_due_diligence/ingest_dataroom.py
"""

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).parent.parent / "examples" / "pe_due_diligence" / "ingest_dataroom.py"
    sys.argv = [str(target), *sys.argv[1:]]
    runpy.run_path(str(target), run_name="__main__")
