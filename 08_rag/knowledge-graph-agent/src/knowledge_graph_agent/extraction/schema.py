"""Pydantic models for the knowledge-graph schema.

The schema is opinionated. The book's audience needs a default that works
out of the box; users extend by adding new node types that share the
``name`` and ``embedding_id`` properties of ``Entity``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


# ─── Node labels ────────────────────────────────────────────────────────────


class NodeLabel(str, Enum):
    DOCUMENT = "Document"
    ENTITY = "Entity"
    CONCEPT = "Concept"
    PERSON = "Person"
    ORGANIZATION = "Organization"
    EVENT = "Event"
    DATASET = "Dataset"


# ─── Edge types ─────────────────────────────────────────────────────────────


class EdgeType(str, Enum):
    MENTIONS = "MENTIONS"
    RELATES_TO = "RELATES_TO"
    WORKS_AT = "WORKS_AT"
    OWNS = "OWNS"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    DERIVED_FROM = "DERIVED_FROM"
    IS_A = "IS_A"


# ─── Base models ────────────────────────────────────────────────────────────


class _Node(BaseModel):
    """Base for all node types. Concrete subclasses set ``label``."""

    # protected_namespaces=() silences the pydantic warning about
    # ``schema_json`` on the Dataset subclass colliding with v1's
    # deprecated ``schema_*`` namespace.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: str = Field(default_factory=_new_id)
    label: NodeLabel
    name: str = Field(min_length=1)
    embedding_id: Optional[str] = None

    def to_properties(self) -> dict[str, Any]:
        """Property map for openCypher MERGE — drops Nones and the label key."""
        data = self.model_dump(mode="json", exclude_none=True)
        data.pop("label", None)
        return data


class Document(_Node):
    label: Literal[NodeLabel.DOCUMENT] = NodeLabel.DOCUMENT
    source_uri: str
    document_type: str
    ingested_at: datetime = Field(default_factory=_utcnow)
    title: Optional[str] = None
    author: Optional[str] = None
    published_date: Optional[datetime] = None


class Entity(_Node):
    label: Literal[NodeLabel.ENTITY] = NodeLabel.ENTITY
    entity_type: str
    canonical_name: Optional[str] = None
    description: Optional[str] = None
    confidence: float = 1.0
    first_seen_at: datetime = Field(default_factory=_utcnow)


class Concept(_Node):
    label: Literal[NodeLabel.CONCEPT] = NodeLabel.CONCEPT
    definition: str
    domain: Optional[str] = None


class Person(_Node):
    label: Literal[NodeLabel.PERSON] = NodeLabel.PERSON
    role: Optional[str] = None
    org_id: Optional[str] = None


class Organization(_Node):
    label: Literal[NodeLabel.ORGANIZATION] = NodeLabel.ORGANIZATION
    org_type: str
    domain: Optional[str] = None
    industry: Optional[str] = None


class Event(_Node):
    label: Literal[NodeLabel.EVENT] = NodeLabel.EVENT
    event_type: str
    occurred_at: Optional[datetime] = None
    description: Optional[str] = None


class Dataset(_Node):
    label: Literal[NodeLabel.DATASET] = NodeLabel.DATASET
    s3_location: str
    table_format: str
    table_schema: Optional[dict[str, Any]] = None
    lineage_namespace: Optional[str] = None


AnyNode = (
    Document | Entity | Concept | Person | Organization | Event | Dataset
)


# ─── Edges ──────────────────────────────────────────────────────────────────


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EdgeType
    from_id: str
    to_id: str
    properties: dict[str, Any] = Field(default_factory=dict)

    def with_property(self, key: str, value: Any) -> "Edge":
        self.properties[key] = value
        return self


# ─── Extraction outputs ─────────────────────────────────────────────────────


class ExtractedEntity(BaseModel):
    """Raw extractor output. Resolved to a concrete node by the loader."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: NodeLabel
    entity_type: Optional[str] = None
    description: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    position_in_doc: Optional[int] = None


class ExtractedRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    target_name: str
    relationship_type: str
    edge_type: EdgeType = EdgeType.RELATES_TO
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)


def is_embeddable(name: str, min_len: int = 4) -> bool:
    """A name is embeddable when it is a real string, not a number, long enough."""
    if not name or len(name) < min_len:
        return False
    stripped = name.replace(",", "").replace(".", "").strip()
    if not stripped:
        return False
    return not stripped.replace("-", "").isdigit()
