import pytest
from click.testing import CliRunner
from cli.main import cli

def test_cli_init(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(tmp_path), "--domain", "Test", "--description", "Test desc"])
    assert result.exit_code == 0
    assert (tmp_path / "wiki.yaml").exists()
    assert (tmp_path / "wiki" / "index.md").exists()
