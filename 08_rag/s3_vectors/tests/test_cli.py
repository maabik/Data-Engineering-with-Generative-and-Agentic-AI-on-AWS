from s3vectors_rag.cli import _parse_filters


def test_parse_filters_empty():
    assert _parse_filters([]) == {}
    assert _parse_filters(None) == {}


def test_parse_filters_single_pair():
    assert _parse_filters(["source=doc.md"]) == {"source": "doc.md"}


def test_parse_filters_multiple_pairs_and():
    out = _parse_filters(["source=doc.md", "chunk_idx=0"])
    assert out == {"$and": [{"source": "doc.md"}, {"chunk_idx": "0"}]}


def test_parse_filters_rejects_bad_syntax():
    import pytest
    with pytest.raises(SystemExit):
        _parse_filters(["nope"])
