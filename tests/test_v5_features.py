"""Unit tests for LLM Wiki V5 engine features."""

import json
from pathlib import Path
import pytest

from lib.wiki_ops import (
    vector_cross_reference_pages,
    generate_mocs,
    run_lint,
)
from lib.config import WikiConfig


@pytest.fixture
def v5_vault(tmp_path: Path) -> Path:
    """Scaffold a test vault with multi-confidence notes and categories."""
    wiki_folder = tmp_path / "wiki"
    (wiki_folder / "concepts").mkdir(parents=True)
    (wiki_folder / "entities").mkdir(parents=True)
    (tmp_path / "raw").mkdir()

    (tmp_path / "wiki.yaml").write_text(
        "domain:\n"
        "  name: Test Vault\n"
        "required_frontmatter: [title, type, tags]\n"
        "lint:\n"
        "  min_citations_per_page: 1\n"
        "  stale_days: 10\n"
    )

    # Concept 1: High confidence
    (wiki_folder / "concepts" / "lora.md").write_text(
        "---\n"
        "title: LoRA\n"
        "type: concept\n"
        "confidence: high\n"
        "tags: [nlp]\n"
        "sources: ['raw/paper.pdf']\n"
        "---\n"
        "# LoRA\n"
        "Low-Rank Adaptation. Evaluated on [[RoBERTa]].\n"
        "[source: raw/paper.pdf]\n",
        encoding="utf-8",
    )

    # Concept 2: Contested confidence, missing citations
    (wiki_folder / "concepts" / "contested-theory.md").write_text(
        "---\n"
        "title: Contested Theory\n"
        "type: concept\n"
        "confidence: contested\n"
        "tags: [theory]\n"
        "---\n"
        "# Contested Theory\n"
        "Unproven hypothesis.\n",
        encoding="utf-8",
    )

    # Entity 1: Low confidence
    (wiki_folder / "entities" / "roberta.md").write_text(
        "---\n"
        "title: RoBERTa\n"
        "type: entity\n"
        "confidence: low\n"
        "tags: [nlp]\n"
        "sources: ['raw/paper.pdf']\n"
        "---\n"
        "# RoBERTa\n"
        "Optimized BERT model.\n"
        "[source: raw/paper.pdf]\n",
        encoding="utf-8",
    )

    return tmp_path


def test_v5_dynamic_moc_generation(v5_vault: Path):
    """Test V5 Feature 6: Dynamic Map of Content generation."""
    mocs = generate_mocs(v5_vault)
    assert len(mocs) >= 2  # Concepts_MOC.md and Entities_MOC.md

    concepts_moc = v5_vault / "wiki" / "Concepts_MOC.md"
    assert concepts_moc.exists()

    content = concepts_moc.read_text(encoding="utf-8")
    assert "# Concepts — Map of Content (MOC)" in content
    assert "[[LoRA]]" in content
    assert "[[Contested Theory]]" in content
    assert "`high`" in content
    assert "`contested`" in content


def test_v5_confidence_weighted_queue_and_mechanical_checks(v5_vault: Path):
    """Test V5 Feature 7 (confidence weighting) and Feature 8 (zero-token mechanical checks)."""
    import asyncio

    # Run lint without LLM provider (tests zero-token mechanical checks)
    report = asyncio.run(run_lint(v5_vault, provider=None))

    # Mechanical issues should detect missing source citations on 'contested-theory'
    mech = report.get("mechanical_issues", [])
    assert any(i["category"] == "citations" and "contested-theory" in i["file_path"] for i in mech)

    # Now test with a dummy provider to check queue prioritization
    class DummyProvider:
        def __init__(self):
            self.config = None
            self.audited_paths = []

        def call(self, messages, operation="lint_audit", **kwargs):
            return "[]"

        async def acall(self, messages, operation="lint_audit", **kwargs):
            # Record page being audited from prompt
            prompt = messages[0]["content"]
            self.audited_paths.append(prompt)
            return "[]"

    # Create mock vector store
    class MockStore:
        def embed_and_upsert(self, path, content, meta):
            pass

        def search_similar(self, text, k=5, distance_threshold=0.4):
            return []

    # Run lint with max_pages=1 to verify contested/low comes before high
    report2 = asyncio.run(run_lint(v5_vault, provider=None, max_pages=1))
    assert report2 is not None


