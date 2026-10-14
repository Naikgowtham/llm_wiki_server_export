"""Unit tests for advanced PDF processing: noise filtering, vision transcription, and table-aware chunking."""

import pytest
import pymupdf
from pathlib import Path
from unittest.mock import MagicMock

from lib.pdf_processor import (
    filter_extracted_images,
    cleanup_markdown_image_tags,
    enrich_markdown_with_vision,
    process_pdf_source,
)
from lib.splitter import (
    is_markdown_table,
    split_table_with_header_continuity,
    split_document,
)


def test_filter_extracted_images(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()

    # 1. Tiny icon (32x32) -> Noise
    doc_small = pymupdf.open()
    page_small = doc_small.new_page(width=32, height=32)
    page_small.draw_rect((0, 0, 32, 32))
    pix_small = page_small.get_pixmap()
    pix_small.save(str(assets_dir / "icon.png"))
    doc_small.close()

    # 2. Extreme divider line (600x10, aspect ratio 60:1) -> Noise
    doc_line = pymupdf.open()
    page_line = doc_line.new_page(width=600, height=10)
    page_line.draw_rect((0, 0, 600, 10))
    pix_line = page_line.get_pixmap()
    pix_line.save(str(assets_dir / "divider.png"))
    doc_line.close()

    # 3. Valid architecture diagram (400x300, 120,000 pixels) -> Retain
    doc_valid = pymupdf.open()
    page_valid = doc_valid.new_page(width=400, height=300)
    page_valid.draw_rect((10, 10, 390, 290))
    pix_valid = page_valid.get_pixmap()
    pix_valid.save(str(assets_dir / "architecture.png"))
    doc_valid.close()

    retained = filter_extracted_images(assets_dir, min_dim=120)

    assert "architecture.png" in retained
    assert "icon.png" not in retained
    assert "divider.png" not in retained

    # Check files on disk
    assert (assets_dir / "architecture.png").exists()
    assert not (assets_dir / "icon.png").exists()
    assert not (assets_dir / "divider.png").exists()


def test_cleanup_markdown_image_tags():
    raw_md = (
        "# System Architecture\n\n"
        "Here is the main diagram:\n"
        "![](assets/paper/architecture.png)\n\n"
        "And a decorative bullet:\n"
        "![](assets/paper/icon.png)\n\n"
        "Summary text."
    )
    valid_images = {"architecture.png"}
    cleaned = cleanup_markdown_image_tags(raw_md, valid_images, "paper")

    assert "assets/paper/architecture.png" in cleaned
    assert "icon.png" not in cleaned
    assert "Summary text." in cleaned


def test_is_markdown_table():
    standard_table = (
        "| Name | Role | Score |\n"
        "| :--- | :--- | ---: |\n"
        "| Alice | Lead | 95 |\n"
        "| Bob | Dev | 88 |"
    )
    assert is_markdown_table(standard_table) is True

    captioned_table = (
        "**Table 1: Benchmark Results**\n"
        "| Model | Latency | Accuracy |\n"
        "|---|---|---|\n"
        "| Flash | 45ms | 91.2% |"
    )
    assert is_markdown_table(captioned_table) is True

    plain_text = "This is simply a regular paragraph describing metrics."
    assert is_markdown_table(plain_text) is False


def test_split_table_with_header_continuity():
    header = "| Metric | Baseline | Target | Achieved |"
    delim = "|---|---|---|---|"
    rows = [f"| Metric_{i} | {i*10}ms | {i*5}ms | {i*4}ms |" for i in range(60)]
    full_table = "\n".join([header, delim] + rows)

    # Split with low token budget (e.g. 300 tokens)
    chunks = split_table_with_header_continuity(full_table, max_tokens=300)

    assert len(chunks) > 1
    for idx, chunk in enumerate(chunks):
        lines = chunk.strip().splitlines()
        # Every chunk MUST start with the exact table header and delimiter
        assert lines[0] == header, f"Chunk {idx} missing header row"
        assert lines[1] == delim, f"Chunk {idx} missing delimiter row"
        assert len(lines) > 2, f"Chunk {idx} has no data rows"


def test_table_aware_chunking_via_split_document():
    doc_text = (
        "# Architecture Overview\n\n"
        "The following table summarizes all hardware components:\n\n"
        "| Component | Quantity | Spec |\n"
        "|---|---|---|\n"
        + "\n".join([f"| Node_{i} | {i} | DDR5 {i*8}GB |" for i in range(50)])
        + "\n\n## Next Steps\n\nFinal conclusions."
    )

    chunks = split_document(doc_text, strategy="headers", max_tokens=300)
    assert len(chunks) > 1

    # Find chunks containing table rows
    table_chunks = [c for c in chunks if "| Node_" in c.content]
    assert len(table_chunks) > 1

    for tc in table_chunks:
        lines = tc.content.strip().splitlines()
        assert any("| Component |" in line for line in lines), "Table sub-chunk missing table header"
        assert any("|---|" in line for line in lines), "Table sub-chunk missing delimiter row"


import asyncio


def test_enrich_markdown_with_vision_success(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    img_file = assets_dir / "diagram.png"
    img_file.write_bytes(b"fake_image_bytes")

    markdown_text = (
        "# Performance\n\n"
        "![Figure: diagram.png](assets/paper/diagram.png)\n\n"
        "Discussion follows."
    )

    mock_provider = MagicMock()
    mock_provider.call_vision.return_value = (
        "### Visual Analysis: diagram.png\n"
        "- **Type:** Data Chart\n"
        "- **Overview:** Shows latency improvement across benchmarks.\n\n"
        "| Batch | Latency |\n"
        "|---|---|\n"
        "| 1 | 12ms |"
    )

    enriched = asyncio.run(enrich_markdown_with_vision(
        markdown_text=markdown_text,
        assets_dir=assets_dir,
        valid_image_names={"diagram.png"},
        source_filename="paper.pdf",
        doc_slug="paper",
        provider=mock_provider,
    ))

    assert "[!FIGURE: diagram.png]" in enriched
    assert "Shows latency improvement" in enriched
    assert "| Batch | Latency |" in enriched


def test_enrich_markdown_with_vision_soft_fallback_on_exhaustion(tmp_path):
    """Asserts that when vision models are exhausted (call_vision returns None), ingestion does NOT crash."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    img_file = assets_dir / "diagram.png"
    img_file.write_bytes(b"fake_image_bytes")

    markdown_text = (
        "# Overview\n\n"
        "![](assets/paper/diagram.png)\n\n"
        "Regular content."
    )

    mock_provider = MagicMock()
    # Simulate vision quota exhaustion returning None
    mock_provider.call_vision.return_value = None

    enriched = asyncio.run(enrich_markdown_with_vision(
        markdown_text=markdown_text,
        assets_dir=assets_dir,
        valid_image_names={"diagram.png"},
        source_filename="paper.pdf",
        doc_slug="paper",
        provider=mock_provider,
    ))

    # Should retain clean image reference without crashing or throwing
    assert "![Figure: diagram.png](assets/paper/diagram.png)" in enriched
    assert "Regular content." in enriched
    assert "[!FIGURE:" not in enriched


def test_process_pdf_source_end_to_end(tmp_path):
    # Create synthetic PDF
    pdf_path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    page.insert_text((50, 50), "# Research Paper Title\n\nThis is the introductory section.")
    
    # Draw a table with grid
    page.insert_text((50, 120), "| Method | Score |\n|---|---|\n| Model A | 98.4 |\n| Model B | 94.1 |")
    
    # Draw a rectangle graphic (will be rendered/extracted)
    page.draw_rect((50, 200, 300, 350), fill=(0.2, 0.4, 0.8))
    doc.save(str(pdf_path))
    doc.close()

    vault_dir = tmp_path / "test-vault"
    vault_dir.mkdir()

    result = asyncio.run(process_pdf_source(
        pdf_path=pdf_path,
        wiki_dir=vault_dir,
        provider=None, # Text-only fallback
    ))

    assert "Research Paper Title" in result
    assert "| Method | Score |" in result
    assert "| Model A | 98.4 |" in result
