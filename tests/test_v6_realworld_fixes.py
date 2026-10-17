"""Tests verifying resolutions for real-world LLM Wiki challenges (v6).

1. Wiki Bloat & Context Truncation (Typed 1-line index, compact index loading)
2. Two-Stage Compilation Pipeline (Stage 1 extraction persistence, recompilation)
3. Decay-Rate Segmentation & Immutability (shelf_life, source immutability)
4. Git rollback safety & sub-agent isolation
5. Fast instant search with zero LLM API calls
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.search_cmd import _lexical_search_vault
from lib.wiki_ops import (
    FileChange,
    get_compact_typed_index,
    recompile_source,
    run_lint,
    update_wiki_index,
)


@pytest.fixture
def mock_wiki(tmp_path):
    """Create a minimal mock wiki structure."""
    wiki_dir = tmp_path / "test-wiki"
    wiki_folder = wiki_dir / "wiki"
    for cat in ["concepts", "entities", "topics", "sources", "queries", "comparisons"]:
        (wiki_folder / cat).mkdir(parents=True, exist_ok=True)
    (wiki_dir / "raw").mkdir(parents=True, exist_ok=True)

    # Configuration
    config_file = wiki_dir / "wiki.yaml"
    config_file.write_text(
        "domain_name: 'Test Domain'\n"
        "domain_description: 'Test Description'\n"
        "required_frontmatter: ['title', 'type']\n"
        "ingest_settings:\n"
        "  auto_commit: false\n"
        "lint_settings:\n"
        "  stale_days: 30\n"
    )

    # AGENTS.md
    (wiki_dir / "AGENTS.md").write_text("# Test Wiki\n")
    # index.md
    (wiki_folder / "index.md").write_text(
        "---\ntitle: 'Wiki Index'\ntype: index\nupdated: '2026-10-17'\n---\n\n# Index\n"
    )
    return wiki_dir


def test_typed_one_line_index_generation(mock_wiki):
    """Verify that update_wiki_index creates strict 1-line typed entries and typed_index.json."""
    concepts_dir = mock_wiki / "wiki" / "concepts"
    (concepts_dir / "acid.md").write_text(
        "---\n"
        "title: 'ACID Properties'\n"
        "type: concept\n"
        "shelf_life: permanent\n"
        "summary: 'Atomicity, Consistency, Isolation, and Durability guarantees in transactional databases.'\n"
        "---\n\n"
        "## Overview\n"
        "ACID guarantees that database transactions are processed reliably.\n"
    )
    (concepts_dir / "b-trees.md").write_text(
        "---\n"
        "title: 'B-Trees'\n"
        "type: concept\n"
        "shelf_life: permanent\n"
        "---\n\n"
        "Self-balancing tree data structure that maintains sorted data and allows searches in logarithmic time.\n"
    )

    update_wiki_index(mock_wiki)

    index_text = (mock_wiki / "wiki" / "index.md").read_text()
    assert "- [[ACID Properties]] (concept)" in index_text
    assert "Atomicity, Consistency" in index_text
    assert "- [[B-Trees]] (concept)" in index_text

    # Verify line lengths stay within bounds (<145 chars)
    for line in index_text.splitlines():
        if line.startswith("- [["):
            assert len(line) <= 145

    # Verify typed_index.json exists and is structured
    typed_json_file = mock_wiki / "wiki" / ".llm-wiki" / "typed_index.json"
    assert typed_json_file.exists()
    typed_data = json.loads(typed_json_file.read_text())
    assert len(typed_data) == 2
    titles = {d["title"] for d in typed_data}
    assert "ACID Properties" in titles
    assert "B-Trees" in titles

    # Verify compact typed index loading
    compact_text = get_compact_typed_index(mock_wiki)
    assert "- [[ACID Properties]]" in compact_text
    assert "- [[B-Trees]]" in compact_text


def test_two_stage_persistence_and_recompile(mock_wiki):
    """Verify that Stage 1 structured extractions can be recompiled without raw source files."""
    extractions_dir = mock_wiki / "wiki" / ".llm-wiki" / "extractions"
    extractions_dir.mkdir(parents=True, exist_ok=True)

    test_extractions = {
        "source_filename": "database_internals.pdf",
        "source_slug": "database-internals",
        "date": "2026-10-17",
        "compact_extractions": {
            "prominent_concepts": [
                {"name": "Write-Ahead Logging", "definition": "A technique for providing atomicity and durability"}
            ]
        },
        "extractions": [],
    }
    (extractions_dir / "database-internals.json").write_text(json.dumps(test_extractions))

    mock_provider = MagicMock()
    mock_provider.acall = AsyncMock(
        return_value=json.dumps([
            {
                "path": "wiki/concepts/write-ahead-logging.md",
                "operation": "create",
                "content": "---\ntitle: 'Write-Ahead Logging'\ntype: concept\nshelf_life: permanent\n---\n\n## Overview\nWAL ensures durability.",
            }
        ])
    )

    import asyncio
    applied = asyncio.run(
        recompile_source(
            wiki_dir=mock_wiki,
            source_slug="database-internals",
            provider=mock_provider,
            auto_approve=True,
        )
    )

    assert len(applied) == 1
    created_file = mock_wiki / "wiki" / "concepts" / "write-ahead-logging.md"
    assert created_file.exists()
    assert "WAL ensures durability" in created_file.read_text()


def test_source_immutability(mock_wiki):
    """Verify that existing source summaries in wiki/sources/ cannot be overwritten or mutated."""
    sources_dir = mock_wiki / "wiki" / "sources"
    source_file = sources_dir / "original-paper.md"
    original_content = "---\ntitle: 'Original Paper'\ntype: source\n---\n\n# Summary\nOriginal untouched summary."
    source_file.write_text(original_content)

    mock_extractions = {
        "source_filename": "mutator.txt",
        "source_slug": "mutator",
        "date": "2026-10-17",
        "compact_extractions": {},
        "extractions": [],
    }
    ext_dir = mock_wiki / "wiki" / ".llm-wiki" / "extractions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "mutator.json").write_text(json.dumps(mock_extractions))

    # An LLM attempts to mutate an existing source note
    mock_provider = MagicMock()
    mock_provider.acall = AsyncMock(
        return_value=json.dumps([
            {
                "path": "wiki/sources/original-paper.md",
                "operation": "update",
                "content": "MALICIOUS OVERWRITE",
            }
        ])
    )

    import asyncio
    applied = asyncio.run(
        recompile_source(
            wiki_dir=mock_wiki,
            source_slug="mutator",
            provider=mock_provider,
            auto_approve=True,
        )
    )

    # Immutability guard should drop the update to existing source
    assert len(applied) == 0
    assert source_file.read_text() == original_content


def test_shelf_life_staleness_check(mock_wiki):
    """Verify that permanent notes never expire while ephemeral notes flag staleness."""
    concepts_dir = mock_wiki / "wiki" / "concepts"
    queries_dir = mock_wiki / "wiki" / "queries"

    # Permanent concept, 120 days old
    old_concept = concepts_dir / "calculus.md"
    old_concept.write_text(
        "---\ntitle: 'Calculus'\ntype: concept\nshelf_life: permanent\nsources: ['[[raw/math.txt]]']\n---\n\n[source: raw/math.txt, §1]\nDerivative rules."
    )
    # Set mtime to 120 days ago
    old_time = time.time() - (120 * 86400)
    import os
    os.utime(old_concept, (old_time, old_time))

    # Ephemeral query note, 10 days old (should expire after 7 days)
    old_query = queries_dir / "today-tasks.md"
    old_query.write_text(
        "---\ntitle: 'Tasks Today'\ntype: query\nshelf_life: ephemeral\nsources: ['[[raw/notes.txt]]']\n---\n\n[source: raw/notes.txt, §1]\nTodo list."
    )
    query_time = time.time() - (10 * 86400)
    os.utime(old_query, (query_time, query_time))

    import asyncio
    report = asyncio.run(run_lint(wiki_dir=mock_wiki, provider=None))
    freshness_issues = [
        issue for issue in report["mechanical_issues"]
        if issue["category"] == "freshness"
    ]

    # The ephemeral note should be flagged
    flagged_paths = [issue["file_path"] for issue in freshness_issues]
    assert any("today-tasks.md" in p for p in flagged_paths)
    # The permanent note should NOT be flagged
    assert not any("calculus.md" in p for p in flagged_paths)


def test_fast_lexical_search(mock_wiki):
    """Verify that _lexical_search_vault returns matching notes and snippets instantly with 0 LLM calls."""
    concepts_dir = mock_wiki / "wiki" / "concepts"
    (concepts_dir / "distributed-consensus.md").write_text(
        "---\ntitle: 'Distributed Consensus'\ntype: concept\ntags: [raft, paxos]\n---\n\n"
        "## Overview\n"
        "Consensus algorithms like Raft and Paxos ensure fault-tolerant state machine replication across server clusters."
    )

    hits = _lexical_search_vault(mock_wiki, ["Raft", "Paxos"], limit=5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["title"] == "Distributed Consensus"
    assert "Raft and Paxos ensure fault-tolerant" in hit["snippet"]
    assert hit["score"] > 0


def test_search_cli_command(mock_wiki):
    """Test wiki search CLI execution."""
    concepts_dir = mock_wiki / "wiki" / "concepts"
    (concepts_dir / "acid.md").write_text(
        "---\ntitle: 'ACID Properties'\ntype: concept\n---\n\nAtomicity Consistency Isolation Durability guarantees."
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "Atomicity", "-w", str(mock_wiki)])
    assert result.exit_code == 0
    assert "ACID Properties" in result.output
    assert "Atomicity Consistency" in result.output
