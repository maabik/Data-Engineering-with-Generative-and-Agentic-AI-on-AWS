from pathlib import Path

from s3vectors_rag.loaders import load_file, iter_files


def test_load_markdown_splits_on_headings(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text(
        "# Title One\nFirst paragraph.\n\n## Subsection\nMore text.\n\n# Title Two\nLast bit.\n",
        encoding="utf-8",
    )
    sections = load_file(md)
    locators = [s.locator for s in sections]
    assert "section=Title One" in locators
    assert "section=Subsection" in locators
    assert "section=Title Two" in locators
    assert all(s.text.strip() for s in sections)


def test_load_markdown_no_headings(tmp_path: Path):
    md = tmp_path / "flat.md"
    md.write_text("just some prose without any heading\nanother line", encoding="utf-8")
    sections = load_file(md)
    assert len(sections) == 1
    assert sections[0].locator in {"section=preamble", "section=root"}


def test_load_txt(tmp_path: Path):
    f = tmp_path / "notes.txt"
    f.write_text("hello world", encoding="utf-8")
    sections = load_file(f)
    assert len(sections) == 1
    assert sections[0].text == "hello world"


def test_iter_files_filters_by_extension(tmp_path: Path):
    (tmp_path / "a.md").write_text("# h\nx", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    (tmp_path / "ignore.png").write_bytes(b"\x89PNG")
    found = sorted(p.name for p in iter_files(tmp_path))
    assert found == ["a.md", "b.txt"]
