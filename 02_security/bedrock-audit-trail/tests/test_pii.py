"""Tests for lambda/transform/pii.py (mocked Comprehend + S3)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda", "transform"))

import json
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_comprehend(entities):
    """Return a fake Comprehend client that returns the given entities."""
    class FakeComprehend:
        def detect_pii_entities(self, Text, LanguageCode):
            return {"Entities": entities}
    return FakeComprehend()


def _make_s3():
    """Return a fake S3 client that captures put_object calls."""
    class FakeS3:
        def __init__(self):
            self.stored = {}
        def put_object(self, **kwargs):
            self.stored[kwargs["Key"]] = json.loads(kwargs["Body"])
        def get_paginator(self, op):
            class Pager:
                def paginate(self, **kw):
                    return []
            return Pager()
    return FakeS3()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDetectAndRedact:
    def test_no_pii(self, monkeypatch):
        import pii
        pii._comprehend = _make_comprehend([])
        pii._s3 = _make_s3()
        monkeypatch.setenv("TOKEN_BUCKET", "test-bucket")
        result = pii.detect_and_redact("Hello, world!", "req-1")
        assert result.detected is False
        assert result.entity_types == []
        assert result.redacted_text == "Hello, world!"

    def test_email_redacted(self, monkeypatch):
        import pii
        fake_s3 = _make_s3()
        pii._comprehend = _make_comprehend([
            {"BeginOffset": 7, "EndOffset": 26,
             "Type": "EMAIL", "Score": 0.99}
        ])
        pii._s3 = fake_s3
        monkeypatch.setenv("TOKEN_BUCKET", "test-bucket")
        monkeypatch.setenv("TOKEN_KEY_ARN", "arn:aws:kms:us-east-1:123:key/abc")

        text = "Email: user@example.com is here"
        result = pii.detect_and_redact(text, "req-email")
        assert result.detected is True
        assert "EMAIL" in result.entity_types
        assert "user@example.com" not in result.redacted_text
        assert "[EMAIL:" in result.redacted_text
        assert len(result.token_ids) == 1

    def test_multiple_pii_types(self, monkeypatch):
        import pii
        pii._comprehend = _make_comprehend([
            {"BeginOffset": 0, "EndOffset": 10, "Type": "NAME", "Score": 0.95},
            {"BeginOffset": 15, "EndOffset": 26, "Type": "SSN", "Score": 0.99},
        ])
        pii._s3 = _make_s3()
        monkeypatch.setenv("TOKEN_BUCKET", "test-bucket")
        monkeypatch.setenv("TOKEN_KEY_ARN", "arn:aws:kms:us-east-1:123:key/abc")

        result = pii.detect_and_redact("John Smith, 123-45-6789", "req-multi")
        assert result.detected is True
        assert set(result.entity_types) == {"NAME", "SSN"}
        assert len(result.token_ids) == 2

    def test_token_stored_in_s3(self, monkeypatch):
        import pii
        fake_s3 = _make_s3()
        pii._comprehend = _make_comprehend([
            {"BeginOffset": 0, "EndOffset": 5, "Type": "NAME", "Score": 0.9}
        ])
        pii._s3 = fake_s3
        monkeypatch.setenv("TOKEN_BUCKET", "test-bucket")
        monkeypatch.setenv("TOKEN_KEY_ARN", "arn:aws:kms:us-east-1:123:key/abc")

        result = pii.detect_and_redact("Alice is here", "req-tok")
        # Token record should be written to S3
        assert len(fake_s3.stored) == 1
        stored_record = list(fake_s3.stored.values())[0]
        token_id = result.token_ids[0]
        assert token_id in stored_record
        assert stored_record[token_id]["original"] == "Alice"

    def test_empty_text_returns_no_pii(self, monkeypatch):
        import pii
        pii._comprehend = _make_comprehend([])
        pii._s3 = _make_s3()
        result = pii.detect_and_redact("", "req-empty")
        assert result.detected is False
        assert result.redacted_text == ""

    def test_no_token_bucket_skips_s3(self, monkeypatch):
        import pii
        fake_s3 = _make_s3()
        pii._comprehend = _make_comprehend([
            {"BeginOffset": 0, "EndOffset": 5, "Type": "NAME", "Score": 0.9}
        ])
        pii._s3 = fake_s3
        monkeypatch.setenv("TOKEN_BUCKET", "")

        result = pii.detect_and_redact("Alice is here", "req-nobucket")
        assert result.detected is True
        # No S3 writes when TOKEN_BUCKET is empty
        assert len(fake_s3.stored) == 0

    def test_comprehend_error_propagates(self, monkeypatch):
        import pii

        class FailingComprehend:
            def detect_pii_entities(self, **kw):
                raise RuntimeError("Comprehend unavailable")

        pii._comprehend = FailingComprehend()
        with pytest.raises(RuntimeError, match="Comprehend unavailable"):
            pii.detect_and_redact("Some text", "req-fail")
