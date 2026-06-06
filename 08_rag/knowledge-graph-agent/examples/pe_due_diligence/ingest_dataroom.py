#!/usr/bin/env python3
"""Ingest the synthetic PE dataroom into the knowledge graph.

Reads every ``*.md`` file in ``sample_dataroom/`` and runs the full
extraction pipeline. Set ``KG_GRAPH_ID`` (and ``KG_S3_VECTORS_BUCKET``
for the default vector backend) before running.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from knowledge_graph_agent.ingestion.batch_loader import process_document

DATAROOM = Path(__file__).parent / "sample_dataroom"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataroom",
        type=Path,
        default=DATAROOM,
        help="Directory of .md documents (default: sample_dataroom/)",
    )
    args = parser.parse_args()

    files = sorted(args.dataroom.glob("*.md"))
    if not files:
        print(f"❌ no .md files found in {args.dataroom}")
        return 1

    print(f"📥 ingesting {len(files)} documents from {args.dataroom}")
    total_entities = 0
    total_edges = 0
    total_tokens = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text.startswith("#") else path.stem
        s3_uri = f"s3://kg-pe-dataroom/{path.name}"  # synthetic — chunk overrides the read
        print(f"  • {path.name}: {title}")
        stats = process_document(
            s3_uri=s3_uri,
            document_type="pe_due_diligence",
            title=title,
            chunk=text,
        )
        total_entities += stats.entities_added
        total_edges += stats.relationships_added
        total_tokens += stats.token_usage.get("total", 0)
        print(
            f"    entities={stats.entities_added} "
            f"deduped={stats.entities_deduped} "
            f"edges={stats.relationships_added} "
            f"tokens={stats.token_usage.get('total', 0)}"
        )

    print("\n✅ done")
    print(f"  total entities added: {total_entities}")
    print(f"  total edges added:    {total_edges}")
    print(f"  total tokens used:    {total_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
