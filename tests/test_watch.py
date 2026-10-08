"""Unit tests for watch command file state utilities."""

import time
from pathlib import Path
from cli.watch_cmd import get_raw_files_state, get_already_ingested, discover_wikis


def test_get_raw_files_state(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # Create dummy files
    f1 = raw_dir / "doc1.md"
    f1.write_text("Hello doc 1", encoding="utf-8")

    f2 = raw_dir / "doc2.txt"
    f2.write_text("Hello doc 2", encoding="utf-8")

    f3 = raw_dir / "doc3.pdf"
    f3.write_bytes(b"%PDF-1.4 dummy")

    ignored = raw_dir / ".hidden.md"
    ignored.write_text("Secret", encoding="utf-8")

    state = get_raw_files_state(raw_dir)
    assert "doc1.md" in state
    assert "doc2.txt" in state
    assert "doc3.pdf" in state
    assert ".hidden.md" not in state
    assert len(state) == 3


def test_get_already_ingested(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    sources_dir = wiki_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True)

    # Simulated compiled source page
    (sources_dir / "my-article.md").write_text("# My Article", encoding="utf-8")

    # Simulated log file
    log_file = wiki_dir / "wiki" / "log.md"
    log_file.write_text("""
## [2026-10-04] ingest | Paper
- Source: raw/paper.pdf
""", encoding="utf-8")

    ingested = get_already_ingested(wiki_dir)
    assert "my-article" in ingested
    assert "paper.pdf" in ingested


def test_discover_wikis_single_path(tmp_path: Path):
    wiki_dir = tmp_path / "my-wiki"
    (wiki_dir / "wiki").mkdir(parents=True)
    (wiki_dir / "AGENTS.md").write_text("# Agents", encoding="utf-8")

    discovered = discover_wikis(base_dir=None, wiki_path=wiki_dir)
    assert len(discovered) == 1
    assert discovered[0].name == "my-wiki"


def test_discover_wikis_base_dir(tmp_path: Path):
    base_dir = tmp_path / "wikis"
    base_dir.mkdir()

    w1 = base_dir / "wiki-a"
    (w1 / "wiki").mkdir(parents=True)
    (w1 / "AGENTS.md").write_text("# A", encoding="utf-8")

    w2 = base_dir / "wiki-b"
    (w2 / "wiki").mkdir(parents=True)
    (w2 / "wiki.yaml").write_text("domain_name: B", encoding="utf-8")

    # Non-wiki directory
    other = base_dir / "random-folder"
    other.mkdir()

    discovered = discover_wikis(base_dir=base_dir, wiki_path=None)
    names = [w.name for w in discovered]
    assert "wiki-a" in names
    assert "wiki-b" in names
    assert "random-folder" not in names
    assert len(discovered) == 2
