"""CLI command to initialize a new LLM Wiki instance from templates."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel

from lib.utils import slugify, today_str

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
console = Console()


def render_template_file(file_path: Path, variables: dict):
    """Replace {{KEY}} markers in a template file with actual values."""
    if not file_path.is_file():
        return
    try:
        content = file_path.read_text(encoding="utf-8")
        for key, val in variables.items():
            content = content.replace(f"{{{{{key}}}}}", str(val))
            content = content.replace(f"{{{{ {key} }}}}", str(val))
        file_path.write_text(content, encoding="utf-8")
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to render {file_path.name}: {e}[/yellow]")


@click.command("init")
@click.argument("target_path", type=click.Path(file_okay=False, dir_okay=True))
@click.option("--domain", "-d", prompt="Domain Name (e.g. 'AI Agents & Tools')", help="Human-readable domain name.")
@click.option("--description", prompt="Short description", default="LLM-maintained knowledge base", help="Domain overview.")
@click.option("--slug", default=None, help="Short slug (defaults to kebab-case of domain).")
def init_cmd(target_path: str, domain: str, description: str, slug: Optional[str]):
    """Initialize a brand-new LLM Wiki Obsidian vault from templates."""
    dest = Path(target_path).resolve()
    slug_val = slug or slugify(domain)
    today = today_str()

    if dest.exists() and any(dest.iterdir()):
        if not click.confirm(f"Destination directory {dest} already exists and is not empty. Continue?"):
            console.print("[red]Aborted.[/red]")
            return

    dest.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[cyan]Scaffolding new LLM Wiki at:[/cyan] [bold]{dest}[/bold]")
    console.print(f"[dim]Domain: {domain} | Slug: {slug_val}[/dim]\n")

    # 1. Copy entire template tree
    for item in TEMPLATES_DIR.iterdir():
        dest_item = dest / item.name
        if item.is_dir():
            shutil.copytree(item, dest_item, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest_item)

    # 2. Template variable substitutions
    vars_dict = {
        "DOMAIN_NAME": domain,
        "DOMAIN_SLUG": slug_val,
        "DOMAIN_DESCRIPTION": description,
        "date": today,
    }

    render_template_file(dest / "AGENTS.md", vars_dict)
    render_template_file(dest / "wiki.yaml", vars_dict)
    render_template_file(dest / "README.md", vars_dict)
    render_template_file(dest / "wiki" / "index.md", vars_dict)
    render_template_file(dest / "wiki" / "log.md", vars_dict)
    render_template_file(dest / "wiki" / "overview.md", vars_dict)

    # 3. Initialize Git repository
    git_status = "Not initialized"
    try:
        import git
        repo = git.Repo.init(dest)
        repo.git.add(A=True)
        repo.index.commit(f"init: bootstrap {domain} LLM wiki")
        git_status = "[green]Initialized with initial commit[/green]"
    except Exception as e:
        git_status = f"[yellow]Git not initialized ({e})[/yellow]"

    success_msg = f"""
[bold green]✓ LLM Wiki Successfully Created![/bold green]

[bold]Location:[/bold] {dest}
[bold]Git:[/bold] {git_status}

[bold cyan]Next Steps:[/bold cyan]
1. Open [bold]Obsidian[/bold] → "Open folder as vault" → select:
   [bold yellow]{dest}[/bold yellow]
2. Drop research articles or documents into [bold]raw/[/bold]
3. Run ingest:
   [bold white]wiki ingest raw/document.md --wiki-path "{dest}"[/bold white]
4. Query your new knowledge base:
   [bold white]wiki query "Summary of core findings" --wiki-path "{dest}"[/bold white]
"""
    console.print(Panel(success_msg.strip(), title=f"Wiki Ready: {domain}", border_style="green"))
