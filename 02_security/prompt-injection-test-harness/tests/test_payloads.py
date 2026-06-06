"""Tests for payload loading, filtering, and schema validation."""

import json
import textwrap
from pathlib import Path

import pytest

from harness.payloads import Payload, load_payloads

PAYLOADS_DIR = Path(__file__).parent.parent / "payloads"


class TestPayloadLoading:
    def test_loads_all_payloads(self):
        payloads = load_payloads([PAYLOADS_DIR])
        # Corpus must contain at least 200 payloads.
        assert len(payloads) >= 200

    def test_no_duplicate_ids(self):
        payloads = load_payloads([PAYLOADS_DIR])
        ids = [p.id for p in payloads]
        assert len(ids) == len(set(ids)), "Duplicate payload IDs detected"

    def test_all_required_fields_present(self):
        payloads = load_payloads([PAYLOADS_DIR])
        for p in payloads:
            assert p.id
            assert p.category
            assert p.severity
            assert p.user_input
            assert p.detectors

    def test_valid_severities(self):
        valid = {"none", "low", "medium", "high", "critical"}
        payloads = load_payloads([PAYLOADS_DIR])
        bad = [p.id for p in payloads if p.severity not in valid]
        assert not bad, f"Invalid severities on: {bad}"

    def test_valid_detector_types(self):
        valid = {"substring", "regex", "canary", "refusal"}
        payloads = load_payloads([PAYLOADS_DIR])
        for p in payloads:
            for d in p.detectors:
                assert d["type"] in valid, f"{p.id}: unknown detector type {d['type']!r}"

    def test_benign_payloads_use_refusal_detector(self):
        benign = load_payloads([PAYLOADS_DIR], categories=["benign_baseline"])
        assert benign, "No benign_baseline payloads found"
        for p in benign:
            types = {d["type"] for d in p.detectors}
            assert "refusal" in types, f"{p.id}: benign payload should have a 'refusal' detector"

    def test_filter_by_category(self):
        payloads = load_payloads([PAYLOADS_DIR], categories=["direct_override"])
        assert all(p.category == "direct_override" for p in payloads)

    def test_filter_by_severity(self):
        payloads = load_payloads([PAYLOADS_DIR], severities=["high"])
        assert all(p.severity == "high" for p in payloads)

    def test_filter_by_tag(self):
        payloads = load_payloads([PAYLOADS_DIR], tags=["exfil"])
        assert all("exfil" in p.tags for p in payloads)
        assert len(payloads) > 0

    def test_filter_by_id(self):
        payloads = load_payloads([PAYLOADS_DIR], ids=["direct-override-001"])
        assert len(payloads) == 1
        assert payloads[0].id == "direct-override-001"

    def test_load_single_file(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text(
            json.dumps({
                "id": "test-001",
                "category": "test",
                "severity": "low",
                "technique": "t",
                "user_input": "hello",
                "detectors": [{"type": "substring", "value": "LEAK", "fail_if": "present"}],
            }) + "\n"
        )
        payloads = load_payloads([f])
        assert len(payloads) == 1

    def test_missing_required_field_raises(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text(json.dumps({"id": "x", "category": "t"}) + "\n")
        with pytest.raises(ValueError, match="missing required field"):
            load_payloads([f])

    def test_invalid_severity_raises(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text(
            json.dumps({
                "id": "x-001",
                "category": "t",
                "severity": "extreme",
                "user_input": "hi",
                "detectors": [{"type": "substring", "value": "X", "fail_if": "present"}],
            }) + "\n"
        )
        with pytest.raises(ValueError, match="invalid severity"):
            load_payloads([f])

    def test_duplicate_ids_raise(self, tmp_path):
        line = json.dumps({
            "id": "dup-001",
            "category": "t",
            "severity": "low",
            "user_input": "hi",
            "detectors": [{"type": "substring", "value": "X", "fail_if": "present"}],
        })
        f = tmp_path / "a.jsonl"
        f.write_text(line + "\n" + line + "\n")
        with pytest.raises(ValueError, match="duplicate payload id"):
            load_payloads([f])

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        f = tmp_path / "a.jsonl"
        f.write_text(
            textwrap.dedent("""\
                # this is a comment

                {"id":"c-001","category":"t","severity":"low","user_input":"hi","detectors":[{"type":"substring","value":"X","fail_if":"present"}]}
            """)
        )
        payloads = load_payloads([f])
        assert len(payloads) == 1

    def test_nonexistent_path_raises(self):
        with pytest.raises(FileNotFoundError):
            load_payloads(["/nonexistent/path/to/payloads"])
