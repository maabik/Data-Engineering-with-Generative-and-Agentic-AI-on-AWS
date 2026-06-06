"""Reporters render SuiteResult into console, JSON, or JUnit XML."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TextIO
from xml.dom import minidom

from .runner import SuiteResult


def print_summary(suite: SuiteResult, stream: TextIO, *, verbose: bool = False) -> None:
    print(f"\nTarget: {suite.target_name}", file=stream)
    print(f"Total:    {suite.total}", file=stream)
    print(f"Passed:   {suite.passed}  ({suite.pass_rate:.1%})", file=stream)
    print(f"Failed:   {suite.failed}  ({suite.failure_rate:.1%})", file=stream)
    print(f"Errored:  {suite.errored}", file=stream)
    print(f"Duration: {suite.duration_s:.2f}s", file=stream)

    print("\nBy category:", file=stream)
    by_cat = suite.by_category()
    name_w = max((len(k) for k in by_cat), default=10)
    for cat in sorted(by_cat):
        s = by_cat[cat]
        rate = s["passed"] / s["total"] if s["total"] else 0.0
        print(
            f"  {cat.ljust(name_w)}  pass={s['passed']:>3}  fail={s['failed']:>3}  "
            f"err={s['errored']:>2}  total={s['total']:>3}  ({rate:.1%})",
            file=stream,
        )

    failed = suite.failed_results()
    if failed:
        print(f"\nFailures ({len(failed)}):", file=stream)
        for r in failed:
            triggered = ", ".join(
                d.detector_type for d in r.detectors if d.fail_if == "present" and d.triggered
            )
            print(f"  [{r.severity:>6}] {r.payload_id}  ({r.category}/{r.technique})", file=stream)
            if triggered:
                print(f"         triggered: {triggered}", file=stream)
            if verbose and r.response:
                snippet = r.response[:160].replace("\n", " ")
                print(f"         response: {snippet}", file=stream)

    errored = [r for r in suite.results if r.error]
    if errored:
        print(f"\nErrors ({len(errored)}):", file=stream)
        for r in errored:
            print(f"  {r.payload_id}: {r.error}", file=stream)


def write_json(suite: SuiteResult, path: str | Path) -> None:
    Path(path).write_text(json.dumps(suite.to_dict(), indent=2), encoding="utf-8")


def write_junit(suite: SuiteResult, path: str | Path) -> None:
    """Emit JUnit XML so CI systems can surface failures natively."""
    testsuites = ET.Element("testsuites", {"name": "prompt-injection-harness"})
    testsuite = ET.SubElement(
        testsuites,
        "testsuite",
        {
            "name": suite.target_name,
            "tests": str(suite.total),
            "failures": str(suite.failed),
            "errors": str(suite.errored),
            "time": f"{suite.duration_s:.3f}",
        },
    )
    for r in suite.results:
        case = ET.SubElement(
            testsuite,
            "testcase",
            {
                "classname": f"{suite.target_name}.{r.category}",
                "name": r.payload_id,
                "time": f"{r.duration_s:.3f}",
            },
        )
        if r.error:
            err = ET.SubElement(case, "error", {"type": "invoke_error", "message": r.error})
            err.text = r.error
        elif not r.passed:
            triggered = [
                d for d in r.detectors if d.fail_if == "present" and d.triggered
            ] + [d for d in r.detectors if d.fail_if == "absent" and not d.triggered]
            msg = "; ".join(
                f"{d.detector_type}: {d.evidence or 'triggered'}" for d in triggered
            ) or "detector failure"
            fail = ET.SubElement(
                case,
                "failure",
                {"type": f"injection_{r.category}", "message": msg[:240]},
            )
            fail.text = (
                f"severity={r.severity} technique={r.technique}\n\n"
                f"payload: {_truncate(_load_user_input(r), 400)}\n\n"
                f"response: {_truncate(r.response, 800)}"
            )

    pretty = minidom.parseString(ET.tostring(testsuites)).toprettyxml(indent="  ")
    Path(path).write_text(pretty, encoding="utf-8")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _load_user_input(r) -> str:
    # RunResult doesn't carry user_input; leave a placeholder for JUnit messages.
    return f"[{r.payload_id}]"