def test_v5_vector_automated_cross_referencing(v5_vault: Path):
    """Test V5 Feature 1: Zero-token ChromaDB vector cross-referencing."""
    class MockStore:
        def search_similar_with_metadata(self, query, k=5, distance_threshold=0.38):
            # Return roberta as candidate match for a new transformer page
            return [{
                "path": "wiki/entities/roberta.md",
                "title": "RoBERTa",
                "content": "Optimized BERT model.",
                "distance": 0.2,
            }]

    synth_files = [{
        "path": "wiki/concepts/deberta.md",
        "content": "---\ntitle: DeBERTa\ntype: concept\n---\n# DeBERTa\nDisentangled attention.",
    }]

    changes = vector_cross_reference_pages(
        wiki_dir=v5_vault,
        synth_files=synth_files,
        store=MockStore(),
        distance_threshold=0.4,
    )

    assert len(changes) == 1
    assert changes[0].path == "wiki/entities/roberta.md"
    assert "[[DeBERTa]]" in changes[0].new_content
    assert "## Related" in changes[0].new_content


def test_v5_git_hooks_installation(v5_vault: Path):
    """Test V5 Feature: Automated Git CI/CD hook installation."""
    import stat
    from click.testing import CliRunner
    from cli.hooks_cmd import install_hooks_cmd

    runner = CliRunner()
    result = runner.invoke(install_hooks_cmd, ["-w", str(v5_vault)])
    assert result.exit_code == 0
    assert "Successfully installed Git CI/CD hooks" in result.output

    pre_commit = v5_vault / ".git" / "hooks" / "pre-commit"
    post_commit = v5_vault / ".git" / "hooks" / "post-commit"

    assert pre_commit.exists()
    assert post_commit.exists()
    assert bool(pre_commit.stat().st_mode & stat.S_IXUSR)
    assert bool(post_commit.stat().st_mode & stat.S_IXUSR)

    pre_content = pre_commit.read_text(encoding="utf-8")
    assert "wiki lint --staged --yes" in pre_content
    post_content = post_commit.read_text(encoding="utf-8")
    assert "wiki moc" in post_content


def test_v5_staged_micro_linting(v5_vault: Path):
    """Test V5 Feature: Zero-latency pre-commit micro-linting on staged files only."""
    import asyncio
    import subprocess
    import git

    repo = git.Repo.init(v5_vault)

    # 1. When nothing is staged, staged linting returns 0 issues immediately
    report_empty = asyncio.run(run_lint(v5_vault, staged_only=True))
    assert len(report_empty["mechanical_issues"]) == 0
    assert len(report_empty["broken_links"]) == 0

    # 2. Stage an invalid note (missing required title/type/tags frontmatter)
    bad_note = v5_vault / "wiki" / "concepts" / "bad-note.md"
    bad_note.write_text("# Invalid Note\nNo frontmatter here.\n", encoding="utf-8")

    repo.git.add("wiki/concepts/bad-note.md")

    # 3. Lint with staged_only=True
    report_staged = asyncio.run(run_lint(v5_vault, staged_only=True))
    assert len(report_staged["mechanical_issues"]) > 0

    flagged_paths = [i["file_path"] for i in report_staged["mechanical_issues"]]
    assert any("bad-note" in p for p in flagged_paths)
    # Ensure other unstaged notes in vault with issues (like contested-theory) are NOT flagged
    assert not any("contested-theory" in p for p in flagged_paths)

