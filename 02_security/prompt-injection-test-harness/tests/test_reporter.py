"""Tests for console and JUnit XML reporters."""

import io
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from harness.reporter import print_summary, write_json, write_junit
from harness.runner import RunResult, SuiteResult
from harness.detectors import DetectorResult


def _make_suite(passed=2, failed=1, errored=0) -> SuiteResult:
    results: list[RunResult] = []
    for i in range(passed):
        r = RunResult(
            payload_id=f"p-pass-{i}",
            category="direct_override",
            severity="high",
            technique="test",
            passed=True,
            detectors=[DetectorResult("substring", False, "present", True)],
            response="clean",
            duration_s=0.1,
        )
        results.append(r)
    for i in range(failed):
        r = RunResult(
            payload_id=f"p-fail-{i}",
            category="role_play_jailbreak",
            severity="high",
            technique="dan",
            passed=False,
            detectors=[DetectorResult("substring", True, "present", False, "found PWNED")],
            response="PWNED",
            duration_s=0.2,
        )
        results.append(r)
    for i in range(errored):
        r = RunResult(
            payload_id=f"p-err-{i}",
            category="tool_abuse",
            severity="medium",
            technique="ssrf",
            passed=False,
            detectors=[],
            response="",
            error="RuntimeError: timeout",
            duration_s=5.0,
        )
        results.append(r)
    return SuiteResult(
        target_name="test-target",
        total=passed + failed + errored,
        passed=passed,
        failed=failed,
        errored=errored,
        duration_s=1.0,
        results=results,
    )


class TestConsoleSummary:
    def test_prints_target_name(self):
        buf = io.StringIO()
        print_summary(_make_suite(), buf)
        assert "test-target" in buf.getvalue()

    def test_prints_counts(self):
        buf = io.StringIO()
        print_summary(_make_suite(passed=5, failed=2), buf)
        out = buf.getvalue()
        assert "Passed" in out
        assert "Failed" in out

    def test_lists_failures(self):
        buf = io.StringIO()
        print_summary(_make_suite(failed=1), buf)
        assert "p-fail-0" in buf.getvalue()

    def test_lists_errors(self):
        buf = io.StringIO()
        print_summary(_make_suite(errored=1), buf)
        assert "p-err-0" in buf.getvalue()

    def test_by_category_section(self):
        buf = io.StringIO()
        print_summary(_make_suite(), buf)
        out = buf.getvalue()
        assert "direct_override" in out
        assert "role_play_jailbreak" in out


class TestJsonOutput:
    def test_writes_json(self, tmp_path):
        path = tmp_path / "out.json"
        write_json(_make_suite(), path)
        import json
        data = json.loads(path.read_text())
        assert data["total"] == 3
        assert "results" in data

    def test_json_has_by_category(self, tmp_path):
        import json
        path = tmp_path / "out.json"
        write_json(_make_suite(), path)
        data = json.loads(path.read_text())
        assert "by_category" in data


class TestJUnitOutput:
    def test_writes_xml(self, tmp_path):
        path = tmp_path / "out.xml"
        write_junit(_make_suite(), path)
        assert path.exists()

    def test_valid_xml(self, tmp_path):
        path = tmp_path / "out.xml"
        write_junit(_make_suite(), path)
        ET.parse(path)  # must not raise

    def test_failure_element_present(self, tmp_path):
        path = tmp_path / "out.xml"
        write_junit(_make_suite(failed=1), path)
        tree = ET.parse(path)
        failures = tree.findall(".//failure")
        assert len(failures) == 1

    def test_error_element_present(self, tmp_path):
        path = tmp_path / "out.xml"
        write_junit(_make_suite(errored=1), path)
        tree = ET.parse(path)
        errors = tree.findall(".//error")
        assert len(errors) == 1

    def test_pass_has_no_failure_element(self, tmp_path):
        path = tmp_path / "out.xml"
        write_junit(_make_suite(passed=3, failed=0), path)
        tree = ET.parse(path)
        failures = tree.findall(".//failure")
        assert failures == []

    def test_counts_in_testsuite_attrs(self, tmp_path):
        path = tmp_path / "out.xml"
        write_junit(_make_suite(passed=2, failed=1), path)
        tree = ET.parse(path)
        ts = tree.find("testsuite")
        assert ts.attrib["tests"] == "3"
        assert ts.attrib["failures"] == "1"
