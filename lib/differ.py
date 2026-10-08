"""Diff display and semi-automatic human-in-the-loop review engine."""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from typing import List, Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
except ImportError:
    Console = None  # type: ignore


@dataclass
class FileChange:
    path: str
    operation: str  # "create" | "update" | "delete"
    new_content: str
    old_content: Optional[str] = None
    reason: str = ""


def _render_unified_diff(old_text: str, new_text: str, file_path: str) -> str:
    """Generate unified diff string between old and new text."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )
    return "".join(diff)


def print_change_summary(changes: List[FileChange], console: Optional[Console] = None):
    """Print a clean high-level summary of proposed file modifications."""
    if not console:
        console = Console()

    table = Table(title="Proposed Wiki Changes", show_header=True, header_style="bold cyan")
    table.add_column("Operation", style="bold", width=12)
    table.add_column("File Path", style="cyan")
    table.add_column("Reason / Notes", style="dim")

    for change in changes:
        op_style = "green" if change.operation == "create" else "yellow" if change.operation == "update" else "red"
        table.add_row(f"[{op_style}]{change.operation.upper()}[/{op_style}]", change.path, change.reason or "-")

    console.print(table)


def review_changes(
    changes: List[FileChange],
    auto_approve: bool = False,
    console: Optional[Console] = None,
) -> List[FileChange]:
    """Present proposed changes to the human reviewer and return the approved subset.

    Options:
        [a] Approve all changes
        [r] Review and decide file-by-file
        [s] Skip / cancel all changes
    """
    if not changes:
        return []

    if auto_approve:
        return changes

    if not console:
        console = Console()

    print_change_summary(changes, console)

    console.print("\n[bold yellow]Semi-Automated Review:[/bold yellow]")
    console.print("  [bold green][a][/bold green] Approve all proposed changes")
    console.print("  [bold blue][r][/bold blue] Review step-by-step (diff by diff)")
    console.print("  [bold red][s][/bold red] Skip / Reject all proposed changes")

    if not sys.stdin.isatty():
        console.print("\n[yellow]Non-interactive environment detected. Skipping changes.[/yellow]")
        return []

    try:
        choice = input("\nYour choice [a/r/s] (default 's'): ").strip().lower() or "s"
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Review aborted by user.[/yellow]")
        return []

    if choice == "s":
        console.print("[red]All proposed changes rejected.[/red]")
        return []

    if choice == "a":
        console.print("[green]All proposed changes approved.[/green]")
        return changes

    # File-by-file review
    approved: List[FileChange] = []
    for idx, change in enumerate(changes, start=1):
        console.print(f"\n[bold cyan]({idx}/{len(changes)}) File:[/bold cyan] {change.path} [{change.operation}]")
        if change.reason:
            console.print(f"[dim]Rationale: {change.reason}[/dim]")

        if change.operation == "create":
            preview = change.new_content[:500] + ("\n... (truncated)" if len(change.new_content) > 500 else "")
            console.print(Panel(preview, title=f"New File: {change.path}", border_style="green"))
        elif change.operation == "update":
            diff_text = _render_unified_diff(change.old_content or "", change.new_content, change.path)
            console.print(Panel(diff_text or "No textual difference", title=f"Diff: {change.path}", border_style="yellow"))
        elif change.operation == "delete":
            console.print(Panel(f"Marked for deletion: {change.path}", border_style="red"))

        try:
            sub_choice = input(f"Approve this change? [y/n] (default 'y'): ").strip().lower() or "y"
        except (EOFError, KeyboardInterrupt):
            break

        if sub_choice == "y":
            approved.append(change)
            console.print("[green]✓ Approved[/green]")
        else:
            console.print("[red]✗ Skipped[/red]")

    return approved
