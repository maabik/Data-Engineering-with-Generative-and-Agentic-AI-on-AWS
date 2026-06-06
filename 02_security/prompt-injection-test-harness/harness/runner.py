"""Run a suite of payloads against a Target and collect results."""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .detectors import DetectorResult, evaluate_detectors
from .payloads import Payload
from .targets import Target


@dataclass
class RunResult:
    payload_id: str
    category: str
    severity: str
    technique: str
    passed: bool
    detectors: list[DetectorResult]
    response: str = ""
    error: str = ""
    duration_s: float = 0.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["detectors"] = [asdict(x) for x in self.detectors]
        return d


@dataclass
class SuiteResult:
    target_name: str
    total: int
    passed: int
    failed: int
    errored: int
    duration_s: float
    results: list[RunResult]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def failure_rate(self) -> float:
        return self.failed / self.total if self.total else 0.0

    def by_category(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for r in self.results:
            s = stats.setdefault(r.category, {"total": 0, "passed": 0, "failed": 0, "errored": 0})
            s["total"] += 1
            if r.error:
                s["errored"] += 1
            elif r.passed:
                s["passed"] += 1
            else:
                s["failed"] += 1
        return stats

    def failed_results(self) -> list[RunResult]:
        return [r for r in self.results if not r.passed and not r.error]

    def to_dict(self) -> dict:
        return {
            "target_name": self.target_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "duration_s": self.duration_s,
            "pass_rate": self.pass_rate,
            "failure_rate": self.failure_rate,
            "by_category": self.by_category(),
            "results": [r.to_dict() for r in self.results],
        }


def run_suite(
    target: Target,
    payloads: list[Payload],
    *,
    max_workers: int = 4,
    on_result: Callable[[RunResult], None] | None = None,
) -> SuiteResult:
    """Execute every payload against `target`, collecting RunResults.

    `max_workers` sets thread-pool concurrency. Set to 1 for sequential
    execution. `on_result` is called after each payload completes — useful for
    progress reporting.
    """
    start = time.perf_counter()
    results: list[RunResult] = []

    def _run_one(payload: Payload) -> RunResult:
        return _invoke_and_evaluate(target, payload)

    if max_workers <= 1:
        for p in payloads:
            r = _run_one(p)
            results.append(r)
            if on_result:
                on_result(r)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_run_one, p): p for p in payloads}
            for fut in concurrent.futures.as_completed(futures):
                r = fut.result()
                results.append(r)
                if on_result:
                    on_result(r)

    # Re-sort to match input order (ThreadPool completes out of order).
    id_order = {p.id: i for i, p in enumerate(payloads)}
    results.sort(key=lambda r: id_order.get(r.payload_id, 0))

    passed = sum(1 for r in results if r.passed and not r.error)
    failed = sum(1 for r in results if not r.passed and not r.error)
    errored = sum(1 for r in results if r.error)

    return SuiteResult(
        target_name=getattr(target, "name", target.__class__.__name__),
        total=len(results),
        passed=passed,
        failed=failed,
        errored=errored,
        duration_s=time.perf_counter() - start,
        results=results,
    )


def _invoke_and_evaluate(target: Target, payload: Payload) -> RunResult:
    t0 = time.perf_counter()
    canaries = list(getattr(target, "canaries", []) or [])
    try:
        response = target.invoke(payload.user_input)
    except Exception as e:
        return RunResult(
            payload_id=payload.id,
            category=payload.category,
            severity=payload.severity,
            technique=payload.technique,
            passed=False,
            detectors=[],
            response="",
            error=f"{type(e).__name__}: {e}",
            duration_s=time.perf_counter() - t0,
            tags=list(payload.tags),
        )

    det_results = evaluate_detectors(response, payload.detectors, canaries=canaries)
    passed = all(d.passed for d in det_results)

    return RunResult(
        payload_id=payload.id,
        category=payload.category,
        severity=payload.severity,
        technique=payload.technique,
        passed=passed,
        detectors=det_results,
        response=response,
        duration_s=time.perf_counter() - t0,
        tags=list(payload.tags),
    )
