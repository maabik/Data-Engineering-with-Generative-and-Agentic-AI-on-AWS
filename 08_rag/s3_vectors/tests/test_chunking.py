from s3vectors_rag.chunking import chunk_text


def test_empty_text_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\t ") == []


def test_basic_chunking_produces_chunks_with_indices():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=10)
    assert len(chunks) >= 9  # ~ ceil(1000/(100-10))
    for i, ch in enumerate(chunks):
        assert ch.metadata["chunk_idx"] == i
        assert ch.text.strip() != ""


def test_overlap_is_applied():
    # Use the whitespace fallback path explicitly by passing tiny text.
    text = " ".join(f"w{i}" for i in range(20))
    chunks = chunk_text(text, chunk_size=10, chunk_overlap=4)
    # First two chunks should share at least one token if overlap > 0.
    if len(chunks) >= 2:
        first = chunks[0].text.split()
        second = chunks[1].text.split()
        assert any(tok in second for tok in first[-4:])


def test_invalid_args_raise():
    import pytest
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=0)
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=10, chunk_overlap=-1)


def test_base_metadata_is_propagated():
    chunks = chunk_text("foo bar baz", chunk_size=2, chunk_overlap=0, base_metadata={"source": "x"})
    assert all(c.metadata.get("source") == "x" for c in chunks)
    assert {c.metadata["chunk_idx"] for c in chunks} == set(range(len(chunks)))
