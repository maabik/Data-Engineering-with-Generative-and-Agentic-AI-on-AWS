"""Project OpenLineage events into the knowledge graph.

Each event with ``inputs`` and ``outputs`` becomes a set of ``Dataset``
nodes connected by a ``DERIVED_FROM`` edge that carries the
transformation name and the lineage event id.
"""

from __future__ import annotations

import logging
from typing import Iterable

from knowledge_graph_agent.extraction.schema import Dataset, Edge, EdgeType
from knowledge_graph_agent.ingestion.graph_loader import GraphLoader

log = logging.getLogger(__name__)


def _dataset_from_event(ds: dict, namespace: str) -> Dataset:
    name = ds.get("name") or ds.get("uri") or "unknown-dataset"
    return Dataset(
        name=name,
        s3_location=ds.get("uri") or ds.get("location") or "",
        table_format=ds.get("format") or "unknown",
        table_schema=ds.get("schema"),
        lineage_namespace=namespace,
    )


def project_events(
    events: Iterable[dict],
    *,
    loader: GraphLoader | None = None,
    namespace: str = "knowledge-graph-agent",
) -> tuple[int, int]:
    """Return ``(datasets_added, edges_added)``."""
    loader = loader or GraphLoader()
    datasets = 0
    edges = 0
    for event in events:
        run_id = event.get("run", {}).get("runId") or event.get("eventTime") or "unknown"
        job_name = event.get("job", {}).get("name") or "unknown-job"

        outputs = [_dataset_from_event(o, namespace) for o in event.get("outputs", [])]
        inputs = [_dataset_from_event(i, namespace) for i in event.get("inputs", [])]
        for node in outputs + inputs:
            loader.upsert_node(node)
            datasets += 1

        for out in outputs:
            for inp in inputs:
                loader.upsert_edge(
                    Edge(
                        type=EdgeType.DERIVED_FROM,
                        from_id=out.id,
                        to_id=inp.id,
                        properties={
                            "transformation": job_name,
                            "lineage_event_id": run_id,
                        },
                    )
                )
                edges += 1
    log.info("lineage projected: %d datasets, %d edges", datasets, edges)
    return datasets, edges
