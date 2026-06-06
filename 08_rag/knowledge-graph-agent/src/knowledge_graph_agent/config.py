"""Environment-aware configuration for the knowledge-graph agent.

Reads from env vars with the KG_ prefix. Mirrors the pydantic-settings
conventions used by the other projects in this repo.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    environment: Literal["dev", "staging", "prod"] = "dev"
    region: str = Field(default="us-east-1")

    # Neptune Analytics
    graph_id: str = Field(default="", description="Neptune Analytics graph identifier (g-XXXX)")
    graph_endpoint: str = Field(default="", description="Optional explicit endpoint override")

    # Bedrock — extraction (Anthropic Claude via Bedrock)
    extraction_model_id: str = "us.anthropic.claude-sonnet-4-6"
    extraction_max_tokens: int = 4096
    extraction_temperature: float = 0.0

    # Bedrock — embeddings (Amazon Nova 2 Multimodal Embeddings)
    embedding_model_id: str = "amazon.nova-2-multimodal-embeddings-v1:0"
    embedding_dimensions: int = 1024

    # Vector store
    vector_backend: Literal["s3vectors", "opensearch"] = "s3vectors"
    s3_vectors_bucket: str = ""
    s3_vectors_index: str = "kg-entities"
    opensearch_endpoint: str = ""
    opensearch_index: str = "kg-entities"

    # Document source
    documents_bucket: str = ""
    documents_prefix: str = "raw/"

    # Deduplication
    dedup_similarity_threshold: float = 0.92
    embeddable_min_name_length: int = 4

    # Retrieval
    default_top_k_vector: int = 20
    default_traversal_depth: int = 2
    default_top_n_hybrid: int = 10

    # Lineage
    lineage_namespace: str = "knowledge-graph-agent"

    # AgentCore / MCP
    mcp_agent_arn_param: str = "/kg/mcp/agent_arn"
    mcp_credentials_secret: str = "/kg/mcp/credentials"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
