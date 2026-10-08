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

