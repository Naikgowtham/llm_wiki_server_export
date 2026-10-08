"""CLI command to generate dynamic Maps of Content (MOCs)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from lib.wiki_ops import generate_mocs

console = Console()


@click.command("moc")
@click.option("--wiki-path", "-w", default=".", type=str, help="Path or name of wiki vault.")
def moc_cmd(wiki_path: str):
    """Generate dynamic Map of Content (MOC) dashboards for categories."""
    from lib.utils import resolve_vault_path
    wiki_dir = resolve_vault_path(wiki_path)
    if not wiki_dir or not (wiki_dir / "wiki").is_dir():
        console.print(f"[red]Error:[/red] '{wiki_path}' is not a valid LLM Wiki instance (missing wiki/ folder).")
        raise click.Abort()

    console.print(f"\n[cyan]Generating Maps of Content for:[/cyan] [bold]{wiki_dir.name}[/bold]...")
    generated = generate_mocs(wiki_dir)

    if generated:
        console.print(f"[bold green]✓ Generated/updated {len(generated)} Map of Content dashboard(s):[/bold green]")
        for moc_path in generated:
            console.print(f"  • [green]{moc_path.name}[/green]")
    else:
        console.print("[yellow]No category subfolders with content found to generate MOCs.[/yellow]")
