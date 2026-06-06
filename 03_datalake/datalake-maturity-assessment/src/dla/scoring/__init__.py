"""Scoring aggregation: applies weights to dimension levels and computes summary stats."""
from .rules import aggregate, load_weights

__all__ = ["aggregate", "load_weights"]
