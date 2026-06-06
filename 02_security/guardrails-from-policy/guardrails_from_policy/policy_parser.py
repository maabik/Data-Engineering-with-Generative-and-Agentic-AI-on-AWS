"""Use Claude via Bedrock Converse to parse a policy document into structured fields.

The model returns a JSON object. We validate it and surface a ParsedPolicy dataclass
with everything the guardrail builder needs.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any

# Bedrock model to use for parsing. Haiku 4.5 is fast and cheap for extraction.
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Bedrock Guardrails supports these PII entity type identifiers.
VALID_PII_TYPES = {
    "ADDRESS", "AGE", "AWS_ACCESS_KEY", "AWS_SECRET_KEY", "CA_HEALTH_NUMBER",
    "CA_SOCIAL_INSURANCE_NUMBER", "CREDIT_DEBIT_CARD_CVV", "CREDIT_DEBIT_CARD_EXPIRY",
    "CREDIT_DEBIT_CARD_NUMBER", "DRIVER_ID", "EMAIL", "INTERNATIONAL_BANK_ACCOUNT_NUMBER",
    "IP_ADDRESS", "LICENSE_PLATE", "MAC_ADDRESS", "NAME", "PASSWORD", "PHONE",
    "PIN", "SWIFT_CODE", "UK_NATIONAL_HEALTH_SERVICE_NUMBER", "UK_NATIONAL_INSURANCE_NUMBER",
    "UK_UNIQUE_TAXPAYER_REFERENCE_NUMBER", "URL", "USERNAME", "US_BANK_ACCOUNT_NUMBER",
    "US_BANK_ROUTING_NUMBER", "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER",
    "US_PASSPORT_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "VEHICLE_IDENTIFICATION_NUMBER",
}

_EXTRACTION_PROMPT = textwrap.dedent("""
You are a security policy analyst. Your task is to read the corporate policy document
below and extract structured information for configuring an Amazon Bedrock Guardrail.

Return ONLY a valid JSON object — no prose, no markdown fences — with exactly this shape:

{
  "guardrail_name": "<short slug, lowercase, hyphens, max 50 chars>",
  "description": "<one sentence summary of what this policy governs>",
  "classification_tiers": [
    {
      "name": "<tier name, e.g. Public / Internal / Confidential / Restricted>",
      "sensitivity": "<public | internal | confidential | restricted>",
      "description": "<what data belongs in this tier>"
    }
  ],
  "denied_topics": [
    {
      "name": "<topic name, max 100 chars>",
      "definition": "<what this topic covers, used verbatim in the Guardrail definition field>",
      "examples": ["<example input>", "<example input>"]
    }
  ],
  "blocked_input_phrases": ["<exact phrase or word>"],
  "blocked_output_phrases": ["<exact phrase or word>"],
  "pii_entities": [
    {
      "type": "<one of the valid PII types listed below>",
      "action": "ANONYMIZE | BLOCK"
    }
  ],
  "content_filters": {
    "hate": "NONE | LOW | MEDIUM | HIGH",
    "insults": "NONE | LOW | MEDIUM | HIGH",
    "sexual": "NONE | LOW | MEDIUM | HIGH",
    "violence": "NONE | LOW | MEDIUM | HIGH",
    "misconduct": "NONE | LOW | MEDIUM | HIGH",
    "prompt_attack": "NONE | LOW | MEDIUM | HIGH"
  },
  "grounding_threshold": null,
  "relevance_threshold": null
}

Rules:
- denied_topics: extract actual prohibited topics from the policy (e.g. competitor discussions,
  legal advice, financial recommendations, etc.). Leave [] if none found.
- blocked_input_phrases: extract explicit prohibited words/phrases that must never appear in
  user inputs. Common examples: internal codenames, swear words, classified project names.
- blocked_output_phrases: extract words/phrases that must never appear in model outputs.
- pii_entities: map data types the policy requires protection for to valid Bedrock PII types.
  Valid PII types: """ + ", ".join(sorted(VALID_PII_TYPES)) + """
  Only include PII types that are clearly implied by the policy.
- content_filters: set filter strength based on policy strictness. Use HIGH for policies that
  mention zero-tolerance; MEDIUM for standard corporate policies; LOW for permissive policies.
  Set prompt_attack to HIGH if the policy mentions adversarial inputs or AI security.
- grounding_threshold / relevance_threshold: set to a float 0.0-1.0 if the policy mentions
  factual accuracy or staying on-topic requirements, otherwise null.
- If a field cannot be derived from the policy, use an empty list or null as appropriate.
- denied_topics must have between 0 and 30 entries.
- blocked_input_phrases and blocked_output_phrases must have between 0 and 10000 entries each,
  but keep them focused: only include phrases explicitly named in the policy.

