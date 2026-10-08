"""LLM Wiki CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from cli.init_cmd import init_cmd
from cli.ingest_cmd import ingest_cmd
from cli.lint_cmd import lint_cmd
from cli.query_cmd import query_cmd
from cli.setup_cmd import setup_cmd
from cli.watch_cmd import watch_cmd
from lib.config import load_wiki_config

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="llm-wiki")
def cli():
    """LLM Wiki — Compiler and maintainer for persistent knowledge wikis on Obsidian."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


@cli.command("status")
@click.option("--wiki-path", "-w", default=".", type=str, help="Path or name of wiki vault (e.g. ai-wiki or ~/wikis/ai-wiki).")
def status_cmd(wiki_path: str):
    """Display high-level statistics and health of the current wiki instance."""
    from lib.utils import resolve_vault_path, list_available_vaults
    wiki_dir = resolve_vault_path(wiki_path)
    if not wiki_dir:
        available = list_available_vaults()
        if available:
            console.print("[bold cyan]Available Wiki Vaults:[/bold cyan]")
            for av in available:
                console.print(f"  • [green]{av.name}[/green] ({av})")
            console.print("\nRun: [white]wiki status -w <name>[/white] to view details of a specific vault.")
        else:
            console.print(f"[red]Error:[/red] No LLM Wiki vault found at '{wiki_path}' or in ~/wikis.")
        return

    config = load_wiki_config(wiki_dir)
    table = Table(title=f"Wiki Status: {config.domain_name}", show_header=True, header_style="bold cyan")
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Folder Location", style="dim")

    categories = [
        ("Raw Sources", wiki_dir / "raw"),
        ("Source Summaries", wiki_dir / "wiki" / "sources"),
        ("Concepts", wiki_dir / "wiki" / "concepts"),
        ("Entities", wiki_dir / "wiki" / "entities"),
        ("Topics", wiki_dir / "wiki" / "topics"),
        ("Comparisons", wiki_dir / "wiki" / "comparisons"),
        ("Filed Queries", wiki_dir / "wiki" / "queries"),
    ]

    total_compiled = 0
    for label, folder in categories:
        if folder.exists():
            files = [f for f in folder.glob("*") if f.is_file() and not f.name.startswith(".")]
            count = len(files)
            if "wiki" in str(folder):
                total_compiled += count
            table.add_row(label, str(count), str(folder.relative_to(wiki_dir)))
        else:
            table.add_row(label, "0", str(folder.relative_to(wiki_dir)))

    console.print(table)
    console.print(f"\n[bold]Total Compiled Pages:[/bold] [green]{total_compiled}[/green]")

    # Check Git commit status
    try:
        import git
        repo = git.Repo(wiki_dir)
        last_commit = repo.head.commit
        console.print(f"[bold]Last Git Commit:[/bold] {last_commit.hexsha[:7]} - {last_commit.message.strip()}")
    except Exception:
        pass


# Register subcommands
from cli.export_cmd import export_cmd
from cli.hooks_cmd import install_hooks_cmd
from cli.mcp_cmd import mcp_cmd
from cli.mcp_router_cmd import mcp_router_cmd
from cli.moc_cmd import moc_cmd
from cli.ui_server import dashboard_cmd

cli.add_command(init_cmd)
cli.add_command(ingest_cmd)
cli.add_command(query_cmd)
cli.add_command(lint_cmd)
cli.add_command(status_cmd)
cli.add_command(setup_cmd)
cli.add_command(watch_cmd)
cli.add_command(export_cmd)
cli.add_command(mcp_cmd)
cli.add_command(mcp_router_cmd)
cli.add_command(dashboard_cmd)
cli.add_command(moc_cmd)
cli.add_command(install_hooks_cmd)


if __name__ == "__main__":
    cli()
