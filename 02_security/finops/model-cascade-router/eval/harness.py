"""Task-level eval harness.

Answers: "Is model X good enough for task Y?"

Two evaluation strategies
--------------------------
1. LLM-as-judge (local, fast, free beyond model cost):
   A judge model (default: claude-sonnet-4-6) rates each response 1–5
   on correctness, completeness, and helpfulness.  Returns a pass/fail
   per test case based on a min_score threshold.

2. Bedrock Evaluations (managed, async):
   Submits a job to the Bedrock Model Evaluation API and polls until
   completion.  Returns the job ARN and summary metrics when done.
   Use this for formal sign-off before promoting a routing rule.

Workflow
--------
    harness = EvalHarness(bedrock_client=client)
    suite = EvalSuite.from_jsonl("eval/suites/qa_simple.jsonl")
    report = harness.run_local(
        suite,
        model_id="amazon.nova-micro-v1:0",
        min_score=3.5,
    )
    print(report.summary())
    # → "Passed 47/50 (94%) — avg score 4.12 — cost $0.0014"
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class EvalCase:
    """A single prompt/expected-answer pair."""
    case_id: str
    messages: list[dict]
    system: Optional[str] = None
    expected_answer: Optional[str] = None
    task_label: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        return cls(
            case_id=d.get("case_id", ""),
            messages=d["messages"],
            system=d.get("system"),
            expected_answer=d.get("expected_answer"),
            task_label=d.get("task_label"),
            metadata=d.get("metadata", {}),
        )


@dataclass
class EvalSuite:
    name: str
    cases: list[EvalCase]

    @classmethod
    def from_jsonl(cls, path: str | Path, name: Optional[str] = None) -> "EvalSuite":
        p = Path(path)
        cases = []
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                cases.append(EvalCase.from_dict(json.loads(line)))
        return cls(name=name or p.stem, cases=cases)

    @classmethod
    def from_list(cls, name: str, cases: list[dict]) -> "EvalSuite":
        return cls(name=name, cases=[EvalCase.from_dict(c) for c in cases])


@dataclass
class CaseResult:
    case_id: str
    model_id: str
    response_text: str
    score: float               # 1.0 – 5.0
    passed: bool
    judge_reasoning: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    error: Optional[str] = None


@dataclass
class EvalReport:
    model_id: str
    suite_name: str
    min_score: float
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_score(self) -> float:
        scores = [r.score for r in self.results if not r.error]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def total_cost_usd(self) -> float:
        return sum(r.estimated_cost_usd for r in self.results)

    def summary(self) -> str:
        return (
            f"Model: {self.model_id}  Suite: {self.suite_name}\n"
            f"Passed {self.passed}/{self.total} ({self.pass_rate:.0%})  "
            f"avg score {self.avg_score:.2f}/5.0  "
            f"total cost ${self.total_cost_usd:.4f}"
        )

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "suite_name": self.suite_name,
            "min_score": self.min_score,
            "passed": self.passed,
            "total": self.total,
            "pass_rate": round(self.pass_rate, 4),
            "avg_score": round(self.avg_score, 4),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "results": [
                {
                    "case_id": r.case_id,
                    "score": r.score,
                    "passed": r.passed,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cost_usd": r.estimated_cost_usd,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


# ── Pricing (for cost tracking during eval runs) ───────────────────────────────

_EVAL_PRICING: dict[str, tuple[float, float]] = {
    "amazon.nova-micro": (0.000035, 0.00014),
    "amazon.nova-lite": (0.00006, 0.00024),
    "amazon.nova-pro": (0.0008, 0.0032),
    # Claude 4.x family — current generation
    "anthropic.claude-haiku-4-5": (0.001, 0.005),
    "anthropic.claude-sonnet-4-6": (0.003, 0.015),
    "anthropic.claude-sonnet-4": (0.003, 0.015),
    "anthropic.claude-opus-4-7": (0.015, 0.075),
    "anthropic.claude-opus-4": (0.015, 0.075),
    # Claude 3.x — kept for legacy logs
    "anthropic.claude-3-haiku": (0.00025, 0.00125),
    "anthropic.claude-3-5-haiku": (0.0008, 0.004),
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "anthropic.claude-3-opus": (0.015, 0.075),
    "meta.llama3": (0.00065, 0.00065),
    "mistral.mistral-small": (0.001, 0.003),
    "mistral.mistral-large": (0.004, 0.012),
}


def _lookup_price(model_id: str) -> tuple[float, float]:
    import re
    ml = re.sub(r"^(us|eu|ap|global)\.", "", model_id.lower())
    best = ""
    for k in _EVAL_PRICING:
        if ml.startswith(k) and len(k) > len(best):
            best = k
    return _EVAL_PRICING.get(best, (0.0, 0.0))


def _estimate(model_id: str, inp: int, out: int) -> float:
    i, o = _lookup_price(model_id)
    return round((inp / 1000.0) * i + (out / 1000.0) * o, 8)


# ── Judge prompt ──────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """You are an impartial evaluator. Score the assistant's response on a 1–5 scale.