POLICY DOCUMENT:
{policy_text}
""")


@dataclass
class ClassificationTier:
    name: str
    sensitivity: str
    description: str


@dataclass
class DeniedTopic:
    name: str
    definition: str
    examples: list[str] = field(default_factory=list)


@dataclass
class PiiEntity:
    type: str
    action: str


@dataclass
class ContentFilters:
    hate: str = "MEDIUM"
    insults: str = "MEDIUM"
    sexual: str = "HIGH"
    violence: str = "MEDIUM"
    misconduct: str = "MEDIUM"
    prompt_attack: str = "HIGH"


@dataclass
class ParsedPolicy:
    guardrail_name: str
    description: str
    classification_tiers: list[ClassificationTier]
    denied_topics: list[DeniedTopic]
    blocked_input_phrases: list[str]
    blocked_output_phrases: list[str]
    pii_entities: list[PiiEntity]
    content_filters: ContentFilters
    grounding_threshold: float | None
    relevance_threshold: float | None
    raw: dict[str, Any] = field(default_factory=dict)


class PolicyParser:
    """Invoke Claude to extract structured policy fields from free-text policy documents."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = "us-west-2",
        max_policy_chars: int = 60_000,
    ):
        self.model_id = model_id
        self.region = region
        self.max_policy_chars = max_policy_chars
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def parse(self, policy_text: str) -> ParsedPolicy:
        """Send policy_text to Claude and return a structured ParsedPolicy."""
        truncated = policy_text[: self.max_policy_chars]
        if len(policy_text) > self.max_policy_chars:
            truncated += "\n\n[... document truncated for length ...]"

        prompt = _EXTRACTION_PROMPT.replace("{policy_text}", truncated)

        client = self._get_client()
        resp = client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )
        content = resp["output"]["message"]["content"]
        raw_text = "".join(c.get("text", "") for c in content)

        return self._parse_response(raw_text)

    def _parse_response(self, raw_text: str) -> ParsedPolicy:
        """Extract JSON from the model response and validate it into ParsedPolicy."""
        json_text = _extract_json(raw_text)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Model returned invalid JSON: {e}\n\nRaw response:\n{raw_text}") from e

        return _coerce(data)


def _extract_json(text: str) -> str:
    """Pull the first {...} block from text, tolerating markdown code fences."""
    # Strip ```json ... ``` fences if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    # Find outermost { ... }
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model response:\n{text[:400]}")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("Unterminated JSON object in model response")


def _coerce(data: dict[str, Any]) -> ParsedPolicy:
    tiers = [
        ClassificationTier(
            name=t.get("name", ""),
            sensitivity=t.get("sensitivity", "internal"),
            description=t.get("description", ""),
        )
        for t in data.get("classification_tiers", [])
    ]

    topics = [
        DeniedTopic(
            name=t.get("name", ""),
            definition=t.get("definition", ""),
            examples=list(t.get("examples", [])),
        )
        for t in data.get("denied_topics", [])
        if t.get("name") and t.get("definition")
    ]

    pii = []
    for p in data.get("pii_entities", []):
        ptype = p.get("type", "").upper()
        action = p.get("action", "ANONYMIZE").upper()
        if ptype in VALID_PII_TYPES and action in ("ANONYMIZE", "BLOCK"):
            pii.append(PiiEntity(type=ptype, action=action))

    cf_raw = data.get("content_filters", {})
    valid_strengths = {"NONE", "LOW", "MEDIUM", "HIGH"}

    def _cf(key: str, default: str) -> str:
        v = str(cf_raw.get(key, default)).upper()
        return v if v in valid_strengths else default

    cf = ContentFilters(
        hate=_cf("hate", "MEDIUM"),
        insults=_cf("insults", "MEDIUM"),
        sexual=_cf("sexual", "HIGH"),
        violence=_cf("violence", "MEDIUM"),
        misconduct=_cf("misconduct", "MEDIUM"),
        prompt_attack=_cf("prompt_attack", "HIGH"),
    )

    def _threshold(key: str) -> float | None:
        v = data.get(key)
        if v is None:
            return None
        try:
            f = float(v)
            return max(0.0, min(1.0, f))
        except (TypeError, ValueError):
            return None

    name = re.sub(r"[^a-z0-9-]", "-", data.get("guardrail_name", "policy-guardrail").lower())
    name = re.sub(r"-{2,}", "-", name).strip("-")[:50] or "policy-guardrail"

    return ParsedPolicy(
        guardrail_name=name,
        description=str(data.get("description", "Generated from policy document"))[:200],
        classification_tiers=tiers,
        denied_topics=topics,
        blocked_input_phrases=[
            str(p) for p in data.get("blocked_input_phrases", []) if str(p).strip()
        ],
        blocked_output_phrases=[
            str(p) for p in data.get("blocked_output_phrases", []) if str(p).strip()
        ],
        pii_entities=pii,
        content_filters=cf,
        grounding_threshold=_threshold("grounding_threshold"),
        relevance_threshold=_threshold("relevance_threshold"),
        raw=data,
    )
