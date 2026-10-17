"""Document chunking and splitting strategies for large sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Any

from lib.utils import count_tokens_approx


@dataclass
class Chunk:
    content: str
    index: int
    total: int
    header_path: List[str] = field(default_factory=list)
    token_count: int = 0


def is_markdown_table(text: str) -> bool:
    """Check if a text block represents a GitHub Flavored Markdown (GFM) table."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    # Case 1: Standard table starting at line 0
    if lines[0].startswith("|") and lines[0].endswith("|"):
        if lines[1].startswith("|") and all(c in "|:- \t" for c in lines[1]) and "-" in lines[1]:
            return True
    # Case 2: Optional 1-line caption immediately before table
    if len(lines) >= 3 and lines[1].startswith("|") and lines[1].endswith("|"):
        if lines[2].startswith("|") and all(c in "|:- \t" for c in lines[2]) and "-" in lines[2]:
            return True
    return False


def split_table_with_header_continuity(table_text: str, max_tokens: int) -> List[str]:
    """Split an oversized markdown table into row-aligned chunks while repeating headers on each sub-chunk.

    Guarantees that downstream LLM extractors never receive headless rows.
    """
    lines = [line.strip() for line in table_text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return [table_text]

    if lines[0].startswith("|"):
        caption = ""
        header = lines[0]
        delimiter = lines[1]
        data_rows = lines[2:]
    else:
        caption = lines[0]
        header = lines[1]
        delimiter = lines[2]
        data_rows = lines[3:]

    header_prefix = f"{caption}\n{header}\n{delimiter}".strip() if caption else f"{header}\n{delimiter}"
    chunks: List[str] = []
    current_rows: List[str] = []

    for row in data_rows:
        candidate = (
            (header_prefix + "\n" + "\n".join(current_rows) + "\n" + row)
            if current_rows
            else (header_prefix + "\n" + row)
        )
        if count_tokens_approx(candidate) > max_tokens and current_rows:
            chunk_body = header_prefix + "\n" + "\n".join(current_rows)
            chunks.append(chunk_body)
            current_rows = [row]
        else:
            current_rows.append(row)

    if current_rows:
        chunk_body = header_prefix + "\n" + "\n".join(current_rows)
        chunks.append(chunk_body)

    return chunks


def split_code_fence_safely(code_block: str, max_tokens: int) -> List[str]:
    """Split an oversized fenced code block across multiple sub-chunks,
    ensuring each sub-chunk has matching opening and closing code fences."""
    lines = code_block.splitlines()
    if len(lines) < 3:
        return [code_block]

    first_line = lines[0]
    fence_match = re.match(r"^(\s*)(`{3,}|~{3,})(.*)$", first_line)
    if not fence_match:
        return [code_block]

    indent = fence_match.group(1)
    fence_chars = fence_match.group(2)
    lang = fence_match.group(3)
    opening_fence = f"{indent}{fence_chars}{lang}"
    closing_fence = f"{indent}{fence_chars}"

    # Extract body lines, excluding first and last fence lines
    body_lines = lines[1:-1] if lines[-1].strip().startswith(fence_chars[:3]) else lines[1:]
    chunks: List[str] = []
    current_lines: List[str] = []

    for line in body_lines:
        cand_body = current_lines + [line]
        cand_text = opening_fence + "\n" + "\n".join(cand_body) + "\n" + closing_fence
        if count_tokens_approx(cand_text) > max_tokens and current_lines:
            chunk_text = opening_fence + "\n" + "\n".join(current_lines) + "\n" + closing_fence
            chunks.append(chunk_text)
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        chunk_text = opening_fence + "\n" + "\n".join(current_lines) + "\n" + closing_fence
        chunks.append(chunk_text)

    return chunks or [code_block]


def _split_markdown_into_blocks(text: str) -> List[str]:
    """Split markdown text into atomic blocks (code fences, tables, and paragraphs).
    Ensures internal newlines within code blocks and tables do not sever them."""
    lines = text.splitlines(keepends=True)
    blocks: List[str] = []
    current_block: List[str] = []
    in_code_fence = False
    fence_marker = ""
    in_table = False

    for line in lines:
        stripped = line.strip()
        fence_match = re.match(r"^(\s*)(`{3,}|~{3,})", line)

        if fence_match:
            marker = fence_match.group(2)
            if not in_code_fence:
                if current_block:
                    blocks.append("".join(current_block).strip())
                    current_block = []
                in_code_fence = True
                fence_marker = marker
                in_table = False
                current_block.append(line)
                continue
            elif stripped.startswith(fence_marker[:3]):
                current_block.append(line)
                blocks.append("".join(current_block).strip())
                current_block = []
                in_code_fence = False
                fence_marker = ""
                continue

        if in_code_fence:
            current_block.append(line)
            continue

        # Check for table row
        is_tbl_line = stripped.startswith("|") and stripped.endswith("|")
        if is_tbl_line:
            if not in_table:
                if current_block:
                    blocks.append("".join(current_block).strip())
                    current_block = []
                in_table = True
            current_block.append(line)
            continue
        elif in_table:
            if current_block:
                blocks.append("".join(current_block).strip())
                current_block = []
            in_table = False

        if stripped == "":
            if current_block:
                blocks.append("".join(current_block).strip())
                current_block = []
        else:
            current_block.append(line)

    if current_block:
        b = "".join(current_block).strip()
        if b:
            blocks.append(b)

    return [b for b in blocks if b]


def _split_by_headers(text: str, max_tokens: int) -> List[tuple[str, List[str]]]:
    """Split markdown text into sections demarcated by headers (#, ##, ###).

    Returns list of tuples: (section_text, header_hierarchy).
    """
    lines = text.splitlines(keepends=True)
    sections: List[tuple[str, List[str]]] = []

    current_headers: List[str] = []
    current_lines: List[str] = []

    header_regex = re.compile(r"^(#{1,6})\s+(.+)$")

    for line in lines:
        match = header_regex.match(line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()

            # Flush existing accumulated lines
            if current_lines:
                sec_text = "".join(current_lines).strip()
                if sec_text:
                    sections.append((sec_text, list(current_headers)))
                current_lines = []

            # Adjust hierarchy based on header level
            # Level 1 (#) keeps 1 item, Level 2 (##) keeps up to 2 items, etc.
            current_headers = current_headers[: level - 1]
            current_headers.append(title)
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "".join(current_lines).strip()
        if sec_text:
            sections.append((sec_text, list(current_headers)))

    # If no headers found at all, treat whole text as one section
    if not sections and text.strip():
        sections.append((text.strip(), []))

    def _append_safely(txt: str, hdrs: List[str]):
        clean = txt.strip()
        if not clean:
            return
        if count_tokens_approx(clean) <= max_tokens:
            refined_sections.append((clean, hdrs))
        else:
            # Check if this oversized block is a markdown table
            if is_markdown_table(clean):
                table_chunks = split_table_with_header_continuity(clean, max_tokens)
                for tc in table_chunks:
                    if tc.strip():
                        refined_sections.append((tc.strip(), hdrs))
            elif clean.startswith("```") or clean.startswith("~~~"):
                code_chunks = split_code_fence_safely(clean, max_tokens)
                for cc in code_chunks:
                    if cc.strip():
                        refined_sections.append((cc.strip(), hdrs))
            else:
                # Subdivide oversized block with sliding window
                overlap = min(100, max_tokens // 10)
                sub_chunks = _split_fixed_size(clean, max_tokens, overlap)
                for sub_txt, _ in sub_chunks:
                    if sub_txt.strip():
                        refined_sections.append((sub_txt.strip(), hdrs))

    # Second pass: bundle tiny sections together up to max_tokens, or split oversized ones
    refined_sections: List[tuple[str, List[str]]] = []
    buffer_text = ""
    buffer_headers: List[str] = []

    for sec_text, headers in sections:
        sec_tokens = count_tokens_approx(sec_text)

        # If a single section is larger than max_tokens, split by page breaks or paragraphs
        if sec_tokens > max_tokens:
            if buffer_text:
                _append_safely(buffer_text, buffer_headers)
                buffer_text = ""
                buffer_headers = []

            page_break_pattern = r"(?:\x0c|<!--\s*pagebreak\s*-->|(?:\n\s*---+\s*\n))"
            if re.search(page_break_pattern, sec_text):
                page_parts = [p.strip() for p in re.split(page_break_pattern, sec_text) if p.strip()]
                p_buf = ""
                for p in page_parts:
                    p_cand = (p_buf + "\n\n-----\n\n" + p).strip() if p_buf else p
                    if count_tokens_approx(p_cand) > max_tokens and p_buf:
                        _append_safely(p_buf, headers)
                        p_buf = p
                    else:
                        p_buf = p_cand
                if p_buf.strip():
                    _append_safely(p_buf, headers)
                continue

            # Use block-aware splitting so code blocks and tables are never severed
            blocks = _split_markdown_into_blocks(sec_text)
            p_buf = ""
            for blk in blocks:
                p_with_sep = (p_buf + "\n\n" + blk).strip() if p_buf else blk
                if count_tokens_approx(p_with_sep) > max_tokens and p_buf:
                    _append_safely(p_buf, headers)
                    p_buf = blk
                else:
                    p_buf = p_with_sep
            if p_buf.strip():
                _append_safely(p_buf, headers)
            continue

        candidate = (buffer_text + "\n\n" + sec_text).strip() if buffer_text else sec_text
        if count_tokens_approx(candidate) <= max_tokens:
            buffer_text = candidate
            if not buffer_headers:
                buffer_headers = headers
        else:
            if buffer_text:
                _append_safely(buffer_text, buffer_headers)
            buffer_text = sec_text
            buffer_headers = headers

    if buffer_text.strip():
        _append_safely(buffer_text, buffer_headers)

    return refined_sections


def _split_fixed_size(text: str, max_tokens: int, overlap_tokens: int) -> List[tuple[str, List[str]]]:
    """Split text into fixed token chunks with sliding window overlap."""
    words = text.split()
    if not words:
        return []

    # Rough conversion: 1 token ≈ 0.75 words (or ~1.3 words per token)
    # To be conservative, use token counting directly on window slices
    chunks: List[tuple[str, List[str]]] = []
    start_idx = 0
    total_words = len(words)

    # Approximate window size in words
    step_words = max(1, int(max_tokens * 0.75))
    overlap_words = int(overlap_tokens * 0.75)
    stride = max(1, step_words - overlap_words)

    while start_idx < total_words:
        end_idx = min(total_words, start_idx + step_words)
        slice_text = " ".join(words[start_idx:end_idx])

        # Trim down if token count exceeds max_tokens
        while count_tokens_approx(slice_text) > max_tokens and end_idx > start_idx + 1:
            end_idx -= 20
            slice_text = " ".join(words[start_idx:end_idx])

        chunks.append((slice_text, []))
        if end_idx >= total_words:
            break
        start_idx += stride

    return chunks


def _split_by_pages(text: str) -> List[tuple[str, List[str]]]:
    """Split text demarcated by form feed characters, page markers, or horizontal rules."""
    page_patterns = re.split(r"(?:\x0c|<!--\s*pagebreak\s*-->|(?:\n\s*---+\s*\n))", text)
    sections: List[tuple[str, List[str]]] = []
    page_num = 1
    for page in page_patterns:
        page_clean = page.strip()
        if page_clean:
            sections.append((page_clean, [f"Page {page_num}"]))
            page_num += 1
    return sections or [(text.strip(), [])]


def _prune_content(text: str) -> str:
    """Pre-LLM context pruning to strip out boilerplate and save tokens (Idea 4)."""
    # 1. Strip basic HTML tags, but preserve <!-- pagebreak -->
    text = re.sub(r"<(?!!-- pagebreak -->)[^>]*>", "", text)
    # 2. Simplify URLs to a <URL> token to save tokens if they are very long (e.g., tracking links)
    text = re.sub(r"https?://[^\s/$.?#].[^\s]*", "<URL>", text)
    # 3. Strip massive repeated markdown tables of contents or navigation links (heuristic: repeated empty list items or markdown nav headers)
    text = re.sub(r"(?im)^##\s+Table of Contents\s*\n(?:^-\s+.*$\n?)+", "", text)
    return text.strip()


def split_document(
    text: str,
    strategy: str = "headers",
    max_tokens: int = 6000,
    overlap_tokens: int = 500,
    provider: Optional[Any] = None,
) -> List[Chunk]:
    """Split document into manageable chunks based on chosen strategy.

    Supported strategies:
        - "headers": Splits at markdown heading boundaries (hierarchical).
        - "fixed_size": Sliding window of tokens with overlap.
        - "pages": Splits by page break markers.
        - "semantic": Topic-aware semantic boundary detection.
    """
    if not text or not text.strip():
        return []

    # Apply context pruning before any chunking
    text = _prune_content(text)

    if strategy == "pages":
        raw_chunks = _split_by_pages(text)
    elif strategy == "semantic":
        raw_chunks = _split_semantic(text, provider, max_tokens)
    else:
        total_tokens = count_tokens_approx(text)
        if total_tokens <= max_tokens:
            # Fits comfortably within a single chunk
            return [Chunk(content=text.strip(), index=1, total=1, header_path=[], token_count=total_tokens)]

        if strategy == "fixed_size":
            raw_chunks = _split_fixed_size(text, max_tokens, overlap_tokens)
        else:  # default "headers"
            raw_chunks = _split_by_headers(text, max_tokens)

    total_count = len(raw_chunks)
    result_chunks: List[Chunk] = []

    for idx, (content, headers) in enumerate(raw_chunks, start=1):
        t_count = count_tokens_approx(content)
        result_chunks.append(
            Chunk(
                content=content,
                index=idx,
                total=total_count,
                header_path=headers,
                token_count=t_count,
            )
        )

    return result_chunks

def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)

def _split_semantic(text: str, provider: Any, max_tokens: int) -> List[tuple[str, List[str]]]:
    """Semantic chunking (Idea 6) based on topic shifts between paragraphs."""
    if not provider:
        # Fallback if no provider is given
        return _split_by_headers(text, max_tokens)
        
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []
        
    # Get embeddings for each paragraph
    # We do this sequentially here for simplicity, though batching would be better if supported
    embeddings = []
    for p in paragraphs:
        emb = provider.embed(p)
        # If embed fails, just use a dummy vector
        embeddings.append(emb if emb else [0.0]*768)
        
    # Calculate similarities between adjacent paragraphs
    similarities = []
    for i in range(len(embeddings) - 1):
        similarities.append(_cosine_similarity(embeddings[i], embeddings[i+1]))
        
    # Identify local minima (drops in similarity)
    # A simple threshold approach: if similarity drops below mean - 0.5 * stddev, it's a boundary
    if similarities:
        mean_sim = sum(similarities) / len(similarities)
        variance = sum((s - mean_sim) ** 2 for s in similarities) / len(similarities)
        stddev = variance ** 0.5
        threshold = mean_sim - (0.5 * stddev)
    else:
        threshold = 0.0

    chunks = []
    current_chunk = []
    
    for i, p in enumerate(paragraphs):
        current_chunk.append(p)
        # Check if this paragraph is a boundary
        if i < len(similarities) and similarities[i] < threshold:
            chunk_text = "\n\n".join(current_chunk)
            # Only split if chunk size is reasonable or getting too big
            if count_tokens_approx(chunk_text) > max_tokens * 0.5:
                chunks.append((chunk_text, []))
                current_chunk = []
                
        # Also split if we exceed max_tokens regardless of semantics
        elif count_tokens_approx("\n\n".join(current_chunk)) > max_tokens:
            # Drop the last paragraph if it pushed us over, or just split here
            chunks.append(("\n\n".join(current_chunk), []))
            current_chunk = []

    if current_chunk:
        chunks.append(("\n\n".join(current_chunk), []))

    return chunks
