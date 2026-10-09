"""Unit tests for document chunking and splitting strategies."""

from lib.splitter import split_document


def test_split_document_short_text():
    short_text = "This is a short note that easily fits in one chunk."
    chunks = split_document(short_text, strategy="headers", max_tokens=1000)
    assert len(chunks) == 1
    assert chunks[0].index == 1
    assert chunks[0].total == 1
    assert chunks[0].content == short_text


def test_split_document_by_headers():
    doc = """# Introduction
The transformer is a neural network architecture.

## Model Architecture
Most competitive neural sequence transduction models have an encoder-decoder structure.

### Attention
An attention function can be described as mapping a query and a set of key-value pairs to an output.

## Training
We trained on the standard WMT 2014 English-German dataset.
"""
    # Force small max tokens to trigger header-based splitting
    chunks = split_document(doc, strategy="headers", max_tokens=25)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.token_count > 0
        assert chunk.total == len(chunks)


def test_split_document_by_pages():
    paged_doc = """First page content with intro.
<!-- pagebreak -->
Second page content with benchmarks.
<!-- pagebreak -->
Third page content with conclusion.
"""
    chunks = split_document(paged_doc, strategy="pages", max_tokens=5000)
    assert len(chunks) == 3
    assert "First page" in chunks[0].content
    assert "Second page" in chunks[1].content
    assert "Third page" in chunks[2].content


def test_split_document_by_pages_horizontal_rules():
    doc = """Page 1 content about models.
-----
Page 2 content with tables and metrics.
-----
Page 3 summary.
"""
    chunks = split_document(doc, strategy="pages", max_tokens=5000)
    assert len(chunks) == 3
    assert "Page 1" in chunks[0].content
    assert "Page 2" in chunks[1].content
    assert "Page 3" in chunks[2].content


def test_split_document_fixed_size():
    """Test fixed_size sliding window chunking."""
    text = "word " * 500
    chunks = split_document(text, strategy="fixed_size", max_tokens=100, overlap_tokens=20)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.token_count > 0


def test_prune_content():
    """Test pre-LLM context pruning removes HTML, TOC, and simplifies URLs."""
    from lib.splitter import _prune_content
    raw = "<div>Hello</div>\nhttps://example.com/very/long/tracking/url?id=123\n\n## Table of Contents\n- Link 1\n- Link 2\n\nActual text."
    pruned = _prune_content(raw)
    assert "<div>" not in pruned
    assert "<URL>" in pruned
    assert "## Table of Contents" not in pruned
    assert "Actual text." in pruned


def test_split_document_empty():
    """Test splitting empty string returns empty list."""
    assert split_document("") == []
    assert split_document("   \n\t  ") == []


def test_split_semantic_fallback():
    """Test semantic chunking with mock provider."""
    class MockSemanticProvider:
        def embed(self, text):
            return [1.0, 0.0] if "1" in text else [0.0, 1.0]

    text = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
    chunks = split_document(text, strategy="semantic", max_tokens=50, provider=MockSemanticProvider())
    assert len(chunks) >= 1


