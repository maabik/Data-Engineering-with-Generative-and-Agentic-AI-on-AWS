from s3vectors_rag.config import Config
from s3vectors_rag.embeddings import TitanEmbedder

from .fakes import FakeBedrock


def test_embed_one_returns_vector_of_configured_dimension():
    cfg = Config(embed_dimension=64, embed_batch_size=2)
    e = TitanEmbedder(cfg, client=FakeBedrock(dimension=64))
    vec = e.embed_one("hello world")
    assert len(vec) == 64


def test_embed_many_iterates_all_inputs():
    cfg = Config(embed_dimension=8, embed_batch_size=3)
    fake = FakeBedrock(dimension=8)
    e = TitanEmbedder(cfg, client=fake)
    out = e.embed_many(["a", "b", "c", "d", "e"])
    assert len(out) == 5
    assert all(len(v) == 8 for v in out)
    assert sum(1 for c in fake.calls if c[0] == "invoke_model") == 5
