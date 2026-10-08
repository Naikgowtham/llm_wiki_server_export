"""CLI command to audit wiki health and consistency."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

import click
from rich.console import Console
from rich.table import Table

from lib.providers import LLMProvider
from lib.wiki_ops import run_lint

console = Console()


def discover_wikis(base_dir: Optional[Path], wiki_path: Optional[str]) -> List[Path]:
    """Find all valid wiki root directories from base_dir or single wiki_path."""
    wikis: List[Path] = []
    if wiki_path:
        from lib.utils import resolve_vault_path
        resolved = resolve_vault_path(wiki_path)
        if resolved and (resolved / "wiki").is_dir():
            wikis.append(resolved)
            return wikis
        w = Path(wiki_path).resolve()
        if (w / "wiki").is_dir():
            wikis.append(w)
        return wikis

    if base_dir:
        b = base_dir.resolve()
        if b.is_dir():
            if (b / "wiki").is_dir():
                wikis.append(b)
                return wikis
            for entry in sorted(b.iterdir()):
                if entry.is_dir() and (entry / "wiki").is_dir():
                    wikis.append(entry)
        return wikis

    # Auto-discovery fallback
    curr = Path(".").resolve()
    if (curr / "wiki").is_dir():
        wikis.append(curr)
    else:
        from lib.utils import list_available_vaults
        wikis.extend(list_available_vaults())

    return wikis


def audit_single_wiki(
    wiki_dir: Path,
    provider: Optional[LLMProvider],
    fix: bool,
    yes: bool,
    reset: bool,
    max_pages: Optional[int] = None,
    staged: bool = False,
):
    """Run integrity audit on a single wiki vault."""
    console.print(f"\n[cyan]Running integrity audit on:[/cyan] [bold]{wiki_dir.name}[/bold]...\n")

    report = asyncio.run(run_lint(
        wiki_dir=wiki_dir,
        provider=provider,
        fix=fix,
        auto_approve=yes,
        reset=reset,
        max_pages=max_pages,
        staged_only=staged,
    ))

    broken_links = report.get("broken_links", [])
    orphan_pages = report.get("orphan_pages", [])
    mechanical_issues = report.get("mechanical_issues", [])
    semantic_issues = report.get("semantic_issues", [])

    # Display results
    table = Table(title=f"Wiki Audit Summary — {wiki_dir.name}", show_header=True, header_style="bold magenta")
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Status")

    table.add_row(
        "Broken [[links]]",
        str(len(broken_links)),
        "[green]Clean[/green]" if not broken_links else f"[red]{len(broken_links)} broken target(s)[/red]",
    )

    from lib.config import load_wiki_config
    config = load_wiki_config(wiki_dir)
    max_orphans = config.lint_settings.get("max_orphan_tolerance", 5)

    orphan_status = "[green]Clean[/green]"
    if len(orphan_pages) > max_orphans:
        orphan_status = f"[red]{len(orphan_pages)} unlinked note(s) (Limit: {max_orphans})[/red]"
    elif orphan_pages:
        orphan_status = f"[yellow]{len(orphan_pages)} unlinked note(s)[/yellow]"

    table.add_row(
        "Orphan Notes",
        str(len(orphan_pages)),
        orphan_status,
    )
    table.add_row(
        "Mechanical Issues (Zero-Token)",
        str(len(mechanical_issues)),
        "[green]Clean[/green]" if not mechanical_issues else f"[yellow]{len(mechanical_issues)} flagged[/yellow]",
    )
    table.add_row(
        "Semantic Issues (LLM Audit)",
        str(len(semantic_issues)),
        "[green]Clean[/green]" if not semantic_issues else f"[yellow]{len(semantic_issues)} flagged[/yellow]",
    )

    console.print(table)

    from rich.markup import escape
    if broken_links:
        console.print("\n[bold red]Broken Wikilinks:[/bold red]")
        for b in broken_links[:10]:
            console.print(f"  • {escape(b)}")
        if len(broken_links) > 10:
            console.print(f"  ... and {len(broken_links) - 10} more.")

    if orphan_pages:
        console.print("\n[bold yellow]Orphan Pages (0 inbound links):[/bold yellow]")
        for o in orphan_pages[:10]:
            console.print(f"  • [[{escape(o)}]]")
        if len(orphan_pages) > 10:
            console.print(f"  ... and {len(orphan_pages) - 10} more.")

    if mechanical_issues:
        console.print("\n[bold yellow]Mechanical Checks (Zero-Token Python):[/bold yellow]")
        for issue in mechanical_issues[:10]:
            cat = escape(issue.get("category", "mechanical"))
            file_p = escape(issue.get("file_path", ""))
            desc = escape(issue.get("description", ""))
            console.print(f"  • [[{file_p}]] [{cat}]: {desc}")
        if len(mechanical_issues) > 10:
            console.print(f"  ... and {len(mechanical_issues) - 10} more.")

    if semantic_issues:
        console.print("\n[bold magenta]Semantic Audit Issues:[/bold magenta]")
        for issue in semantic_issues[:10]:
            cat = escape(issue.get("category", "issue"))
            file_p = escape(issue.get("file_path", ""))
            desc = escape(issue.get("description", ""))
            console.print(f"  • [[{file_p}]] [{cat}]: {desc}")
        if len(semantic_issues) > 10:
            console.print(f"  ... and {len(semantic_issues) - 10} more.")

    if not broken_links and not orphan_pages and not mechanical_issues and not semantic_issues:
        console.print(f"\n[bold green]✓ All checks passed for {wiki_dir.name}! Wiki integrity is in excellent health.[/bold green]")
        return True

    if staged and (broken_links or mechanical_issues):
        console.print(f"\n[bold red]❌ Micro-lint failed for {wiki_dir.name} on staged note(s).[/bold red]")
        return False

    return True


@click.command("lint")
@click.option("--base-dir", "-b", default=None, type=click.Path(exists=True, file_okay=False), help="Path to base directory containing multiple wiki vaults.")
@click.option("--wiki-path", "-w", default=None, type=str, help="Path or name of a wiki vault (e.g. ai-wiki or ~/wikis/ai-wiki).")
@click.option("--fix", is_flag=True, help="Automatically propose and apply fixes for discovered issues.")
@click.option("--yes", "-y", is_flag=True, help="Auto-approve all proposed fixes without interactive prompt.")
@click.option("--reset", is_flag=True, help="Clear lint state and force a full re-lint of all pages.")
@click.option("--max-pages", "-m", default=None, type=int, help="Limit number of pages to audit (prioritizes low-confidence notes).")
@click.option("--staged", "-s", is_flag=True, help="Only lint pages staged in git (fast pre-commit micro-lint).")
def lint_cmd(
    base_dir: Optional[str],
    wiki_path: Optional[str],
    fix: bool,
    yes: bool,
    reset: bool,
    max_pages: Optional[int],
    staged: bool,
):
    """Audit wiki integrity: check for broken links, orphan notes, and contradictions."""
    base_p = Path(base_dir).resolve() if base_dir else None

    discovered = discover_wikis(base_p, wiki_path)
    if not discovered:
        target_name = str(base_p) if base_p else str(wiki_path)
        console.print(f"[red]Error:[/red] {target_name} is not a valid wiki instance (missing wiki/ folder).")
        raise click.Abort()

    provider = None
    try:
        provider = LLMProvider()
    except Exception:
        console.print("[dim]Note: LLM provider not configured. Running structural audit only.[/dim]")

    has_failure = False
    for w_dir in discovered:
        success = audit_single_wiki(w_dir, provider, fix, yes, reset, max_pages=max_pages, staged=staged)
        if not success:
            has_failure = True

    if staged and has_failure:
        import sys
        sys.exit(1)