Criteria:
- 5: Perfectly correct, complete, and helpful
- 4: Mostly correct with minor omissions
- 3: Partially correct; key information present but incomplete
- 2: Mostly incorrect or unhelpful
- 1: Wrong, harmful, or refuses a legitimate request

Output JSON only: {"score": <1-5>, "reasoning": "<one sentence>"}"""


def _judge_response(
    bedrock_client,
    judge_model_id: str,
    case: EvalCase,
    response_text: str,
) -> tuple[float, str]:
    """Ask the judge model to score response_text for the given case."""
    user_text = case.messages[-1]["content"]
    if isinstance(user_text, list):
        user_text = " ".join(p.get("text", "") for p in user_text if isinstance(p, dict))

    judge_prompt = f"""User question: {user_text}

Assistant response: {response_text[:2000]}
"""
    if case.expected_answer:
        judge_prompt += f"\nExpected answer (for reference): {case.expected_answer[:500]}"

    try:
        resp = bedrock_client.converse(
            modelId=judge_model_id,
            system=[{"text": _JUDGE_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": judge_prompt}]}],
            inferenceConfig={"maxTokens": 150, "temperature": 0.0},
        )
        raw = resp["output"]["message"]["content"][0]["text"].strip()
        import re
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        return float(data.get("score", 3.0)), data.get("reasoning", "")
    except Exception as e:
        logger.warning("Judge call failed: %s", e)
        return 3.0, f"judge_error: {e}"


# ── EvalHarness ───────────────────────────────────────────────────────────────

class EvalHarness:
    """Runs evaluation suites against a target model using LLM-as-judge.

    Args:
        bedrock_client: boto3 bedrock-runtime client
        judge_model_id: model used to score responses
    """

    def __init__(
        self,
        bedrock_client,
        judge_model_id: str = "us.anthropic.claude-sonnet-4-6",
    ):
        self._client = bedrock_client
        self.judge_model_id = judge_model_id

    def run_local(
        self,
        suite: EvalSuite,
        model_id: str,
        min_score: float = 3.5,
        max_tokens: int = 1024,
        delay_between_cases: float = 0.0,
    ) -> EvalReport:
        """Run all cases locally using LLM-as-judge scoring."""
        report = EvalReport(
            model_id=model_id,
            suite_name=suite.name,
            min_score=min_score,
        )

        for case in suite.cases:
            if delay_between_cases:
                time.sleep(delay_between_cases)

            result = self._run_case(case, model_id, min_score, max_tokens)
            report.results.append(result)
            logger.info(
                "case=%s score=%.1f passed=%s",
                case.case_id, result.score, result.passed,
            )

        logger.info("Eval complete: %s", report.summary())
        return report

    def run_managed(
        self,
        suite: EvalSuite,
        model_id: str,
        output_s3_uri: str,
        role_arn: str,
        eval_name: Optional[str] = None,
        dataset_s3_uri: Optional[str] = None,
        bedrock_eval_client=None,
        s3_client=None,
    ) -> str:
        """Upload dataset to S3 and submit a Bedrock Model Evaluation job.

        Args:
            suite: EvalSuite whose cases are converted to Bedrock JSONL format.
            model_id: Bedrock model ID (or inference profile ARN) under test.
            output_s3_uri: S3 URI prefix for job output (e.g. s3://bucket/results/).
            role_arn: IAM role ARN trusted by bedrock.amazonaws.com with S3 + InvokeModel.
            eval_name: Optional job name; auto-generated if omitted.
            dataset_s3_uri: S3 URI for the uploaded dataset JSONL. If omitted,
                the dataset is uploaded to output_s3_uri/datasets/<suite.name>.jsonl.
            bedrock_eval_client: Optional pre-built boto3 bedrock client.
            s3_client: Optional pre-built boto3 s3 client.

        Returns:
            Job ARN string.
        """
        import boto3 as _boto3
        import re
        bedrock = bedrock_eval_client or _boto3.client("bedrock")
        s3 = s3_client or _boto3.client("s3")
        name = (eval_name or f"cascade-eval-{suite.name}-{int(time.time())}")
        # Job names must be ≤63 chars, alphanumeric + hyphens only
        name = re.sub(r"[^a-zA-Z0-9\-]", "-", name)[:63]

        # Build Bedrock evaluation JSONL (one record per case)
        lines = []
        for case in suite.cases:
            user_content = case.messages[-1].get("content", "")
            if isinstance(user_content, list):
                user_content = " ".join(
                    p.get("text", "") for p in user_content if isinstance(p, dict)
                )
            lines.append(json.dumps({
                "prompt": user_content,
                "referenceResponse": case.expected_answer or "",
            }))
        dataset_body = "\n".join(lines)

        # Upload dataset to S3
        if dataset_s3_uri is None:
            base = output_s3_uri.rstrip("/")
            dataset_s3_uri = f"{base}/datasets/{suite.name}.jsonl"

        m = re.match(r"s3://([^/]+)/(.+)", dataset_s3_uri)
        if not m:
            raise ValueError(f"Invalid dataset_s3_uri: {dataset_s3_uri!r}")
        bucket, key = m.group(1), m.group(2)
        s3.put_object(Bucket=bucket, Key=key, Body=dataset_body.encode())
        logger.info("Uploaded dataset to s3://%s/%s (%d cases)", bucket, key, len(lines))

        resp = bedrock.create_evaluation_job(
            jobName=name,
            roleArn=role_arn,
            evaluationConfig={
                "automated": {
                    "datasetMetricConfigs": [
                        {
                            "taskType": "QuestionAndAnswer",
                            "dataset": {
                                "name": suite.name,
                                "datasetLocation": {"s3Uri": dataset_s3_uri},
                            },
                            "metricNames": ["Builtin.Accuracy"],
                        }
                    ]
                }
            },
            inferenceConfig={
                "models": [
                    {"bedrockModel": {"modelIdentifier": model_id}}
                ]
            },
            outputDataConfig={"s3Uri": output_s3_uri},
        )
        job_arn = resp["jobArn"]
        logger.info("Bedrock eval job submitted: %s", job_arn)
        return job_arn

    def poll_managed(
        self,
        job_arn: str,
        timeout_s: int = 3600,
        poll_interval_s: int = 30,
        bedrock_eval_client=None,
    ) -> dict:
        """Poll a managed eval job until completion. Returns job details."""
        import boto3 as _boto3
        client = bedrock_eval_client or _boto3.client("bedrock")
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            resp = client.get_evaluation_job(jobIdentifier=job_arn)
            status = resp.get("status", "")
            if status == "Completed":
                return resp
            if status in ("Failed", "Stopped"):
                raise RuntimeError(f"Eval job {status}: {resp}")
            time.sleep(poll_interval_s)

        raise TimeoutError(f"Eval job {job_arn} did not complete within {timeout_s}s")

    # ── Private ────────────────────────────────────────────────────────────────

    def _run_case(
        self,
        case: EvalCase,
        model_id: str,
        min_score: float,
        max_tokens: int,
    ) -> CaseResult:
        try:
            kwargs: dict = {
                "modelId": model_id,
                "messages": case.messages,
                "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0},
            }
            if case.system:
                kwargs["system"] = [{"text": case.system}]

            resp = self._client.converse(**kwargs)
            usage = resp.get("usage", {})
            input_t = usage.get("inputTokens", 0)
            output_t = usage.get("outputTokens", 0)
            response_text = resp["output"]["message"]["content"][0]["text"]
            cost = _estimate(model_id, input_t, output_t)

            score, reasoning = _judge_response(
                self._client, self.judge_model_id, case, response_text
            )

            return CaseResult(
                case_id=case.case_id,
                model_id=model_id,
                response_text=response_text,
                score=score,
                passed=score >= min_score,
                judge_reasoning=reasoning,
                input_tokens=input_t,
                output_tokens=output_t,
                estimated_cost_usd=cost,
            )

        except Exception as e:
            logger.error("Case %s failed: %s", case.case_id, e)
            return CaseResult(
                case_id=case.case_id,
                model_id=model_id,
                response_text="",
                score=0.0,
                passed=False,
                error=str(e),
            )
