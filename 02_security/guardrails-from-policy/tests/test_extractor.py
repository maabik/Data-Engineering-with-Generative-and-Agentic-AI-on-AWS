"""Tests for text extraction from various file formats."""

import textwrap
from pathlib import Path

import pytest

from guardrails_from_policy.extractor import extract_text

SAMPLE_POLICY = Path(__file__).parent.parent / "examples" / "sample_policy.txt"


class TestExtractText:
    def test_extract_txt(self):
        text = extract_text(SAMPLE_POLICY)
        assert len(text) > 100
        assert "ACME" in text

    def test_extract_txt_tmpfile(self, tmp_path):
        f = tmp_path / "policy.txt"
        f.write_text("Hello policy world.", encoding="utf-8")
        assert extract_text(f) == "Hello policy world."

    def test_extract_md(self, tmp_path):
        f = tmp_path / "policy.md"
        f.write_text("# Policy\n\nSome rules.", encoding="utf-8")
        text = extract_text(f)
        assert "Policy" in text

    def test_extract_unknown_ext_as_text(self, tmp_path):
        f = tmp_path / "policy.rst"
        f.write_text("RST content here.", encoding="utf-8")
        assert "RST content" in extract_text(f)

    def test_extract_pdf_missing_dep(self, tmp_path, monkeypatch):
        """If pypdf is not importable, raises ImportError with helpful message."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pypdf":
                raise ImportError("No module named 'pypdf'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")
        with pytest.raises(ImportError, match="pypdf"):
            extract_text(f)

    def test_extract_docx_missing_dep(self, tmp_path, monkeypatch):
        """If python-docx is not importable, raises ImportError with helpful message."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "docx":
                raise ImportError("No module named 'docx'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK")  # fake docx
        with pytest.raises(ImportError, match="python-docx"):
            extract_text(f)

    def test_sample_policy_contains_key_terms(self):
        text = extract_text(SAMPLE_POLICY)
        for term in ["RESTRICTED", "CONFIDENTIAL", "INTERNAL", "PUBLIC", "PII"]:
            assert term in text, f"Expected {term!r} in extracted text"
