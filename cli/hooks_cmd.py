"""CLI command to install automated Git hooks into an LLM Wiki vault."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import click
from rich.console import Console

console = Console()


@click.command("install-hooks")
@click.option("--wiki-path", "-w", default=".", type=str, help="Path or name of wiki vault.")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing Git hooks if present.")
def install_hooks_cmd(wiki_path: str, force: bool):
    """Install automated Git CI/CD hooks (pre-commit micro-linting & post-commit MOC sync)."""
    from lib.utils import resolve_vault_path
    wiki_dir = resolve_vault_path(wiki_path)
    if not wiki_dir:
        wiki_dir = Path(wiki_path).resolve()
    git_dir = wiki_dir / ".git"

    if not git_dir.is_dir():
        # Try initializing git if not initialized
        try:
            import git
            git.Repo.init(wiki_dir)
            console.print(f"[cyan]Initialized new Git repository at {wiki_dir}[/cyan]")
        except Exception as e:
            console.print(f"[red]Error:[/red] {wiki_dir} is not a Git repository and could not be initialized: {e}")
            raise click.Abort()

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    pre_commit_hook = hooks_dir / "pre-commit"
    post_commit_hook = hooks_dir / "post-commit"

    if (pre_commit_hook.exists() or post_commit_hook.exists()) and not force:
        console.print("[yellow]Git hooks already exist. Use `--force` to overwrite.[/yellow]")
        return

    # Pre-commit hook: runs staged micro-linting
    pre_commit_script = """#!/bin/sh
# LLM Wiki V5: Pre-Commit Micro-Linting
echo "🔍 Running LLM Wiki pre-commit micro-lint on staged notes..."
wiki lint --staged --yes
if [ $? -ne 0 ]; then
    echo "❌ Wiki micro-lint failed. Fix errors or bypass with git commit --no-verify"
    exit 1
fi
"""

    # Post-commit hook: runs MOC update and sync
    post_commit_script = """#!/bin/sh
# LLM Wiki V5: Post-Commit MOC & Index Sync
wiki moc --wiki-path "$PWD" >/dev/null 2>&1 || true
"""

    pre_commit_hook.write_text(pre_commit_script, encoding="utf-8")
    post_commit_hook.write_text(post_commit_script, encoding="utf-8")

    # Make executable
    for hook_file in (pre_commit_hook, post_commit_hook):
        st = os.stat(hook_file)
        os.chmod(hook_file, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    console.print(f"[bold green]✓ Successfully installed Git CI/CD hooks at {hooks_dir}:[/bold green]")
    console.print("  • [cyan]pre-commit[/cyan]: Micro-lints staged notes on every commit (zero-latency).")
    console.print("  • [cyan]post-commit[/cyan]: Silently updates MOCs and index after commit.")
