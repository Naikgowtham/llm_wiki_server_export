"""Command to export the wiki to llm.txt standard format."""

from pathlib import Path

import click
from rich.console import Console

from lib.wiki_ops import get_template

console = Console()

@click.command("export")
@click.option("--wiki-path", "-w", default=".", type=click.Path(exists=True, file_okay=False), help="Path to wiki vault.")
@click.option("--format", "-f", default="llm.txt", type=click.Choice(["llm.txt"]), help="Export format.")
def export_cmd(wiki_path: str, format: str):
    """Export the entire wiki to a single optimized file for AI context."""
    wiki_dir = Path(wiki_path).resolve()
    
    if not (wiki_dir / "wiki").is_dir():
        console.print(f"[red]Error:[/red] {wiki_dir} is not a valid LLM Wiki instance.")
        raise click.Abort()
        
    out_file = wiki_dir / "llm.txt"
    if out_file.exists():
        if not click.confirm(f"{out_file.name} already exists. Overwrite?"):
            raise click.Abort()
            
    # Check if wiki is empty
    md_files = [f for f in (wiki_dir / "wiki").rglob("*.md") if f.name not in ("index.md", "log.md", "overview.md")]
    if not md_files:
        console.print("[yellow]Warning: The wiki has no content pages to export.[/yellow]")
        raise click.Abort()
        
    console.print(f"Exporting {len(md_files)} pages from {wiki_dir.name} to {out_file.name}...")
    
    with open(out_file, "w", encoding="utf-8") as out:
        out.write(f"# {wiki_dir.name} Knowledge Base\n\n")
        # Write index first
        index_file = wiki_dir / "wiki" / "index.md"
        if index_file.exists():
            out.write("## Wiki Index\n")
            out.write(index_file.read_text(encoding="utf-8"))
            out.write("\n\n---\n\n")
            
        # Write all notes
        exported_count = 0
        for md_file in sorted(md_files):
            # Read content and strip basic frontmatter
            content = md_file.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            
            # Simple conversion of [[wikilinks]] to plain text for external LLMs
            import re
            content = re.sub(r"\[\[(.*?)\]\]", r"\1", content)
            
            out.write(f"## Document: {md_file.stem}\n")
            out.write(content)
            out.write("\n\n---\n\n")
            exported_count += 1
            
    console.print(f"[bold green]✓ Export complete: {exported_count} pages written to {out_file}[/bold green]")
