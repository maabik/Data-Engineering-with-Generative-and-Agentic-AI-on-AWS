# Amazon S3 Vectors

Amazon S3 Vectors lets you store, index, and query vector embeddings as a native
S3 capability. You create a vector bucket, then create one or more indexes
inside it. Each index is configured with a fixed dimension and a distance
metric such as cosine.

## Indexing

Vectors are written via `put_vectors` with a stable key, the vector itself, and
arbitrary metadata. Metadata fields can later be used as filters at query time,
so you can scope a search to a single document, section, or tag.

## Querying

`query_vectors` accepts a query vector, a max-results count, and an optional
metadata filter. Scores reflect the configured distance metric — for cosine,
higher is more similar.

# Bedrock Titan Embeddings v2

Amazon Bedrock provides the Titan Text Embeddings v2 model under the model id
`amazon.titan-embed-text-v2:0`. It supports configurable output dimensions
(256, 512, or 1024) and produces dense vectors suitable for semantic search.

## Choosing dimensions

Larger dimensions retain more semantic detail but use more storage and compute.
1024 is a reasonable default for general-purpose retrieval; 512 is a good
choice when index size matters more than the last few points of recall.

# Retrieval Augmented Generation

RAG combines a retrieval step over a vector index with a generation step that
conditions on the retrieved passages. The retrieval index can be S3 Vectors,
OpenSearch, or any other ANN store. The generator is typically a hosted LLM
such as Anthropic Claude on Bedrock.

## Why retrieval helps

Retrieval keeps the model grounded in your private corpus and lets you cite
sources at answer time. It also reduces hallucination by anchoring the
generated text to passages the model can quote from.
