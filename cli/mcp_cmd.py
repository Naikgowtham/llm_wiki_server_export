"""Command to launch the MCP Server using tobi/qmd."""

import os
import subprocess
from pathlib import Path

import click
from rich.console import Console

console = Console()

@click.command("mcp")
@click.option("--wiki-path", "-w", default=".", type=click.Path(exists=True, file_okay=False), help="Path to wiki vault.")
def mcp_cmd(wiki_path: str):
    """Launch an MCP (Model Context Protocol) server via tobi/qmd for Agentic Integration."""
    wiki_dir = Path(wiki_path).resolve()
    
    if not (wiki_dir / "wiki").is_dir():
        console.print(f"[red]Error:[/red] {wiki_dir} is not a valid LLM Wiki instance.")
        raise click.Abort()
        
    console.print(f"[cyan]Initializing tobi/qmd MCP Server for {wiki_dir.name}...[/cyan]")
    
    # Check if qmd is installed
    try:
        subprocess.run(["qmd", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[red]Error: `qmd` is not installed or not in PATH.[/red]")
        console.print("Please install tobi/qmd globally using Bun:")
        console.print("\n  [bold]bun install -g https://github.com/tobi/qmd[/bold]\n")
        raise click.Abort()
        
    console.print("[dim]First, adding wiki to qmd index...[/dim]")
    try:
        subprocess.run(["qmd", "collection", "add", str(wiki_dir / "wiki")], check=True)
    except subprocess.CalledProcessError:
        console.print("[yellow]Warning: Failed to add folder to qmd index. Proceeding to MCP anyway.[/yellow]")
        
    console.print("[bold green]Starting MCP Server...[/bold green] (Attach your agents here!)")
    try:
        # Run the qmd mcp server natively
        subprocess.run(["qmd", "mcp"])
    except OSError as e:
        console.print(f"[red]Failed to launch MCP server: {e}[/red]")
