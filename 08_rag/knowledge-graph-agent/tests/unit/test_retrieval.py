"""Retrieval-layer tests using pure-Python fakes for the graph and vector stores."""

from __future__ import annotations

from knowledge_graph_agent.retrieval.graph_search import GraphSearch
from knowledge_graph_agent.retrieval.hybrid_search import HybridSearch
from knowledge_graph_agent.retrieval.prompt_construction import build_rag_context, build_subgraph_summary
from knowledge_graph_agent.retrieval.vector_search import VectorHit, VectorSearch


class _FakeLoader:
    def __init__(self, results=None):
        self._results = results or {}
        self.calls = []

    def execute_cypher(self, query, params=None):
        self.calls.append((query, params))
        return self._results.get(query, [])

    def get_node(self, node_id):
        return {"id": node_id, "name": "Node " + node_id, "label": "Entity"}


class _FakeStore:
    def __init__(self, hits):
        self.hits = hits

    def query(self, vector, top_k):
        return self.hits[:top_k]

    def upsert(self, *_args, **_kwargs):
        pass

    def get(self, _id):
        return None


def _hit(eid, name, distance):
    return (
        eid,
        distance,
        {"node_id": "node-" + eid, "name": name, "label": "Organization"},
    )


def test_graph_search_find_neighbors_calls_loader():
    loader = _FakeLoader()
    search = GraphSearch(loader=loader)
    search.find_neighbors("e1", depth=2)
    assert loader.calls
    query, _ = loader.calls[0]
    assert "*1..2" in query


def test_graph_search_find_path_bounds_depth():
    loader = _FakeLoader()
    GraphSearch(loader=loader).find_path("a", "b", max_depth=42)
    query, _ = loader.calls[0]
    # bounded internally at 6
    assert "*1..6" in query


def test_hybrid_search_returns_top_n_sorted():
    store = _FakeStore(
        [
            _hit("a", "Acme", 0.8),
            _hit("b", "Sequoia", 0.4),
            _hit("c", "Stripe", 0.2),
        ]
    )

    class _Embedder:
        def search_text(self, query, top_k):
            return [
                VectorHit(
                    embedding_id=eid,
                    node_id=meta["node_id"],
                    name=meta["name"],
                    label=meta["label"],
                    distance=dist,
                    metadata=meta,
                )
                for eid, dist, meta in store.hits[:top_k]
            ]

        def search_vector(self, *args, **kwargs):
            return self.search_text("", kwargs.get("top_k", 10))

    class _Graph:
        def summarize_subgraph(self, *_a, **_kw):
            return {"entity": {"id": "e1"}, "neighbors": [{"name": "X"}], "edges": []}

    hybrid = HybridSearch(graph=_Graph(), vector=_Embedder())
    out = hybrid.search("anything", top_k_vector=3, top_n=2)
    assert len(out) == 2
    # Lowest distance should win after re-rank.
    assert out[0].name == "Stripe"


def test_build_rag_context_renders_hits():
    from knowledge_graph_agent.retrieval.hybrid_search import HybridHit

    hits = [
        HybridHit(
            node_id="n1",
            name="Acme Corp",
            label="Organization",
            score=0.83,
            vector_distance=0.2,
            graph_neighbors=[{"name": "FinFlow Inc", "label": "Organization"}],
            graph_edges=[],
        )
    ]
    text = build_rag_context("acquisitions", hits)
    assert "Acme Corp" in text
    assert "FinFlow Inc" in text
    assert "node_id: n1" in text


def test_build_subgraph_summary_handles_empty():
    assert build_subgraph_summary({"entity": None}) == "(empty subgraph)"
