"""CLI command to ingest raw source documents into the LLM Wiki."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from lib.providers import LLMProvider, LLMProviderError
from lib.wiki_ops import run_ingest, SynthesisParseError

console = Console()


@click.command("ingest")
@click.argument("source_path", required=False, type=str)
@click.option("--wiki-path", "-w", default=".", type=str, help="Path or name of wiki vault.")
@click.option("--all", "ingest_all", is_flag=True, help="Ingest all uncompiled files from the raw/ folder.")
@click.option("--yes", "-y", is_flag=True, help="Auto-approve all proposed file operations (non-interactive).")
def ingest_cmd(source_path: Optional[str], wiki_path: str, ingest_all: bool, yes: bool):
    """Ingest raw sources (articles, papers, notes) into compiled wiki pages."""
    from lib.utils import resolve_vault_path
    resolved = resolve_vault_path(wiki_path)
    wiki_dir = resolved if resolved else Path(wiki_path).resolve()
    raw_dir = wiki_dir / "raw"

    if not (wiki_dir / "wiki").is_dir() or not (wiki_dir / "AGENTS.md").is_file():
        console.print(f"[red]Error:[/red] {wiki_dir} is not a valid LLM Wiki instance (missing AGENTS.md or wiki/ folder).")
        raise click.Abort()

    # Determine files to process
    sources_to_process = []
    if ingest_all:
        if not raw_dir.is_dir():
            console.print(f"[red]Error:[/red] No raw/ directory found at {raw_dir}")
            return
            
        from cli.watch_cmd import get_already_ingested
        from lib.utils import slugify
        ingested = get_already_ingested(wiki_dir)
        
        for f in sorted(raw_dir.glob("*")):
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in (".md", ".txt", ".pdf"):
                if f.stem not in ingested and f.name not in ingested and slugify(f.stem) not in ingested:
                    sources_to_process.append(f)
        if not sources_to_process:
            console.print("[yellow]No uncompiled raw source documents found in raw/[/yellow]")
            return
    elif source_path:
        p = Path(source_path)
        if not p.is_absolute():
            # Check relative to cwd, then relative to wiki_dir
            if p.exists():
                sources_to_process.append(p.resolve())
            elif (wiki_dir / p).exists():
                sources_to_process.append((wiki_dir / p).resolve())
            else:
                console.print(f"[red]Error:[/red] Source file not found: {source_path}")
                return
        else:
            if not p.exists():
                console.print(f"[red]Error:[/red] Source file not found: {source_path}")
                return
            sources_to_process.append(p.resolve())
    else:
        console.print("[red]Error:[/red] Please specify a source file to ingest or use --all.")
        return

    console.print(f"\n[cyan]Ingesting {len(sources_to_process)} source document(s) into:[/cyan] [bold]{wiki_dir.name}[/bold]\n")

    try:
        provider = LLMProvider()
    except Exception as e:
        console.print(f"[red]Error initializing LLM Provider:[/red] {e}")
        return

    for src in sources_to_process:
        console.print(f"[bold cyan]Processing:[/bold cyan] {src.name}...")
        try:
            applied = asyncio.run(run_ingest(
                source_file=src,
                wiki_dir=wiki_dir,
                provider=provider,
                auto_approve=yes,
            ))
            if applied:
                console.print(f"[green]✓ Successfully integrated {src.name}[/green] ({len(applied)} file changes committed)\n")
            else:
                console.print(f"[yellow]No changes were applied for {src.name}[/yellow]\n")
        except LLMProviderError as e:
            console.print(f"[red]LLM Provider Error:[/red] {e}")
            break
        except SynthesisParseError as e:
            console.print(f"[bold red]Synthesis Parse Error:[/bold red]\n{e}")
            import sys
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]Failed processing {src.name}:[/red] {e}")
