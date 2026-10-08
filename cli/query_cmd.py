"""CLI command to query the LLM Wiki."""

from __future__ import annotations

import asyncio

from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from lib.providers import LLMProvider, LLMProviderError
from lib.wiki_ops import run_query

console = Console()


@click.command("query")
@click.argument("question")
@click.option("--wiki-path", "-w", default=".", type=str, help="Path or name of wiki vault (e.g. ai-wiki or ~/wikis/ai-wiki).")
@click.option("--file-back", "-f", is_flag=True, help="Save the synthesized answer permanently into wiki/queries/.")
@click.option("--hybrid", is_flag=True, help="Use qmd hybrid search instead of ChromaDB vector search.")
@click.option("--stream", is_flag=True, help="Stream synthesized answer to the console in real-time.")
def query_cmd(question: str, wiki_path: str, file_back: bool, hybrid: bool, stream: bool):
    """Ask a question grounded entirely in the compiled knowledge wiki."""
    from lib.utils import resolve_vault_path, list_available_vaults

    wiki_dir = resolve_vault_path(wiki_path, query=question)

    if not wiki_dir:
        available = list_available_vaults()
        if available:
            console.print(f"[red]Error:[/red] Current directory is not a wiki vault.")
            console.print("\n[bold cyan]Discovered available vaults:[/bold cyan]")
            for av in available:
                console.print(f"  • [green]{av.name}[/green] ({av})")
            console.print(f"\nPlease specify which vault to query using [bold]-w <name>[/bold]:")
            console.print(f"  [white]wiki query \"{question}\" -w {available[0].name} --stream[/white]\n")
        else:
            console.print(f"[red]Error:[/red] '{wiki_path}' is not a wiki vault, and no vaults found in ~/wikis.")
        raise click.Abort()

    console.print(f"[dim]Vault: [bold]{wiki_dir.name}[/bold] ({wiki_dir})[/dim]")
    console.print(f"\n[cyan]Synthesizing answer from wiki context:[/cyan] [bold]\"{question}\"[/bold]\n")

    try:
        provider = LLMProvider()
        if stream:
            # Stream mode: fetch context and stream answer tokens
            from lib.vector_store import WikiVectorStore
            from lib.wiki_ops import get_template, load_wiki_config
            cfg = load_wiki_config(wiki_dir)
            store = WikiVectorStore(wiki_dir, provider)
            context_pages = store.search_similar_with_metadata(question, k=5, distance_threshold=0.5)
            tmpl = get_template("query_answer.md")
            prompt_str = tmpl.render(domain_name=cfg.domain_name, question=question, context_pages=context_pages)
            
            console.print("[bold cyan]Answer:[/bold cyan]")
            streamed_chunks = []
            for chunk in provider.stream_call([{"role": "user", "content": prompt_str}], operation="query_answer"):
                print(chunk, end="", flush=True)
                streamed_chunks.append(chunk)
            print("\n")
            return

        answer, filed_path = asyncio.run(run_query(
            question=question,
            wiki_dir=wiki_dir,
            provider=provider,
            file_back=file_back,
            use_hybrid=hybrid,
        ))

        console.print(Panel(Markdown(answer), title="Wiki Answer", border_style="cyan"))

        if filed_path:
            console.print(f"\n[bold green]✓ Answer permanently filed to wiki:[/bold green] [cyan]{filed_path.relative_to(wiki_dir)}[/cyan]")
        elif not file_back:
            console.print("\n[dim]Tip: Use `--file-back` to persist high-value answers as permanent notes in your wiki.[/dim]")

    except LLMProviderError as e:
        console.print(f"[red]LLM Provider Error:[/red] {e}")
    except Exception as e:
        console.print(f"[red]Query execution failed:[/red] {e}")
