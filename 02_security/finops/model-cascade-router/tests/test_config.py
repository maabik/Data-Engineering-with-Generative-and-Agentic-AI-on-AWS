"""Tests for router/config.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from router.config import RouterConfig, TierConfig, DEFAULT_TIERS


class TestTierConfig:
    def test_effective_model_id_uses_profile_if_set(self):
        t = TierConfig(tier_id="x", model_id="base-model",
                       inference_profile_arn="arn:aws:bedrock::p:profile")
        assert t.effective_model_id == "arn:aws:bedrock::p:profile"

    def test_effective_model_id_falls_back_to_model_id(self):
        t = TierConfig(tier_id="x", model_id="base-model")
        assert t.effective_model_id == "base-model"


class TestDefaultTiers:
    def test_six_tiers_in_default(self):
        assert len(DEFAULT_TIERS) == 6

    def test_nano_is_cheapest(self):
        nano = DEFAULT_TIERS[0]
        assert nano.tier_id == "nano"
        assert nano.output_cost_per_1k < DEFAULT_TIERS[-1].output_cost_per_1k

    def test_flagship_is_most_expensive(self):
        flagship = DEFAULT_TIERS[-1]
        assert flagship.tier_id == "flagship"

    def test_tiers_ordered_by_cost(self):
        costs = [t.output_cost_per_1k for t in DEFAULT_TIERS]
        assert costs == sorted(costs)


class TestRouterConfig:
    def _config(self, **kw):
        return RouterConfig(**kw)

    def test_tier_by_id_found(self):
        cfg = self._config()
        t = cfg.tier_by_id("small")
        assert t is not None
        assert t.tier_id == "small"

    def test_tier_by_id_not_found(self):
        cfg = self._config()
        assert cfg.tier_by_id("nonexistent") is None

    def test_tier_index(self):
        cfg = self._config()
        assert cfg.tier_index("nano") == 0
        assert cfg.tier_index("flagship") == len(DEFAULT_TIERS) - 1
        assert cfg.tier_index("missing") == -1

    def test_floor_index_default_zero(self):
        assert self._config().floor_index() == 0

    def test_floor_index_custom(self):
        cfg = RouterConfig(min_tier_id="small")
        assert cfg.floor_index() == cfg.tier_index("small")

    def test_ceiling_index_default_last(self):
        cfg = self._config()
        assert cfg.ceiling_index() == len(DEFAULT_TIERS) - 1

    def test_ceiling_index_custom(self):
        cfg = RouterConfig(max_tier_id="large")
        assert cfg.ceiling_index() == cfg.tier_index("large")

    def test_task_overrides_stored(self):
        cfg = RouterConfig(task_overrides={"code_generation": "large"})
        assert cfg.task_overrides["code_generation"] == "large"
