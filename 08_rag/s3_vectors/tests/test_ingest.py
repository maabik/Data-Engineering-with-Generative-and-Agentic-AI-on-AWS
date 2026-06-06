from pathlib import Path

from s3vectors_rag.config import Config
from s3vectors_rag.embeddings import TitanEmbedder
from s3vectors_rag.ingest import ingest_paths
from s3vectors_rag.store import S3VectorStore

from .fakes import FakeBedrock, FakeS3Vectors


def test_ingest_end_to_end(tmp_path: Path):
    (tmp_path / "doc.md").write_text(
        "# Topic A\nVectors are stored in S3.\n\n# Topic B\nBedrock embeds text.",
        encoding="utf-8",
    )
    cfg = Config(
        bucket_name="bkt",
        index_name="idx",
        embed_dimension=16,
        chunk_size=20,
        chunk_overlap=5,
        embed_batch_size=4,
        put_batch_size=4,
    )
    sv = FakeS3Vectors()
    bd = FakeBedrock(dimension=16)
    store = S3VectorStore(cfg, client=sv)
    embed = TitanEmbedder(cfg, client=bd)

    written = ingest_paths([tmp_path], cfg, store=store, embedder=embed)
    assert written >= 2

    # Each stored vector carries source/locator/text metadata.
    idx = sv.buckets["bkt"]["idx"]["vectors"]
    sample = next(iter(idx.values()))
    md = sample["metadata"]
    assert md["source"] == "doc.md"
    assert md["locator"].startswith("section=")
    assert "text" in md and md["text"]


def test_ingest_with_source_tag(tmp_path: Path):
    (tmp_path / "doc.md").write_text("# H\nbody body body", encoding="utf-8")
    cfg = Config(
        bucket_name="bkt", index_name="idx", embed_dimension=8,
        chunk_size=10, chunk_overlap=0, source_tag="my-corpus",
    )
    sv = FakeS3Vectors()
    bd = FakeBedrock(dimension=8)
    ingest_paths(
        [tmp_path], cfg,
        store=S3VectorStore(cfg, client=sv),
        embedder=TitanEmbedder(cfg, client=bd),
    )
    md = next(iter(sv.buckets["bkt"]["idx"]["vectors"].values()))["metadata"]
    assert md["source"] == "my-corpus"
