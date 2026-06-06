"""Router configuration: tier definitions and per-task overrides.

A "tier" is an ordered list of Bedrock model IDs tried cheapest-first.
The router picks the cheapest tier that meets the task's quality threshold.

Default cascade (cheapest → most capable):
  tier-0  amazon.nova-micro-v1:0            $0.000035/$0.00014 per 1K
  tier-1  amazon.nova-lite-v1:0             $0.00006/$0.00024
  tier-2  amazon.nova-pro-v1:0              $0.0008/$0.0032
  tier-3  anthropic.claude-haiku-4-5-...    $0.001/$0.005
  tier-4  anthropic.claude-sonnet-4-6       $0.003/$0.015
  tier-5  anthropic.claude-opus-4-7         $0.015/$0.075

Pricing reflects on-demand list prices at time of writing; verify against
https://aws.amazon.com/bedrock/pricing/ before relying on them for budgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TierConfig:
    tier_id: str
    model_id: str
    max_tokens: int = 4096
    temperature: float = 0.0
    # Pricing ($/1K tokens) for cost tracking
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    # Optional: Bedrock cross-region inference profile ARN
    inference_profile_arn: Optional[str] = None

    @property
    def effective_model_id(self) -> str:
        return self.inference_profile_arn or self.model_id


# Default tier ordering: cheapest → most capable
DEFAULT_TIERS: list[TierConfig] = [
    TierConfig(
        tier_id="nano",
        model_id="amazon.nova-micro-v1:0",
        input_cost_per_1k=0.000035,
        output_cost_per_1k=0.00014,
    ),
    TierConfig(
        tier_id="micro",
        model_id="amazon.nova-lite-v1:0",
        input_cost_per_1k=0.00006,
        output_cost_per_1k=0.00024,
    ),
    TierConfig(
        tier_id="small",
        model_id="amazon.nova-pro-v1:0",
        input_cost_per_1k=0.0008,
        output_cost_per_1k=0.0032,
    ),
    TierConfig(
        tier_id="medium",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        input_cost_per_1k=0.001,
        output_cost_per_1k=0.005,
    ),
    TierConfig(
        tier_id="large",
        model_id="us.anthropic.claude-sonnet-4-6",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
    ),
    TierConfig(
        tier_id="flagship",
        model_id="us.anthropic.claude-opus-4-7",
        input_cost_per_1k=0.015,
        output_cost_per_1k=0.075,
    ),
]

# Llama and Mistral fallback tiers (opt-in via config)
LLAMA_TIERS: list[TierConfig] = [
    TierConfig(
        tier_id="llama-small",
        model_id="meta.llama3-8b-instruct-v1:0",
        input_cost_per_1k=0.0003,
        output_cost_per_1k=0.0006,
    ),
    TierConfig(
        tier_id="llama-large",
        model_id="meta.llama3-70b-instruct-v1:0",
        input_cost_per_1k=0.00265,
        output_cost_per_1k=0.0035,
    ),
]

MISTRAL_TIERS: list[TierConfig] = [
    TierConfig(
        tier_id="mistral-small",
        model_id="mistral.mistral-small-2402-v1:0",
        input_cost_per_1k=0.001,
        output_cost_per_1k=0.003,
    ),
    TierConfig(
        tier_id="mistral-large",
        model_id="mistral.mistral-large-2402-v1:0",
        input_cost_per_1k=0.004,
        output_cost_per_1k=0.012,
    ),
]


@dataclass
class RouterConfig:
    """Full router configuration.

    tiers           : ordered cheapest-first list of TierConfig
    min_tier_id     : never route below this tier (floor)
    max_tier_id     : never route above this tier (ceiling)
    task_overrides  : {task_label: min_tier_id} — pin specific tasks to a
                      minimum tier (e.g. "code_generation" -> "large")
    quality_threshold: 0.0–1.0; classifier confidence must exceed this to
                      use the cheaper tier (default 0.7)
    retry_on_refusal: escalate to next tier when current model refuses/errors
    max_retries     : maximum escalation steps before raising
    """

    tiers: list[TierConfig] = field(default_factory=lambda: list(DEFAULT_TIERS))
    min_tier_id: Optional[str] = None
    max_tier_id: Optional[str] = None
    task_overrides: dict[str, str] = field(default_factory=dict)
    quality_threshold: float = 0.70
    retry_on_refusal: bool = True
    max_retries: int = 3

    def tier_by_id(self, tier_id: str) -> Optional[TierConfig]:
        for t in self.tiers:
            if t.tier_id == tier_id:
                return t
        return None

    def tier_index(self, tier_id: str) -> int:
        for i, t in enumerate(self.tiers):
            if t.tier_id == tier_id:
                return i
        return -1

    def floor_index(self) -> int:
        if self.min_tier_id:
            idx = self.tier_index(self.min_tier_id)
            return max(0, idx)
        return 0

    def ceiling_index(self) -> int:
        if self.max_tier_id:
            idx = self.tier_index(self.max_tier_id)
            if idx >= 0:
                return idx
        return len(self.tiers) - 1
