"""
In-memory fakes for the s3vectors and bedrock-runtime clients.

Shapes mirror the real APIs (lowerCamelCase keys, tagged-union vector data,
`vectors` in put/query payloads) so swap-in for the production client is
behaviorally accurate.
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError


def _client_error(code: str, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3Vectors:
    def __init__(self):
        self.buckets: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
        # buckets[bucket][index] = {"config": {...}, "vectors": {key: record}}
        self.calls: List[tuple] = []

    # ----- bucket -----
    def create_vector_bucket(self, *, vectorBucketName: str, **_):
        self.calls.append(("create_vector_bucket", vectorBucketName))
        if vectorBucketName in self.buckets:
            raise _client_error("ConflictException", "CreateVectorBucket")
        self.buckets[vectorBucketName] = {}
        return {}

    def delete_vector_bucket(self, *, vectorBucketName: str, **_):
        self.calls.append(("delete_vector_bucket", vectorBucketName))
        self.buckets.pop(vectorBucketName, None)
        return {}

    # ----- index -----
    def create_index(
        self,
        *,
        vectorBucketName: str,
        indexName: str,
        dataType: str,
        dimension: int,
        distanceMetric: str,
        **_,
    ):
        self.calls.append(("create_index", vectorBucketName, indexName, dimension, distanceMetric))
        bucket = self.buckets.setdefault(vectorBucketName, {})
        if indexName in bucket:
            raise _client_error("ConflictException", "CreateIndex")
        bucket[indexName] = {
            "config": {"dataType": dataType, "dimension": dimension, "distanceMetric": distanceMetric},
            "vectors": {},
        }
        return {}

    def delete_index(self, *, vectorBucketName: str, indexName: str, **_):
        self.calls.append(("delete_index", vectorBucketName, indexName))
        if vectorBucketName in self.buckets:
            self.buckets[vectorBucketName].pop(indexName, None)
        return {}

    def get_index(self, *, vectorBucketName: str, indexName: str, **_):
        self.calls.append(("get_index", vectorBucketName, indexName))
        idx = self.buckets.get(vectorBucketName, {}).get(indexName)
        if not idx:
            raise _client_error("ResourceNotFoundException", "GetIndex")
        cfg = idx["config"]
        return {
            "index": {
                "indexName": indexName,
                "vectorBucketName": vectorBucketName,
                "dataType": cfg["dataType"],
                "dimension": cfg["dimension"],
                "distanceMetric": cfg["distanceMetric"],
            }
        }

    # ----- vectors -----
    def put_vectors(self, *, vectorBucketName: str, indexName: str, vectors: List[Dict[str, Any]], **_):
        self.calls.append(("put_vectors", vectorBucketName, indexName, len(vectors)))
        idx = self.buckets[vectorBucketName][indexName]
        for v in vectors:
            idx["vectors"][v["key"]] = v
        return {}

    def delete_vectors(self, *, vectorBucketName: str, indexName: str, keys: List[str], **_):
        self.calls.append(("delete_vectors", vectorBucketName, indexName, len(keys)))
        idx = self.buckets[vectorBucketName][indexName]
        for k in keys:
            idx["vectors"].pop(k, None)
        return {}

    def query_vectors(
        self,
        *,
        vectorBucketName: str,
        indexName: str,
        queryVector: Dict[str, List[float]],
        topK: int = 5,
        filter: Optional[Dict[str, Any]] = None,
        returnMetadata: bool = True,
        returnDistance: bool = True,
        **_,
    ):
        self.calls.append(("query_vectors", vectorBucketName, indexName, topK, filter))
        idx = self.buckets[vectorBucketName][indexName]
        records = list(idx["vectors"].values())
        if filter:
            records = [r for r in records if _match(r.get("metadata", {}), filter)]
        qv = queryVector["float32"]
        scored = []
        for r in records:
            v = r["data"]["float32"]
            d = sum((a - b) ** 2 for a, b in zip(qv, v)) ** 0.5
            scored.append((d, r))
        scored.sort(key=lambda t: t[0])
        out = []
        for d, r in scored[:topK]:
            entry = {"key": r["key"]}
            if returnDistance:
                entry["distance"] = d
            if returnMetadata:
                entry["metadata"] = r.get("metadata", {})
            out.append(entry)
        return {"vectors": out}


def _match(md: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    if "$and" in flt:
        return all(_match(md, sub) for sub in flt["$and"])
    return all(md.get(k) == v for k, v in flt.items())


class FakeBedrock:
    """Deterministic embedder: hashes input text into a fixed-length vector."""

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.calls: List[tuple] = []

    def invoke_model(self, *, modelId: str, body: str):
        self.calls.append(("invoke_model", modelId))
        payload = json.loads(body)
        text = payload.get("inputText", "")
        dim = payload.get("dimensions") or self.dimension
        h = abs(hash(text))
        vec = [((h >> (i % 32)) & 0xFF) / 255.0 for i in range(dim)]
        out = {"embedding": vec, "inputTextTokenCount": len(text.split())}
        return {"body": io.BytesIO(json.dumps(out).encode("utf-8"))}
