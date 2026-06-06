"""Sampling policy: keep 100% of flagged events, sample N% of normal traffic."""

from __future__ import annotations

import os
import random

_SAMPLE_RATE = None


def _get_sample_rate() -> float:
    global _SAMPLE_RATE
    if _SAMPLE_RATE is None:
        _SAMPLE_RATE = float(os.environ.get("SAMPLE_RATE", "100")) / 100.0
    return _SAMPLE_RATE


def should_keep(record: dict) -> bool:
    """Returns True if this record should be retained in the audit trail."""
    # Always keep flagged events
    if record.get("pii_detected"):
        return True
    if record.get("guardrail_action") in ("INTERVENED", "BLOCKED"):
        return True
    if record.get("injection_detected"):
        return True
    if record.get("error_code"):
        return True
    # Sample normal traffic
    return random.random() < _get_sample_rate()
