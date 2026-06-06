#!/usr/bin/env python3
"""Project an OpenLineage event stream into the graph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from knowledge_graph_agent.lineage.lineage_collector import load_events
from knowledge_graph_agent.lineage.lineage_to_graph import project_events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events_file", type=Path)
    args = parser.parse_args()

    events = load_events(args.events_file)
    datasets, edges = project_events(events)
    print(f"projected {datasets} datasets and {edges} DERIVED_FROM edges")
    return 0


if __name__ == "__main__":
    sys.exit(main())
