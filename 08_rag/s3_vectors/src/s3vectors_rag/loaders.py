from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from .logging_setup import get_logger

log = get_logger("loaders")


@dataclass
class LoadedSection:
    """A piece of source text plus locator metadata (e.g., page or section title)."""
    text: str
    locator: str  # e.g., "page=3" or "section=Intro"


SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}


def iter_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES:
                yield p


def load_file(path: Path) -> List[LoadedSection]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix in {".md", ".markdown"}:
        return _load_markdown(path)
    if suffix == ".txt":
        return [LoadedSection(text=path.read_text(encoding="utf-8", errors="replace"), locator="file")]
    raise ValueError(f"Unsupported file type: {path.suffix}")


def _load_pdf(path: Path) -> List[LoadedSection]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as e:  # pragma: no cover - exercised in environments without pypdf
        raise RuntimeError(
            "pypdf is required to load PDFs. Install with `pip install pypdf`."
        ) from e

    reader = PdfReader(str(path))
    sections: List[LoadedSection] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception as e:
            log.warning("page %d of %s failed to extract: %s", page_num, path.name, e)
            txt = ""
        txt = txt.strip()
        if txt:
            sections.append(LoadedSection(text=txt, locator=f"page={page_num}"))
    return sections


def _load_markdown(path: Path) -> List[LoadedSection]:
    """
    Split a markdown file by top-level (#) and second-level (##) headings.
    Each section retains its heading as the locator so retrieval can be
    cited by section title rather than line number.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    sections: List[LoadedSection] = []
    current_title = "preamble"
    current_buf: List[str] = []

    def flush():
        body = "\n".join(current_buf).strip()
        if body:
            sections.append(LoadedSection(text=body, locator=f"section={current_title}"))

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("# ") or stripped.startswith("## "):
            flush()
            current_title = stripped.lstrip("#").strip() or "untitled"
            current_buf = []
        else:
            current_buf.append(line)
    flush()

    if not sections:
        # No headings — treat whole file as one section.
        body = raw.strip()
        if body:
            sections.append(LoadedSection(text=body, locator="section=root"))
    return sections
