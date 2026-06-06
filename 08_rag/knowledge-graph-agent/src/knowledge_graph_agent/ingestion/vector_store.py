"""Vector-store abstraction.

S3 Vectors is the default to match Chapter 8. OpenSearch is a parallel
implementation behind the same interface — toggle with
``KG_VECTOR_BACKEND=opensearch``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, Sequence

import boto3

from knowledge_graph_agent.config import get_settings

log = logging.getLogger(__name__)


class VectorStore(Protocol):
    def upsert(self, vector_id: str, vector: Sequence[float], metadata: dict[str, Any]) -> None: ...
    def query(self, vector: Sequence[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]: ...
    def get(self, vector_id: str) -> tuple[list[float], dict[str, Any]] | None: ...


class S3VectorsStore:
    """Wraps boto3 ``s3vectors`` client."""

    def __init__(self, bucket: str | None = None, index: str | None = None, region: str | None = None) -> None:
        settings = get_settings()
        self.bucket = bucket or settings.s3_vectors_bucket
        self.index = index or settings.s3_vectors_index
        if not self.bucket:
            raise ValueError(
                "S3 Vectors bucket not configured. Set KG_S3_VECTORS_BUCKET or pass bucket="
            )
        self.client = boto3.client("s3vectors", region_name=region or settings.region)

    def upsert(self, vector_id: str, vector: Sequence[float], metadata: dict[str, Any]) -> None:
        self.client.put_vectors(
            vectorBucketName=self.bucket,
            indexName=self.index,
            vectors=[
                {
                    "key": vector_id,
                    "data": {"float32": list(vector)},
                    "metadata": metadata,
                }
            ],
        )

    def query(self, vector: Sequence[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        response = self.client.query_vectors(
            vectorBucketName=self.bucket,
            indexName=self.index,
            queryVector={"float32": list(vector)},
            topK=top_k,
            returnMetadata=True,
            returnDistance=True,
        )
        out: list[tuple[str, float, dict[str, Any]]] = []
        for v in response.get("vectors", []):
            out.append(
                (
                    v.get("key", ""),
                    float(v.get("distance", 0.0)),
                    v.get("metadata", {}) or {},
                )
            )
        return out

    def get(self, vector_id: str) -> tuple[list[float], dict[str, Any]] | None:
        try:
            response = self.client.get_vectors(
                vectorBucketName=self.bucket,
                indexName=self.index,
                keys=[vector_id],
                returnData=True,
                returnMetadata=True,
            )
        except self.client.exceptions.ResourceNotFoundException:
            return None
        vectors = response.get("vectors", [])
        if not vectors:
            return None
        v = vectors[0]
        data = v.get("data", {}).get("float32", [])
        return list(data), v.get("metadata", {}) or {}


class OpenSearchStore:
    """Minimal OpenSearch-backed vector store used when ``KG_VECTOR_BACKEND=opensearch``.

    Kept here as a parallel implementation rather than a separate file so
    swapping backends is a one-line change in :func:`make_vector_store`.
    """

    def __init__(self, endpoint: str | None = None, index: str | None = None) -> None:
        settings = get_settings()
        self.endpoint = endpoint or settings.opensearch_endpoint
        self.index = index or settings.opensearch_index
        if not self.endpoint:
            raise ValueError("OpenSearch endpoint not configured")
        # Lazy-import to avoid forcing the dep on S3-Vectors users.
        from opensearchpy import OpenSearch, RequestsHttpConnection  # type: ignore

        from aws_requests_auth.aws_auth import AWSRequestsAuth  # type: ignore

        session = boto3.Session(region_name=settings.region)
        creds = session.get_credentials()
        auth = AWSRequestsAuth(
            aws_access_key=creds.access_key,
            aws_secret_access_key=creds.secret_key,
            aws_token=creds.token,
            aws_host=self.endpoint.replace("https://", "").replace("http://", ""),
            aws_region=settings.region,
            aws_service="es",
        )
        self.client = OpenSearch(
            hosts=[{"host": self.endpoint.replace("https://", "").replace("http://", ""), "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )

    def upsert(self, vector_id: str, vector: Sequence[float], metadata: dict[str, Any]) -> None:
        body = {"vector": list(vector), **metadata}
        self.client.index(index=self.index, id=vector_id, body=body)

    def query(self, vector: Sequence[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        body = {
            "size": top_k,
            "query": {"knn": {"vector": {"vector": list(vector), "k": top_k}}},
        }
        response = self.client.search(index=self.index, body=body)
        out: list[tuple[str, float, dict[str, Any]]] = []
        for hit in response.get("hits", {}).get("hits", []):
            md = dict(hit.get("_source", {}))
            md.pop("vector", None)
            out.append((hit["_id"], float(hit.get("_score", 0.0)), md))
        return out

    def get(self, vector_id: str) -> tuple[list[float], dict[str, Any]] | None:
        try:
            doc = self.client.get(index=self.index, id=vector_id)
        except Exception:
            return None
        source = doc.get("_source", {})
        vector = list(source.pop("vector", []))
        return vector, source


def make_vector_store() -> VectorStore:
    settings = get_settings()
    if settings.vector_backend == "s3vectors":
        return S3VectorsStore()
    if settings.vector_backend == "opensearch":
        return OpenSearchStore()
    raise ValueError(f"unknown vector backend: {settings.vector_backend!r}")
