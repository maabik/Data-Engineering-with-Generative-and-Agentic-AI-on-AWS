# Graph schema

The default schema is opinionated. The book's audience needs something
that works out of the box, with clear extension points.

## Node types

| Label          | Required properties                                       | Optional                                          |
| -------------- | --------------------------------------------------------- | ------------------------------------------------- |
| `Document`     | `id`, `source_uri`, `ingested_at`, `document_type`        | `title`, `author`, `published_date`, `embedding_id` |
| `Entity`       | `id`, `name`, `entity_type`, `first_seen_at`              | `canonical_name`, `description`, `embedding_id`, `confidence` |
| `Concept`      | `id`, `name`, `definition`                                | `domain`, `embedding_id`                          |
| `Person`       | `id`, `name`, `role`                                      | `org_id`, `embedding_id`                          |
| `Organization` | `id`, `name`, `org_type`                                  | `domain`, `industry`, `embedding_id`              |
| `Event`        | `id`, `event_type`, `occurred_at`                         | `description`, `embedding_id`                     |
| `Dataset`      | `id`, `name`, `s3_location`, `table_format`               | `table_schema`, `lineage_namespace`               |

`Entity` is the catch-all base. Specialized labels (`Person`, etc.)
share the `name` and `embedding_id` slots so vector search returns a
useful mix.

## Edge types

| Type             | From → To                                                            | Properties                                       |
| ---------------- | -------------------------------------------------------------------- | ------------------------------------------------ |
| `MENTIONS`       | `Document → Entity\|Person\|Organization\|Event\|Concept`            | `confidence`, `position_in_doc`                  |
| `RELATES_TO`     | `Entity → Entity` (generic)                                          | `relationship_type`, `confidence`, `source_doc_id` |
| `WORKS_AT`       | `Person → Organization`                                              | `role`, `start_date`, `end_date`                 |
| `OWNS`           | `Organization → Organization`                                        | `ownership_pct`, `effective_date`                |
| `PARTICIPATED_IN`| `Person\|Organization → Event`                                       | `role`                                           |
| `DERIVED_FROM`   | `Dataset → Dataset`                                                  | `transformation`, `lineage_event_id`             |
| `IS_A`           | `any → Concept`                                                      | (semantic typing)                                |

## Embeddings

Every node with an `embedding_id` property has its embedding stored in
the configured vector store (S3 Vectors by default) keyed by that id.
The graph stores the reference, not the vector itself, so graph
storage stays cheap and the vector store stays queryable.

The embedding model is **Amazon Nova 2 Multimodal Embeddings**
(`amazon.nova-2-multimodal-embeddings-v1:0`, 1024 dimensions).
Configure via `KG_EMBEDDING_MODEL_ID` and `KG_EMBEDDING_DIMENSIONS`.
Document-side embeddings use `embeddingPurpose=GENERIC_INDEX`;
query-side embeddings use `TEXT_RETRIEVAL`.

## Extending the schema

Add a new label by:

1. Subclassing `_Node` in `knowledge_graph_agent/extraction/schema.py` with
   `label: Literal[NodeLabel.YOURLABEL] = NodeLabel.YOURLABEL`.
2. Adding the new label to `NodeLabel` enum.
3. Updating the entity-extraction prompt at
   `src/knowledge_graph_agent/extraction/prompts/entity_extraction.md` to teach
   Claude when to choose it.
4. (Optional) handling the new label in `batch_loader._node_for`.

Edges extend the same way via `EdgeType`.
