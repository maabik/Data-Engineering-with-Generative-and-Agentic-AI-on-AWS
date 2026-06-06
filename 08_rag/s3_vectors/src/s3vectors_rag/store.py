from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import boto3
from botocore.exceptions import ClientError

from .config import Config
from .logging_setup import get_logger

log = get_logger("store")


@dataclass
class VectorRecord:
    key: str
    vector: List[float]
    metadata: Dict[str, Any]


@dataclass
class QueryHit:
    key: str
    score: float          # similarity-style score derived from distance (higher = better)
    distance: float       # raw distance from the API
    metadata: Dict[str, Any]


def _to_float32_list(vec: List[float]) -> List[float]:
    """Coerce to plain Python floats — required for the float32 tagged union."""
    return [float(x) for x in vec]


class S3VectorStore:
    """Idempotent wrapper around the s3vectors API (lowerCamelCase shape)."""

    def __init__(self, config: Config, client=None):
        self._cfg = config
        if client is not None:
            self._client = client
        else:
            self._client = boto3.client("s3vectors", region_name=config.region)

    @property
    def bucket(self) -> str:
        return self._cfg.bucket_name

    @property
    def index(self) -> str:
        return self._cfg.index_name

    @property
    def _distance_metric(self) -> str:
        # The API expects lowercase ("cosine" / "euclidean").
        return self._cfg.distance_metric.lower()

    # ----- setup -----

    def ensure_bucket(self) -> None:
        try:
            self._client.create_vector_bucket(vectorBucketName=self.bucket)
            log.info("created vector bucket %s", self.bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"ConflictException", "BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                log.info("vector bucket %s already exists", self.bucket)
                return
            raise

    def ensure_index(self) -> None:
        try:
            self._client.create_index(
                vectorBucketName=self.bucket,
                indexName=self.index,
                dataType="float32",
                dimension=self._cfg.embed_dimension,
                distanceMetric=self._distance_metric,
            )
            log.info("created index %s (dim=%d, %s)", self.index, self._cfg.embed_dimension, self._distance_metric)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"ConflictException", "ResourceAlreadyExistsException"}:
                log.info("index %s already exists", self.index)
                return
            raise

    def ensure(self) -> None:
        self.ensure_bucket()
        self.ensure_index()

    # ----- writes -----

    def put_vectors(self, records: Iterable[VectorRecord]) -> int:
        items = list(records)
        if not items:
            return 0
        batch = self._cfg.put_batch_size
        total = 0
        for i in range(0, len(items), batch):
            window = items[i : i + batch]
            self._client.put_vectors(
                vectorBucketName=self.bucket,
                indexName=self.index,
                vectors=[
                    {
                        "key": r.key,
                        "data": {"float32": _to_float32_list(r.vector)},
                        "metadata": r.metadata,
                    }
                    for r in window
                ],
            )
            total += len(window)
            log.info("put_vectors %d / %d", total, len(items))
        return total

    def delete_by_keys(self, keys: List[str]) -> int:
        if not keys:
            return 0
        batch = self._cfg.put_batch_size
        total = 0
        for i in range(0, len(keys), batch):
            window = keys[i : i + batch]
            self._client.delete_vectors(
                vectorBucketName=self.bucket,
                indexName=self.index,
                keys=window,
            )
            total += len(window)
        return total

    # ----- reads -----

    def query(
        self,
        query_vector: List[float],
        top_k: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None,
        return_metadata: bool = True,
        return_distance: bool = True,
    ) -> List[QueryHit]:
        kwargs: Dict[str, Any] = {
            "vectorBucketName": self.bucket,
            "indexName": self.index,
            "queryVector": {"float32": _to_float32_list(query_vector)},
            "topK": top_k,
            "returnMetadata": return_metadata,
            "returnDistance": return_distance,
        }
        if metadata_filter:
            kwargs["filter"] = metadata_filter
        resp = self._client.query_vectors(**kwargs)
        hits: List[QueryHit] = []
        for m in resp.get("vectors", []):
            distance = float(m.get("distance", 0.0))
            # For cosine distance the API returns 1 - cos_sim, so 1 - distance is similarity.
            # For euclidean we expose -distance as a "higher is better" score.
            if self._distance_metric == "cosine":
                score = 1.0 - distance
            else:
                score = -distance
            hits.append(
                QueryHit(
                    key=m.get("key", ""),
                    distance=distance,
                    score=score,
                    metadata=m.get("metadata", {}) or {},
                )
            )
        return hits

    def stats(self) -> Dict[str, Any]:
        try:
            resp = self._client.get_index(vectorBucketName=self.bucket, indexName=self.index)
            return resp.get("index", resp)
        except ClientError as e:
            log.warning("get_index failed: %s", e)
            return {}

    # ----- teardown -----

    def delete_index(self) -> None:
        try:
            self._client.delete_index(vectorBucketName=self.bucket, indexName=self.index)
            log.info("deleted index %s", self.index)
        except ClientError as e:
            log.warning("delete_index failed: %s", e)

    def delete_bucket(self) -> None:
        try:
            self._client.delete_vector_bucket(vectorBucketName=self.bucket)
            log.info("deleted vector bucket %s", self.bucket)
        except ClientError as e:
            log.warning("delete_vector_bucket failed: %s", e)

    def teardown(self) -> None:
        self.delete_index()
        self.delete_bucket()
