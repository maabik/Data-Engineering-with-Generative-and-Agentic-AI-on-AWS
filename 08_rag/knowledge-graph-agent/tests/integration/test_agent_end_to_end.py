"""End-to-end integration smoke test for the KG agent.

Skipped unless KG_INTEGRATION=1 — depends on a live deployment with the
PE dataroom already ingested.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("KG_INTEGRATION") != "1",
    reason="KG_INTEGRATION=1 not set",
)


def test_pe_question_one_top_three_risks():
    from knowledge_graph_agent.agent.knowledge_graph_agent import run_local

    answer = run_local(
        "What are the top three integration risks for Acme Corp's acquisition of FinFlow?"
    )
    lower = answer.lower()
    # Loose check — the deal-blocking facts that should appear if the
    # pipeline is wired correctly.
    assert "megabank" in lower or "change of control" in lower
    assert "litigation" in lower or "webb" in lower
