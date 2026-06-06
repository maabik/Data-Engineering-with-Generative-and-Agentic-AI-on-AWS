"""Map a ParsedPolicy to the boto3 CreateGuardrail / UpdateGuardrail request shape.

Bedrock Guardrails API reference:
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateGuardrail.html
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .policy_parser import ParsedPolicy

# Bedrock strength name → numeric filter strength for the API
_FILTER_STRENGTH_MAP = {
    "NONE": "NONE",
    "LOW": "LOW",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
}

# Bedrock content filter type names
_CONTENT_FILTER_TYPES = {
    "hate": "HATE",
    "insults": "INSULTS",
    "sexual": "SEXUAL",
    "violence": "VIOLENCE",
    "misconduct": "MISCONDUCT",
    "prompt_attack": "PROMPT_ATTACK",
}


@dataclass
class GuardrailConfig:
    """The full kwargs dict for boto3 bedrock.create_guardrail(**config.as_boto3_kwargs())."""

    name: str
    description: str
    topic_policy_config: dict[str, Any] | None
    content_policy_config: dict[str, Any] | None
    word_policy_config: dict[str, Any] | None
    sensitive_information_policy_config: dict[str, Any] | None
    contextual_grounding_policy_config: dict[str, Any] | None
    blocked_input_messaging: str
    blocked_outputs_messaging: str

    def as_boto3_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "blockedInputMessaging": self.blocked_input_messaging,
            "blockedOutputsMessaging": self.blocked_outputs_messaging,
        }
        if self.topic_policy_config:
            kwargs["topicPolicyConfig"] = self.topic_policy_config
        if self.content_policy_config:
            kwargs["contentPolicyConfig"] = self.content_policy_config
        if self.word_policy_config:
            kwargs["wordPolicyConfig"] = self.word_policy_config
        if self.sensitive_information_policy_config:
            kwargs["sensitiveInformationPolicyConfig"] = self.sensitive_information_policy_config
        if self.contextual_grounding_policy_config:
            kwargs["contextualGroundingPolicyConfig"] = self.contextual_grounding_policy_config
        return kwargs

    def as_dict(self) -> dict[str, Any]:
        """Human-readable representation of the config (for --dry-run / JSON output)."""
        return self.as_boto3_kwargs()


def build_guardrail_config(policy: ParsedPolicy) -> GuardrailConfig:
    """Convert a ParsedPolicy into a GuardrailConfig ready for the Bedrock API."""
    topic_policy = _build_topic_policy(policy)
    content_policy = _build_content_policy(policy)
    word_policy = _build_word_policy(policy)
    pii_policy = _build_pii_policy(policy)
    grounding_policy = _build_grounding_policy(policy)

    # Derive messaging from the highest sensitivity tier found, or use a safe default.
    sensitivity_levels = {t.sensitivity.lower() for t in policy.classification_tiers}
    if "restricted" in sensitivity_levels:
        input_msg = (
            "This request was blocked because it may involve restricted information. "
            "Please contact your security team."
        )
        output_msg = (
            "The response was blocked because it may contain restricted information. "
            "Please contact your security team."
        )
    elif "confidential" in sensitivity_levels:
        input_msg = (
            "This request was blocked because it may involve confidential information. "
            "Rephrase your question or contact IT support."
        )
        output_msg = (
            "The response was blocked because it may contain confidential information. "
            "Contact IT support if you believe this is an error."
        )
    else:
        input_msg = "This request was blocked by the content policy. Please rephrase your request."
        output_msg = "The response was blocked by the content policy. Please try a different query."

    return GuardrailConfig(
        name=policy.guardrail_name,
        description=policy.description,
        topic_policy_config=topic_policy,
        content_policy_config=content_policy,
        word_policy_config=word_policy,
        sensitive_information_policy_config=pii_policy,
        contextual_grounding_policy_config=grounding_policy,
        blocked_input_messaging=input_msg,
        blocked_outputs_messaging=output_msg,
    )


def _build_topic_policy(policy: ParsedPolicy) -> dict[str, Any] | None:
    if not policy.denied_topics:
        return None
    topics = []
    for t in policy.denied_topics:
        topic: dict[str, Any] = {
            "name": t.name[:100],
            "definition": t.definition[:200],
            "type": "DENY",
        }
        if t.examples:
            topic["examples"] = [str(e)[:100] for e in t.examples[:5]]
        topics.append(topic)
    return {"topicsConfig": topics}


def _build_content_policy(policy: ParsedPolicy) -> dict[str, Any] | None:
    cf = policy.content_filters
    filters = []
    for attr, api_type in _CONTENT_FILTER_TYPES.items():
        strength = getattr(cf, attr, "MEDIUM").upper()
        if strength == "NONE":
            continue
        # prompt_attack applies only to INPUT; the others apply to both
        if attr == "prompt_attack":
            filters.append({
                "type": api_type,
                "inputStrength": _FILTER_STRENGTH_MAP.get(strength, "MEDIUM"),
                "outputStrength": "NONE",
            })
        else:
            filters.append({
                "type": api_type,
                "inputStrength": _FILTER_STRENGTH_MAP.get(strength, "MEDIUM"),
                "outputStrength": _FILTER_STRENGTH_MAP.get(strength, "MEDIUM"),
            })
    if not filters:
        return None
    return {"filtersConfig": filters}


def _build_word_policy(policy: ParsedPolicy) -> dict[str, Any] | None:
    words: list[dict[str, str]] = []
    # Blocked input phrases go into wordsConfig (Guardrails checks them in both directions)
    all_phrases = list(dict.fromkeys(
        policy.blocked_input_phrases + policy.blocked_output_phrases
    ))
    for phrase in all_phrases[:10000]:
        phrase = phrase.strip()
        if phrase:
            words.append({"text": phrase})
    if not words:
        return None
    return {"wordsConfig": words}


def _build_pii_policy(policy: ParsedPolicy) -> dict[str, Any] | None:
    if not policy.pii_entities:
        return None
    entities = [
        {"type": p.type, "action": p.action}
        for p in policy.pii_entities
    ]
    return {"piiEntitiesConfig": entities}


def _build_grounding_policy(policy: ParsedPolicy) -> dict[str, Any] | None:
    filters = []
    if policy.grounding_threshold is not None:
        filters.append({
            "type": "GROUNDING",
            "threshold": round(policy.grounding_threshold, 2),
        })
    if policy.relevance_threshold is not None:
        filters.append({
            "type": "RELEVANCE",
            "threshold": round(policy.relevance_threshold, 2),
        })
    if not filters:
        return None
    return {"filtersConfig": filters}
