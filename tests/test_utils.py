"""Unit tests for utility functions."""

from lib.utils import (
    extract_source_citations,
    extract_wikilinks,
    parse_frontmatter,
    read_source_file,
    render_frontmatter,
    slugify,
    today_str,
)


def test_slugify():
    assert slugify("Transformer Architecture & Attention!") == "transformer-architecture-attention"
    assert slugify("GPT-4o vs Claude 3.5") == "gpt-4o-vs-claude-3-5"
    assert slugify("") == "untitled"


def test_wikilinks_extraction():
    text = "We compare [[Transformer Architecture]] and [[RLHF|Reinforcement Learning]] with [[DeepSeek]]."
    links = extract_wikilinks(text)
    assert links == ["Transformer Architecture", "RLHF", "DeepSeek"]


def test_source_citations_extraction():
    text = "Achieved 95% accuracy [source: paper.pdf, §Table 2] using 8 GPUs [source: hardware.md, §Setup]."
    citations = extract_source_citations(text)
    assert citations == ["paper.pdf, §Table 2", "hardware.md, §Setup"]


def test_frontmatter_parsing_and_rendering():
    raw_md = """---
title: "Self-Attention Mechanism"
type: concept
tags:
  - deep-learning
  - attention
confidence: high
source_count: 2
---

## Definition
Self-attention relates different positions of a single sequence.
"""
    fm, body = parse_frontmatter(raw_md)
    assert fm["title"] == "Self-Attention Mechanism"
    assert fm["type"] == "concept"
    assert "deep-learning" in fm["tags"]
    assert fm["confidence"] == "high"
    assert "## Definition" in body

    # Re-render
    re_rendered = render_frontmatter(fm, body)
    fm2, body2 = parse_frontmatter(re_rendered)
    assert fm2["title"] == fm["title"]
    assert "## Definition" in body2

def test_frontmatter_list_formatting():
    fm = {"title": "Test", "sources": ["[[a]]", "[[b]]"]}
    body = "body"
    rendered = render_frontmatter(fm, body)
    assert "sources:\n  - '[[a]]'\n  - '[[b]]'" in rendered


def test_read_source_file_text_and_md(tmp_path):
    txt_file = tmp_path / "sample.txt"
    txt_file.write_text("Hello from text file", encoding="utf-8")
    assert read_source_file(txt_file) == "Hello from text file"

    md_file = tmp_path / "sample.md"
    md_file.write_text("# Hello Markdown", encoding="utf-8")
    assert read_source_file(md_file) == "# Hello Markdown"


def test_read_source_file_pdf(tmp_path):
    import pymupdf
    pdf_file = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Sample PDF Ingestion Text")
    doc.save(str(pdf_file))
    doc.close()

    result = read_source_file(pdf_file)
    assert "Sample PDF Ingestion Text" in result


def test_build_link_graph(tmp_path):
    """Test building link graph from markdown notes."""
    from lib.utils import build_link_graph

    wiki_sub = tmp_path / "wiki"
    wiki_sub.mkdir()
    (wiki_sub / "a.md").write_text("Links to [[b]] and [[c]].", encoding="utf-8")
    (wiki_sub / "b.md").write_text("Links to [[c]].", encoding="utf-8")
    (wiki_sub / "c.md").write_text("No outgoing links.", encoding="utf-8")

    graph = build_link_graph(tmp_path)
    assert "b" in graph.get("a", set())
    assert "c" in graph.get("a", set())
    assert "c" in graph.get("b", set())
    assert len(graph.get("c", set())) == 0


def test_resolve_vault_path(tmp_path):
    """Test resolving wiki vault paths directly and by relative name."""
    from lib.utils import resolve_vault_path

    vault_dir = tmp_path / "my-vault"
    (vault_dir / "wiki").mkdir(parents=True)

    # 1. Direct path
    assert resolve_vault_path(str(vault_dir)) == vault_dir.resolve()

    # 2. Non-existent path returns None
    assert resolve_vault_path(str(tmp_path / "nonexistent")) is None


def test_count_tokens_approx():
    """Test token estimation."""
    from lib.utils import count_tokens_approx

    tokens = count_tokens_approx("Hello world, this is a token test sentence.")
    assert tokens > 0
    assert count_tokens_approx("") == 0


