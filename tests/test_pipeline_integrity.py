"""Unit tests for pipeline integrity: stratified compaction, token estimation, code fence preservation,
persistent cooldowns, numeric claim gate false-positive prevention, and git lock recovery."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lib.providers import RateLimitCooldownTracker, LLMProvider
from lib.splitter import _split_by_headers, split_code_fence_safely, is_markdown_table
from lib.utils import count_tokens_approx
from lib.wiki_ops import commit_if_git, run_ingest


def test_token_estimator_density():
    # 1. English prose
    prose = "This is a standard technical document explaining distributed architecture."
    prose_tokens = count_tokens_approx(prose)
    assert 8 <= prose_tokens <= 18

    # 2. Dense code and JSON
    code = '{"query": "SELECT * FROM users WHERE id = $1 AND active = true;", "params": [42]}'
    code_tokens = count_tokens_approx(code)
    assert code_tokens >= 15

    # 3. Dense LaTeX math formula
    math_expr = r"\int_{-\infty}^{\infty} e^{-x^2} dx = \sqrt{\pi}"
    math_tokens = count_tokens_approx(math_expr)
    assert math_tokens >= 10

    # 4. Non-Latin script (Devanagari / Hindi)
    hindi_text = "डेटाबेस प्रबंधन प्रणाली (DBMS)"
    hindi_tokens = count_tokens_approx(hindi_text)
    assert hindi_tokens >= 10


def test_code_fence_safe_splitting():
    # Long code block that exceeds max_tokens (e.g. 50 tokens)
    code_block = (
        "```python\n"
        + "\n".join(f"def step_{i}():\n    return {i} * 2" for i in range(25))
        + "\n```"
    )

    chunks = split_code_fence_safely(code_block, max_tokens=60)
    assert len(chunks) >= 2
    for c in chunks:
        # Every single chunk must start and end with ``` code fences!
        assert c.strip().startswith("```python")
        assert c.strip().endswith("```")


def test_split_by_headers_preserves_code_fences_and_tables():
    # Document with a section containing both a table and a code fence
    markdown_doc = """# Chapter 1: Foundations

## Core Algorithms
Here is the benchmark comparison:

| Algorithm | Complexity | Memory |
| :--- | :--- | :--- |
| QuickSort | O(n log n) | O(log n) |
| MergeSort | O(n log n) | O(n) |
| HeapSort | O(n log n) | O(1) |
| BubbleSort | O(n^2) | O(1) |

And here is the implementation details:

```python
def benchmark(algo, data):
    start = time.time()
    algo(data)
    return time.time() - start
```

