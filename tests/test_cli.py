import pytest
from click.testing import CliRunner
from cli.main import cli

def test_cli_init(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(tmp_path), "--domain", "Test", "--description", "Test desc"])
    assert result.exit_code == 0
    assert (tmp_path / "wiki.yaml").exists()
    assert (tmp_path / "wiki" / "index.md").exists()


def test_cli_moc_and_export(tmp_path):
    """Test 'wiki moc' and 'wiki export' commands on an initialized vault."""
    runner = CliRunner()
    init_res = runner.invoke(cli, ["init", str(tmp_path), "--domain", "Test", "--description", "Desc"])
    assert init_res.exit_code == 0

    # Add a concept page so export and moc have content
    concept_file = tmp_path / "wiki" / "concepts" / "sample.md"
    concept_file.write_text("---\ntitle: Sample\ntype: concept\ntags: [test]\n---\nSample content.", encoding="utf-8")

    # Run moc command
    moc_res = runner.invoke(cli, ["moc", "-w", str(tmp_path)])
    assert moc_res.exit_code == 0

    # Run export command
    export_res = runner.invoke(cli, ["export", "-w", str(tmp_path)])
    assert export_res.exit_code == 0
    assert (tmp_path / "llm.txt").exists()
    assert "Sample" in (tmp_path / "llm.txt").read_text(encoding="utf-8")


def test_cli_query(tmp_path, monkeypatch):
    """Test 'wiki query' command invoking grounded Q&A."""
    runner = CliRunner()
    runner.invoke(cli, ["init", str(tmp_path), "--domain", "Test", "--description", "Desc"])

    from unittest.mock import MagicMock
    async def mock_run_query(*args, **kwargs):
        return "The capital is Paris.", None

    monkeypatch.setattr("cli.query_cmd.run_query", mock_run_query)
    monkeypatch.setattr("cli.query_cmd.LLMProvider", lambda: MagicMock())

    res = runner.invoke(cli, ["query", "What is capital?", "-w", str(tmp_path)])
    assert res.exit_code == 0
    assert "Paris" in res.output


def test_cli_ingest(tmp_path, monkeypatch):
    """Test 'wiki ingest' command with mock run_ingest."""
    runner = CliRunner()
    runner.invoke(cli, ["init", str(tmp_path), "--domain", "Test", "--description", "Desc"])

    raw_file = tmp_path / "raw" / "note.md"
    raw_file.write_text("# Note\nSample note.", encoding="utf-8")

    from unittest.mock import MagicMock
    from lib.differ import FileChange

    async def mock_run_ingest(*args, **kwargs):
        return [FileChange(path="wiki/concepts/note.md", operation="create", new_content="content")]

    monkeypatch.setattr("cli.ingest_cmd.run_ingest", mock_run_ingest)
    monkeypatch.setattr("cli.ingest_cmd.LLMProvider", lambda: MagicMock())

    res = runner.invoke(cli, ["ingest", "note.md", "-w", str(tmp_path), "--yes"])
    assert res.exit_code == 0


def test_cli_lint(tmp_path, monkeypatch):
    """Test 'wiki lint' command with mock run_lint."""
    runner = CliRunner()
    runner.invoke(cli, ["init", str(tmp_path), "--domain", "Test", "--description", "Desc"])

    from unittest.mock import MagicMock

    async def mock_run_lint(*args, **kwargs):
        return {
            "orphaned_pages": [],
            "broken_links": [],
            "semantic_issues": [],
            "total_issues": 0,
        }

    monkeypatch.setattr("cli.lint_cmd.run_lint", mock_run_lint)
    monkeypatch.setattr("cli.lint_cmd.LLMProvider", lambda: MagicMock())

    res = runner.invoke(cli, ["lint", "-w", str(tmp_path)])
    assert res.exit_code == 0


def test_cli_hooks_and_help():
    """Test 'wiki install-hooks --help' and 'wiki --help' commands."""
    runner = CliRunner()
    hooks_res = runner.invoke(cli, ["install-hooks", "--help"])
    assert hooks_res.exit_code == 0
    assert "Install automated Git" in hooks_res.output

    help_res = runner.invoke(cli, ["--help"])
    assert help_res.exit_code == 0
    assert "LLM Wiki CLI" in help_res.output or "Usage:" in help_res.output

