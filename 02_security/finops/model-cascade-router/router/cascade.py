"""CascadeRouter: the main routing engine.

Routing algorithm
-----------------
1. Classify the prompt → TaskComplexity + confidence.
2. Resolve the minimum tier from (a) complexity mapping, (b) task_overrides,
   (c) config floor.  Never exceed config ceiling.
3. Call the selected tier's model.
4. On refusal / throttle / error, escalate to the next tier (up to max_retries).
5. Return RouteDecision with the full audit trail.

The router is a thin wrapper around boto3 bedrock-runtime.converse().
It does NOT add streaming support — callers that need converse_stream()
should use the returned `tier` and call the client directly.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import boto3

from .classifier import (
    COMPLEXITY_TO_TIER,
    ClassifierResult,
    TaskClassifier,
    TaskComplexity,
)
from .config import RouterConfig, TierConfig

logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    """Full audit trail for one routing decision."""
    # Selected model
    tier: TierConfig
    model_id: str
    # Classifier output
    classifier: ClassifierResult
    # Cost tracking
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # Escalation path (tier_ids tried before landing here)
    escalation_path: list[str] = field(default_factory=list)
    # Raw Bedrock response (set after invoke)
    response: Optional[dict] = None
    # Error if all tiers exhausted
    error: Optional[str] = None
    # Timing
    latency_ms: int = 0

    @property
    def success(self) -> bool:
        return self.response is not None and self.error is None


def _is_refusal(response: dict) -> bool:
    """Detect model refusals or content policy blocks."""
    stop = response.get("stopReason", "")
    if stop in ("content_filtered", "guardrail_intervened"):
        return True
    # Heuristic: very short output after a substantial prompt → possible refusal
    usage = response.get("usage", {})
    output_tokens = usage.get("outputTokens", 999)
    if output_tokens < 5:
        return True
    return False


def _estimate_cost(tier: TierConfig, input_t: int, output_t: int) -> float:
    return round(
        (input_t / 1000.0) * tier.input_cost_per_1k
        + (output_t / 1000.0) * tier.output_cost_per_1k,
        8,
    )


class CascadeRouter:
    """Drop-in router that selects the cheapest model tier capable of a task.

    Usage::

        router = CascadeRouter()
        decision = router.route(
            messages=[{"role": "user", "content": [{"text": "What is 2+2?"}]}],
        )
        print(decision.response["output"]["message"]["content"][0]["text"])
        print(f"Cost: ${decision.estimated_cost_usd:.6f}  Tier: {decision.tier.tier_id}")
    """

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
        bedrock_client=None,
        region: Optional[str] = None,
    ):
        self.config = config or RouterConfig()
        self._region = region
        self._bedrock = bedrock_client or boto3.client(
            "bedrock-runtime", region_name=region
        )
        self._classifier = TaskClassifier(
            quality_threshold=self.config.quality_threshold,
            bedrock_client=self._bedrock,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify(
        self,
        messages: list[dict],
        system: Optional[str] = None,
    ) -> ClassifierResult:
        """Classify without routing (useful for dry-runs and evals)."""
        return self._classifier.classify(messages, system)

    def route(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        task_label: Optional[str] = None,
        inference_config: Optional[dict] = None,
        additional_model_fields: Optional[dict] = None,
        request_metadata: Optional[dict] = None,
    ) -> RouteDecision:
        """Classify and invoke. Returns RouteDecision with full audit trail."""
        classifier_result = self._classifier.classify(messages, system)
        start_tier_index = self._resolve_start_tier(classifier_result.complexity, task_label)
        ceiling = self.config.ceiling_index()

        escalation_path: list[str] = []
        last_error: Optional[str] = None
        retries = 0

        for tier_index in range(start_tier_index, ceiling + 1):
            if retries > self.config.max_retries:
                break
            tier = self.config.tiers[tier_index]

            if escalation_path:
                retries += 1

            t0 = time.monotonic()
            try:
                resp = self._invoke(
                    tier,
                    messages,
                    system,
                    inference_config,
                    additional_model_fields,
                    request_metadata,
                )
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                usage = resp.get("usage", {})
                input_t = usage.get("inputTokens", 0)
                output_t = usage.get("outputTokens", 0)

                if self.config.retry_on_refusal and _is_refusal(resp):
                    logger.info("Tier %s refused; escalating", tier.tier_id)
                    escalation_path.append(tier.tier_id)
                    last_error = "refusal"
                    continue

                return RouteDecision(
                    tier=tier,
                    model_id=tier.effective_model_id,
                    classifier=classifier_result,
                    input_tokens=input_t,
                    output_tokens=output_t,
                    estimated_cost_usd=_estimate_cost(tier, input_t, output_t),
                    escalation_path=escalation_path,
                    response=resp,
                    latency_ms=elapsed_ms,
                )

            except self._bedrock.exceptions.ThrottlingException:
                logger.info("Tier %s throttled; escalating", tier.tier_id)
                escalation_path.append(tier.tier_id)
                last_error = "throttled"
            except Exception as exc:
                logger.warning("Tier %s error: %s; escalating", tier.tier_id, exc)
                escalation_path.append(tier.tier_id)
                last_error = str(exc)

        # All tiers exhausted
        return RouteDecision(
            tier=self.config.tiers[ceiling],
            model_id=self.config.tiers[ceiling].effective_model_id,
            classifier=classifier_result,
            escalation_path=escalation_path,
            error=f"All tiers exhausted. Last error: {last_error}",
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _resolve_start_tier(
        self,
        complexity: TaskComplexity,
        task_label: Optional[str],
    ) -> int:
        # Start from complexity-mapped tier
        complexity_tier_id = COMPLEXITY_TO_TIER.get(complexity, "small")
        complexity_index = self.config.tier_index(complexity_tier_id)
        if complexity_index < 0:
            complexity_index = 0

        # Apply task override (floor per task type)
        if task_label and task_label in self.config.task_overrides:
            override_id = self.config.task_overrides[task_label]
            override_index = self.config.tier_index(override_id)
            if override_index >= 0:
                complexity_index = max(complexity_index, override_index)

        # Apply global floor
        floor = self.config.floor_index()
        start = max(complexity_index, floor)

        # Cap at ceiling
        ceiling = self.config.ceiling_index()
        return min(start, ceiling)

    def _invoke(
        self,
        tier: TierConfig,
        messages: list[dict],
        system: Optional[str],
        inference_config: Optional[dict],
        additional_model_fields: Optional[dict],
        request_metadata: Optional[dict],
    ) -> dict:
        kwargs: dict = {
            "modelId": tier.effective_model_id,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": tier.max_tokens,
                "temperature": tier.temperature,
                **(inference_config or {}),
            },
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if additional_model_fields:
            kwargs["additionalModelRequestFields"] = additional_model_fields
        if request_metadata:
            kwargs["requestMetadata"] = request_metadata

        logger.debug("Invoking tier=%s model=%s", tier.tier_id, tier.effective_model_id)
        return self._bedrock.converse(**kwargs)
