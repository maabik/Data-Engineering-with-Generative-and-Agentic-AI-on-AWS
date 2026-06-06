#!/usr/bin/env python3
"""Phase 1 checkpoint: add 3 nodes + 1 edge, then read them back.

Run after ``cdk deploy KGNeptune`` to confirm the data plane is reachable.
"""

from __future__ import annotations

import os
import sys

from knowledge_graph_agent.extraction.schema import (
    Edge,
    EdgeType,
    NodeLabel,
    Organization,
    Person,
)
from knowledge_graph_agent.ingestion.graph_loader import GraphLoader


def main() -> int:
    if not os.environ.get("KG_GRAPH_ID"):
        print("❌ KG_GRAPH_ID not set. Set it after `cdk deploy KGNeptune`:")
        print("   export KG_GRAPH_ID=$(aws ssm get-parameter --name /kg/graph_id "
              "--query Parameter.Value --output text)")
        return 1

    loader = GraphLoader()

    sequoia = Organization(name="Sequoia Capital", org_type="venture-capital-firm")
    finflow = Organization(name="FinFlow Inc", org_type="company")
    maria = Person(name="Maria Chen", role="CEO")

    print("upserting 3 nodes...")
    loader.upsert_node(sequoia)
    loader.upsert_node(finflow)
    loader.upsert_node(maria)

    print("upserting 1 edge...")
    loader.upsert_edge(
        Edge(
            type=EdgeType.OWNS,
            from_id=sequoia.id,
            to_id=finflow.id,
            properties={"ownership_pct": 12.0},
        )
    )

    org_count = loader.count_nodes(NodeLabel.ORGANIZATION)
    person_count = loader.count_nodes(NodeLabel.PERSON)
    print(f"✅ Organizations: {org_count}")
    print(f"✅ Persons:       {person_count}")

    seq = loader.get_node(sequoia.id)
    print(f"✅ readback: {seq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