Further architectural details follow in subsequent paragraphs.
"""
    # Split with small max_tokens to force section splitting
    sections = _split_by_headers(markdown_doc, max_tokens=70)
    assert len(sections) >= 2

    # Verify that code blocks are not severed in half
    for sec_text, _ in sections:
        if "```python" in sec_text:
            assert "```" in sec_text[sec_text.find("```python") + 9:]
        if "| Algorithm |" in sec_text:
            assert "| :--- |" in sec_text


def test_ratelimit_cooldown_tracker_disk_persistence(tmp_path, monkeypatch):
    cooldown_file = tmp_path / "cooldowns.json"
    monkeypatch.setattr("lib.providers.DEFAULT_COOLDOWNS_FILE", cooldown_file)

    RateLimitCooldownTracker.clear()
    assert not RateLimitCooldownTracker.is_cooling_down("groq/qwen-27b")

    # Mark cooling down
    RateLimitCooldownTracker.mark_cooling_down("groq/qwen-27b", cooldown_seconds=60.0)
    assert RateLimitCooldownTracker.is_cooling_down("groq/qwen-27b")
    assert cooldown_file.exists()

    # Clear in-memory dictionary to simulate process restart
    RateLimitCooldownTracker._cooldowns.clear()
    RateLimitCooldownTracker._quota_exhausted.clear()
    RateLimitCooldownTracker._last_load_mtime = 0.0

    # Fresh process inspection must reload from disk!
    assert RateLimitCooldownTracker.is_cooling_down("groq/qwen-27b")
    cooldowns = RateLimitCooldownTracker.get_cooldowns()
    assert "groq/qwen-27b" in cooldowns

    # Cleanup
    RateLimitCooldownTracker.clear()
    assert not RateLimitCooldownTracker.is_cooling_down("groq/qwen-27b")


def test_commit_if_git_stale_lock_recovery(tmp_path):
    import git
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Tester")
        cfg.set_value("user", "email", "test@example.com")

    (tmp_path / "file.txt").write_text("initial content", encoding="utf-8")
    repo.git.add(A=True)
    repo.git.commit("-m", "initial commit", "--no-verify")

    # Simulate a stale index.lock file left by a crashed process (> 20s old)
    lock_file = tmp_path / ".git" / "index.lock"
    lock_file.write_text("stale lock")
    old_time = time.time() - 30.0
    import os
    os.utime(lock_file, (old_time, old_time))

    (tmp_path / "file.txt").write_text("updated content", encoding="utf-8")

    # commit_if_git must recover from the stale lock and succeed!
    success = commit_if_git(tmp_path, "update file")
    assert success is True
    assert not lock_file.exists()


def test_numeric_claim_gate_false_positives(tmp_path, monkeypatch):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "wiki").mkdir()
    (wiki_dir / "raw").mkdir()

    raw_file = wiki_dir / "raw" / "network.md"
    raw_file.write_text(
        "# Network Addressing\n\n"
        "IPv6 provides an enormous address space of 3.4 x 10^38 addresses. "
        "It supports dual-stack operation.\n"
    )

    # Synthesized page contains scientific notation, numbered lists, and acronyms
    synth_resp = json.dumps([
        {
            "path": "wiki/concepts/ipv6.md",
            "operation": "create",
            "reason": "Synthesized IPv6 concept",
            "content": (
                "---\n"
                "title: IPv6 Architecture\n"
                "type: concept\n"
                "confidence: high\n"
                "---\n"
                "# IPv6 Architecture\n\n"
                "Key capabilities:\n"
                "1. Vast address space of 3.4 x 10^38 addresses.\n"
                "2. Dual-stack support with 2PL coordination and OAuth2 security.\n"
            ),
        }
    ])

    mock_provider = MagicMock()
    mock_provider.acall = asyncio.iscoroutinefunction(MagicMock())
    async def mock_acall(messages, operation=None):
        if operation == "ingest_synth":
            return synth_resp
        return "[]"
    mock_provider.acall = mock_acall

    from lib.config import WikiConfig
    monkeypatch.setattr("lib.wiki_ops.load_wiki_config", lambda d: WikiConfig(domain_name="Networking"))

    applied = asyncio.run(run_ingest(
        source_file=raw_file,
        wiki_dir=wiki_dir,
        provider=mock_provider,
        auto_approve=True,
    ))

    assert len(applied) == 1
    created_page = (wiki_dir / "wiki" / "concepts" / "ipv6.md").read_text(encoding="utf-8")
    # Must NOT be demoted to confidence: low!
    assert "confidence: high" in created_page or "confidence: low" not in created_page


def test_link_integrity_scrubs_unbacked_and_rejected_wikilinks(tmp_path, monkeypatch):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "wiki").mkdir()
    (wiki_dir / "raw").mkdir()

    raw_file = wiki_dir / "raw" / "database.md"
    raw_file.write_text("# Database Transactions\n\nExplaining transaction properties.")

    # Page 1 references valid Page 2, a rejected short page (stub < 100 chars), and an unbacked peripheral concept
    synth_resp = json.dumps([
        {
            "path": "wiki/concepts/transactions.md",
            "operation": "create",
            "reason": "Synthesized transactions",
            "content": (
                "---\n"
                "title: Transactions\n"
                "type: concept\n"
                "confidence: high\n"
                "---\n"
                "# Transactions\n\n"
                "Transactions ensure [[Atomicity]] and reference [[DanglingStub]] while maintaining [[IsolatedState]].\n"
            ),
        },
        {
            "path": "wiki/concepts/atomicity.md",
            "operation": "create",
            "reason": "Synthesized atomicity",
            "content": (
                "---\n"
                "title: Atomicity\n"
                "type: concept\n"
                "confidence: high\n"
                "---\n"
                "# Atomicity\n\n"
                "All or nothing execution semantics for database transactions across distributed clusters.\n"
            ),
        },
        {
            "path": "wiki/entities/dangling_stub.md",
            "operation": "create",
            "reason": "Truncated junk stub",
            "content": "Too short",  # < 100 chars, rejected
        },
    ])

    mock_provider = MagicMock()
    async def mock_acall(messages, operation=None):
        if operation == "ingest_synth":
            return synth_resp
        return "[]"
    mock_provider.acall = mock_acall

    from lib.config import WikiConfig
    monkeypatch.setattr("lib.wiki_ops.load_wiki_config", lambda d: WikiConfig(domain_name="Databases"))

    applied = asyncio.run(run_ingest(
        source_file=raw_file,
        wiki_dir=wiki_dir,
        provider=mock_provider,
        auto_approve=True,
    ))

    tx_page = (wiki_dir / "wiki" / "concepts" / "transactions.md").read_text(encoding="utf-8")
    # Valid synthesized link [[Atomicity]] is retained
    assert "[[Atomicity]]" in tx_page
    # Rejected stub link was scrubbed to plain text
    assert "[[DanglingStub]]" not in tx_page
    assert "DanglingStub" in tx_page
    # Unbacked peripheral link [[IsolatedState]] was scrubbed to plain text
    assert "[[IsolatedState]]" not in tx_page
    assert "IsolatedState" in tx_page
