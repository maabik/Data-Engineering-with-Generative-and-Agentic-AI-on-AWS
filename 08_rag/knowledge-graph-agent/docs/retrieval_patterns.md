# Retrieval patterns

## When GraphRAG beats vector RAG

GraphRAG is **not** always-on. Use it when:

- The question requires **multi-hop reasoning**: "executives at companies
  owned by Sequoia who have a fintech background" is three hops.
- Answers depend on **typed structure**: "show me the OWNS chain for X."
- You need **deterministic enumeration**: "every contract that mentions
  a change-of-control clause" — vector recall alone misses the long tail.

Stick with vector RAG when:

- The question is a single-hop semantic match ("what does this clause
  say about indemnification?").
- The corpus is small enough that vector recall over the whole corpus
  is exhaustive.
- You don't need typed structure to filter results.

## The hybrid algorithm

`knowledge_graph_agent.retrieval.hybrid_search.HybridSearch` implements the
algorithm the spec calls for:

1. Vector search returns top-K (default 20) semantically similar nodes.
2. For each result, traverse 1–2 hops in the graph to find related
   entities (configurable via `KG_DEFAULT_TRAVERSAL_DEPTH`).
3. Re-rank by `(1 - graph_weight) * vector_similarity + graph_weight * graph_proximity`.
   Default `graph_weight` is 0.4.
4. Return the top-N hits with their context subgraphs (default N=10).

`graph_weight` is the dial. Higher values favor results with rich graph
neighborhoods; lower values trust the vector layer more. The default
0.4 is biased toward semantic recall — change it when you want the
graph to dominate.

## Tool design

The MCP server exposes four tools, intentionally narrow:

- `kg_search` — does the heavy lifting. The agent should use this first.
- `kg_get_entity` — exact lookup by id. Used to expand a search hit.
- `kg_find_path` — multi-hop. Useful for "how is X connected to Y?"
- `kg_summarize_subgraph` — compact context. Used for grounding.

No `execute_cypher` escape hatch in the MCP layer — agents tend to
write Cypher that traverses too aggressively. If you need it, call
`knowledge_graph_agent.ingestion.graph_loader.GraphLoader.execute_cypher` directly
from your own code.

## Latency

Typical hybrid query (vector + 1-hop graph context):

- Bedrock embedding: ~150–250 ms
- S3 Vectors top-20: ~80–150 ms
- Neptune Analytics 1-hop traversal × 20: ~600–900 ms (parallel)
- Re-rank + render: ~10 ms

End-to-end p50 ≈ 1.0 s; p95 ≈ 1.6 s in the dataroom example. The agent
adds 2–4 s for reasoning depending on question complexity, so 2-hop
queries return in under 6 s.

The acceptance criteria target is "under 10 seconds for typical 2-hop
queries" — the default config meets that.
