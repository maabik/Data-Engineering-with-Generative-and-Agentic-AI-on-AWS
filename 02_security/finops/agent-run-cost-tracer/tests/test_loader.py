"""Tests for stitcher/loader.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gzip
import json
from datetime import date
from unittest.mock import MagicMock, call

import pytest
from stitcher.loader import load_spans_from_s3, nightly_stitch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_s3_client(objects: dict[str, bytes]) -> MagicMock:
    """Fake S3 client whose list+get returns `objects` keyed by S3 key."""
    client = MagicMock()
    paginate_calls: list[dict] = []

    def paginate(Bucket, Prefix):
        paginate_calls.append({"Bucket": Bucket, "Prefix": Prefix})
        keys = [k for k in objects if k.startswith(Prefix)]
        return [{"Contents": [{"Key": k} for k in keys]}]

    pag = MagicMock()
    pag.paginate.side_effect = paginate
    client.get_paginator.return_value = pag

    def get_object(Bucket, Key):
        body = MagicMock()
        body.read.return_value = objects[Key]
        return {"Body": body}

    client.get_object.side_effect = get_object
    client._paginate_calls = paginate_calls
    return client


def _ndjson(records: list[dict]) -> bytes:
    return "\n".join(json.dumps(r) for r in records).encode()


def _gzipped_ndjson(records: list[dict]) -> bytes:
    return gzip.compress(_ndjson(records))


# ── load_spans_from_s3 ────────────────────────────────────────────────────────

class TestLoadSpansFromS3:
    def test_loads_plain_ndjson(self):
        span = {"run_id": "r1", "span_type": "model"}
        client = _make_s3_client({"prefix/2024/01/15/file.json": _ndjson([span])})
        spans = list(load_spans_from_s3("bucket", "prefix", date(2024, 1, 15), client))
        assert len(spans) == 1
        assert spans[0]["run_id"] == "r1"

    def test_loads_gzipped_ndjson(self):
        span = {"run_id": "r2", "span_type": "tool"}
        client = _make_s3_client({"prefix/2024/01/15/file.json.gz": _gzipped_ndjson([span])})
        spans = list(load_spans_from_s3("bucket", "prefix", date(2024, 1, 15), client))
        assert len(spans) == 1
        assert spans[0]["run_id"] == "r2"

    def test_date_prefix_construction(self):
        client = _make_s3_client({})
        list(load_spans_from_s3("bucket", "spans", date(2024, 3, 5), client))
        assert len(client._paginate_calls) == 1
        assert client._paginate_calls[0] == {"Bucket": "bucket", "Prefix": "spans/2024/03/05/"}

    def test_no_date_uses_prefix_as_is(self):
        client = _make_s3_client({})
        list(load_spans_from_s3("bucket", "spans/", None, client))
        assert len(client._paginate_calls) == 1
        assert client._paginate_calls[0] == {"Bucket": "bucket", "Prefix": "spans/"}

    def test_multiple_files_merged(self):
        spans1 = [{"run_id": "r1", "span_type": "model"}]
        spans2 = [{"run_id": "r2", "span_type": "tool"}]
        client = _make_s3_client({
            "p/2024/01/15/a.json": _ndjson(spans1),
            "p/2024/01/15/b.json": _ndjson(spans2),
        })
        spans = list(load_spans_from_s3("bucket", "p", date(2024, 1, 15), client))
        assert len(spans) == 2

    def test_empty_prefix_returns_nothing(self):
        client = _make_s3_client({})
        spans = list(load_spans_from_s3("bucket", "empty", date(2024, 1, 15), client))
        assert spans == []

    def test_skips_blank_lines(self):
        raw = b'{"run_id": "r1"}\n\n{"run_id": "r2"}\n'
        client = _make_s3_client({"p/2024/01/15/f.json": raw})
        spans = list(load_spans_from_s3("bucket", "p", date(2024, 1, 15), client))
        assert len(spans) == 2

    def test_skips_invalid_json(self):
        raw = b'{"run_id": "r1"}\nnot-json\n{"run_id": "r2"}\n'
        client = _make_s3_client({"p/2024/01/15/f.json": raw})
        spans = list(load_spans_from_s3("bucket", "p", date(2024, 1, 15), client))
        assert len(spans) == 2  # invalid line skipped


# ── nightly_stitch ────────────────────────────────────────────────────────────

class TestNightlyStitch:
    def _spans(self):
        return [
            {
                "run_id": "run-1", "trace_id": "run-1",
                "span_type": "model", "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
                "input_tokens": 500, "output_tokens": 200,
                "estimated_cost_usd": 1.5,
                "started_at": "2024-01-15T10:00:00Z",
                "ended_at": "2024-01-15T10:00:01Z",
                "user_id": "u1", "tenant_id": "acme",
            },
            {
                "run_id": "run-2", "trace_id": "run-2",
                "span_type": "tool", "tool_name": "web",
                "estimated_cost_usd": 0.0,
                "started_at": "2024-01-15T11:00:00Z",
                "ended_at": "2024-01-15T11:00:01Z",
                "user_id": "u2", "tenant_id": "acme",
            },
        ]

    def test_returns_run_count(self):
        spans = self._spans()
        s3 = _make_s3_client({"spans/2024/01/15/f.json": _ndjson(spans)})
        count = nightly_stitch("spans-bucket", "spans", date(2024, 1, 15),
                               "runs-bucket", "agent-runs/", s3)
        assert count == 2

    def test_writes_to_correct_key(self):
        spans = self._spans()
        s3 = _make_s3_client({"spans/2024/01/15/f.json": _ndjson(spans)})
        nightly_stitch("spans-bucket", "spans", date(2024, 1, 15),
                       "runs-bucket", "agent-runs/", s3)
        s3.put_object.assert_called_once()
        kwargs = s3.put_object.call_args[1]
        assert kwargs["Bucket"] == "runs-bucket"
        assert kwargs["Key"] == "agent-runs/date=2024-01-15/runs.ndjson"

    def test_output_is_valid_ndjson(self):
        spans = self._spans()
        s3 = _make_s3_client({"spans/2024/01/15/f.json": _ndjson(spans)})
        nightly_stitch("spans-bucket", "spans", date(2024, 1, 15),
                       "runs-bucket", "agent-runs/", s3)
        kwargs = s3.put_object.call_args[1]
        body = kwargs["Body"].decode("utf-8")
        records = [json.loads(line) for line in body.strip().splitlines()]
        assert len(records) == 2
        assert all("run_id" in r for r in records)

    def test_no_spans_skips_write(self):
        s3 = _make_s3_client({})
        count = nightly_stitch("spans-bucket", "spans", date(2024, 1, 15),
                               "runs-bucket", "agent-runs/", s3)
        assert count == 0
        s3.put_object.assert_not_called()
