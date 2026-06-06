#!/usr/bin/env python3
"""Ingest SEC 10-K excerpts. See README.md."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from knowledge_graph_agent.ingestion.batch_loader import process_document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filings_dir", type=Path)
    args = parser.parse_args()

    files = sorted(args.filings_dir.glob("*.txt")) + sorted(args.filings_dir.glob("*.md"))
    if not files:
        print(f"❌ no .txt/.md files in {args.filings_dir}")
        return 1

    for path in files:
        text = path.read_text(encoding="utf-8")
        title = path.stem.replace("_", " ").title()
        stats = process_document(
            s3_uri=f"s3://kg-sec-filings/{path.name}",
            document_type="sec_10k",
            title=title,
            chunk=text,
        )
        print(f"{path.name}: entities={stats.entities_added} edges={stats.relationships_added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
