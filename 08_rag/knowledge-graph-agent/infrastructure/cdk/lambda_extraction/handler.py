"""Lambda entrypoint for the Step Functions extraction workflow.

Each Map-state iteration receives a single document descriptor:

    {"s3_uri": "s3://kg-docs-…/01_acme.md",
     "document_type": "pe_due_diligence",
     "title": "Acme Corp 10-K"}

The handler delegates to ``knowledge_graph_agent.ingestion.batch_loader.process_document``,
which already encapsulates extraction → embedding → graph load. The
real work — Bedrock calls, Neptune writes, S3 Vectors upserts — runs
inside the Lambda container, fully owned by the Step Functions run.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_SETTINGS_HYDRATED = False


def _hydrate_settings_from_ssm() -> None:
    """Pull /kg/graph_id and /kg/vectors_bucket into env vars once per
    container — the knowledge_graph_agent settings layer reads from KG_* env vars."""
    global _SETTINGS_HYDRATED
    if _SETTINGS_HYDRATED:
        return
    region = os.environ.get("KG_REGION") or os.environ.get("AWS_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    if "KG_GRAPH_ID" not in os.environ:
        os.environ["KG_GRAPH_ID"] = ssm.get_parameter(Name="/kg/graph_id")["Parameter"]["Value"]
    if "KG_S3_VECTORS_BUCKET" not in os.environ:
        os.environ["KG_S3_VECTORS_BUCKET"] = ssm.get_parameter(Name="/kg/vectors_bucket")["Parameter"]["Value"]
    _SETTINGS_HYDRATED = True


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    log.info("event=%s", json.dumps(event)[:500])
    _hydrate_settings_from_ssm()

    # Step Functions Map sends the per-item dict as the event payload.
    s3_uri = event.get("s3_uri")
    if not s3_uri:
        raise ValueError("missing s3_uri in event")

    # Lazy import — avoids loading boto3 + pydantic during cold-start
    # if the Lambda is mistakenly invoked with an empty payload.
    from knowledge_graph_agent.ingestion.batch_loader import process_document

    stats = process_document(
        s3_uri=s3_uri,
        document_type=event.get("document_type", "unknown"),
        title=event.get("title"),
        chunk=event.get("chunk"),
    )
    return {
        "document_id": stats.document_id,
        "entities_added": stats.entities_added,
        "entities_deduped": stats.entities_deduped,
        "relationships_added": stats.relationships_added,
        "embeddings_written": stats.embeddings_written,
        "tokens": stats.token_usage.get("total", 0),
    }
