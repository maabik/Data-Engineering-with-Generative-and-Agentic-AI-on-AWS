"""Task complexity classifier.

Estimates the complexity of a prompt using two strategies:

1. Rule-based heuristics (no API call, ~0 cost):
   - Token length bands
   - Keyword signals (code, math, multi-step, summarise, …)
   - Structural signals (multi-message conversation depth)

2. LLM-as-classifier (one cheap model call):
   Uses a fixed meta-prompt sent to the cheapest configured tier to
   classify the task and return a JSON complexity label + confidence.
   The meta-prompt itself is short (~150 tokens) so the overhead is
   < $0.00001 per routing decision.

The router uses (1) by default and falls back to (2) when the
heuristic confidence is below `quality_threshold`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TaskComplexity(str, Enum):
    TRIVIAL = "trivial"       # single-fact lookup, simple Q&A
    SIMPLE = "simple"         # short explanation, basic extraction
    MODERATE = "moderate"     # multi-step reasoning, short code
    COMPLEX = "complex"       # long-form generation, deep analysis
    EXPERT = "expert"         # research-grade, adversarial, high-stakes


# Maps complexity level → minimum tier_id in the default cascade
COMPLEXITY_TO_TIER: dict[TaskComplexity, str] = {
    TaskComplexity.TRIVIAL:  "nano",
    TaskComplexity.SIMPLE:   "micro",
    TaskComplexity.MODERATE: "small",
    TaskComplexity.COMPLEX:  "large",
    TaskComplexity.EXPERT:   "flagship",
}


@dataclass
class ClassifierResult:
    complexity: TaskComplexity
    confidence: float           # 0.0 – 1.0
    reason: str = ""
    strategy: str = "heuristic" # "heuristic" | "llm"


# ── Keyword tables ─────────────────────────────────────────────────────────────

_TRIVIAL_PATTERNS = [
    r"\bwhat is\b", r"\bwho is\b", r"\bwhen (was|did|is)\b",
    r"\bdefine\b", r"\bname (a|the|one)\b", r"\btrue or false\b",
]
_COMPLEX_PATTERNS = [
    r"\bwrite (a|an|the)\b.{0,30}\b(function|class|program|script|api)\b",
    r"\bimplement\b", r"\bdesign\b.{0,20}\b(system|architecture|schema)\b",
    r"\banalyse\b", r"\banalyze\b", r"\bcompare.{0,30}(advantages|trade.?offs)\b",
    r"\brefactor\b", r"\bdebug\b", r"\bexplain.{0,30}(how|why|mechanism)\b",
    r"\bstep.by.step\b", r"\bmulti.step\b",
]
_EXPERT_PATTERNS = [
    r"\badversarial\b", r"\bjailbreak\b", r"\bbypass\b.{0,20}\bfilter\b",
    r"\bproof\b", r"\btheorem\b", r"\bformal (verification|proof)\b",
    r"\bsecurity (audit|review|pentest)\b",
    r"\bcomprehensive (report|analysis|review)\b",
]


def _token_estimate(text: str) -> int:
    """Rough token estimate: words / 0.75."""
    return int(len(text.split()) / 0.75)


def _match_any(text: str, patterns: list[str]) -> bool:
    tl = text.lower()
    return any(re.search(p, tl) for p in patterns)


def classify_heuristic(
    messages: list[dict],
    system: Optional[str] = None,
) -> ClassifierResult:
    """Rule-based classifier — zero API cost."""
    # Combine all user/assistant turns
    full_text = " ".join(
        part.get("text", "") if isinstance(part, dict) else str(part)
        for msg in messages
        for part in (msg.get("content", []) if isinstance(msg.get("content"), list)
                     else [{"text": str(msg.get("content", ""))}])
    )
    if system:
        full_text = system + " " + full_text

    tokens = _token_estimate(full_text)
    depth = len(messages)

    # Hard signals
    if _match_any(full_text, _EXPERT_PATTERNS):
        return ClassifierResult(TaskComplexity.EXPERT, 0.85, "expert keyword match")
    if _match_any(full_text, _COMPLEX_PATTERNS) or tokens > 800 or depth >= 6:
        return ClassifierResult(TaskComplexity.COMPLEX, 0.80, "complex keyword/length")
    if _match_any(full_text, _TRIVIAL_PATTERNS) and tokens < 50:
        return ClassifierResult(TaskComplexity.TRIVIAL, 0.90, "trivial keyword match")

    # Length-based bands
    if tokens < 80:
        return ClassifierResult(TaskComplexity.SIMPLE, 0.72, f"short prompt ({tokens} tokens)")
    if tokens < 300:
        return ClassifierResult(TaskComplexity.MODERATE, 0.68, f"medium prompt ({tokens} tokens)")
    return ClassifierResult(TaskComplexity.COMPLEX, 0.75, f"long prompt ({tokens} tokens)")


_CLASSIFIER_SYSTEM = """You are a routing classifier. Given a user prompt, output JSON only:
{"complexity": "<trivial|simple|moderate|complex|expert>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}

Guidelines:
- trivial: single-fact lookup, yes/no, basic definition
- simple: short explanation, basic extraction, conversion
- moderate: multi-step reasoning, short code, summarisation
- complex: long-form generation, deep analysis, substantial code
- expert: research-grade, security-sensitive, formal proof, adversarial"""


def classify_llm(
    messages: list[dict],
    bedrock_client,
    classifier_model_id: str = "amazon.nova-micro-v1:0",
    system: Optional[str] = None,
) -> ClassifierResult:
    """LLM-based classifier using the cheapest available model."""
    # Build a compact representation of the conversation
    summary_parts = []
    for msg in messages[-3:]:  # last 3 turns max
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        else:
            text = str(content)
        summary_parts.append(f"[{role}]: {text[:300]}")

    payload = "\n".join(summary_parts)

    try:
        resp = bedrock_client.converse(
            modelId=classifier_model_id,
            system=[{"text": _CLASSIFIER_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": payload}]}],
            inferenceConfig={"maxTokens": 100, "temperature": 0.0},
        )
        raw = resp["output"]["message"]["content"][0]["text"].strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        complexity = TaskComplexity(data.get("complexity", "moderate"))
        confidence = float(data.get("confidence", 0.7))
        reason = data.get("reason", "")
        return ClassifierResult(complexity, confidence, reason, strategy="llm")
    except Exception as e:
        # Fall back to heuristic on any LLM/parse error
        result = classify_heuristic(messages, system)
        result.reason = f"llm_fallback({e}): {result.reason}"
        result.strategy = "llm_fallback"
        return result


class TaskClassifier:
    """Unified classifier: tries heuristic first, escalates to LLM if needed."""

    def __init__(
        self,
        quality_threshold: float = 0.70,
        classifier_model_id: str = "amazon.nova-micro-v1:0",
        bedrock_client=None,
    ):
        self.quality_threshold = quality_threshold
        self.classifier_model_id = classifier_model_id
        self._client = bedrock_client

    def classify(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        force_llm: bool = False,
    ) -> ClassifierResult:
        if not force_llm:
            result = classify_heuristic(messages, system)
            if result.confidence >= self.quality_threshold:
                return result
        # Low-confidence heuristic → use LLM classifier
        if self._client is not None:
            return classify_llm(messages, self._client, self.classifier_model_id, system)
        # No client available — return heuristic result anyway
        return classify_heuristic(messages, system)
