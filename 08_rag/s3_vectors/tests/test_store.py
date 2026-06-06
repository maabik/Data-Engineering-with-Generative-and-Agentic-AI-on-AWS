from s3vectors_rag.config import Config
from s3vectors_rag.store import S3VectorStore, VectorRecord

from .fakes import FakeS3Vectors


def make_store() -> tuple[S3VectorStore, FakeS3Vectors]:
    cfg = Config(bucket_name="bkt", index_name="idx", embed_dimension=4, put_batch_size=2)
    fake = FakeS3Vectors()
    return S3VectorStore(cfg, client=fake), fake


def test_ensure_is_idempotent():
    store, fake = make_store()
    store.ensure()
    store.ensure()  # second call should not raise
    creates = [c for c in fake.calls if c[0] in {"create_vector_bucket", "create_index"}]
    # Two calls each, but the second hit ConflictException internally and was swallowed.
    assert len(creates) == 4


def test_put_and_query_roundtrip():
    store, _ = make_store()
    store.ensure()
    records = [
        VectorRecord(key="a", vector=[1.0, 0.0, 0.0, 0.0], metadata={"source": "a.md", "text": "alpha"}),
        VectorRecord(key="b", vector=[0.0, 1.0, 0.0, 0.0], metadata={"source": "b.md", "text": "beta"}),
        VectorRecord(key="c", vector=[0.0, 0.0, 1.0, 0.0], metadata={"source": "a.md", "text": "gamma"}),
    ]
    written = store.put_vectors(records)
    assert written == 3

    hits = store.query([1.0, 0.0, 0.0, 0.0], top_k=2)
    assert hits[0].key == "a"
    assert len(hits) == 2


def test_query_metadata_filter():
    store, _ = make_store()
    store.ensure()
    store.put_vectors([
        VectorRecord(key="a", vector=[1.0, 0.0, 0.0, 0.0], metadata={"source": "a.md"}),
        VectorRecord(key="b", vector=[0.9, 0.1, 0.0, 0.0], metadata={"source": "b.md"}),
    ])
    hits = store.query([1.0, 0.0, 0.0, 0.0], top_k=5, metadata_filter={"source": "b.md"})
    assert [h.key for h in hits] == ["b"]


def test_put_batches_respect_config():
    store, fake = make_store()
    store.ensure()
    records = [VectorRecord(key=f"k{i}", vector=[float(i), 0, 0, 0], metadata={}) for i in range(5)]
    store.put_vectors(records)
    put_calls = [c for c in fake.calls if c[0] == "put_vectors"]
    # put_batch_size=2 -> 3 calls for 5 records (2+2+1).
    assert [c[3] for c in put_calls] == [2, 2, 1]


def test_teardown_removes_resources():
    store, fake = make_store()
    store.ensure()
    store.teardown()
    assert "delete_index" in [c[0] for c in fake.calls]
    assert "delete_vector_bucket" in [c[0] for c in fake.calls]
