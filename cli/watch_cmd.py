"""Automated file watcher command to ingest new files dropped into raw/ and run linting."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import click
from rich.console import Console
from rich.panel import Panel

from lib.providers import LLMProvider, LLMProviderError
from lib.wiki_ops import run_ingest, run_lint

console = Console()

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}


def get_raw_files_state(raw_dir: Path) -> Dict[str, tuple[float, int]]:
    """Return map of relative filename -> (mtime, size) for all supported files in raw/."""
    state = {}
    if not raw_dir.is_dir():
        return state

    for f in raw_dir.glob("**/*"):
        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            if any(p in f.parts for p in ("assets", "failed", "quarantine")):
                continue
            try:
                st = f.stat()
                rel = str(f.relative_to(raw_dir))
                state[rel] = (st.st_mtime, st.st_size)
            except OSError:
                continue
    return state


def get_already_ingested(wiki_dir: Path) -> Set[str]:
    """Scan wiki/log.md and wiki/sources/ to find files that have already been ingested."""
    ingested = set()

    from lib.utils import slugify

    sources_dir = wiki_dir / "wiki" / "sources"
    if sources_dir.is_dir():
        for f in sources_dir.glob("*.md"):
            ingested.add(f.stem)
            ingested.add(slugify(f.stem))

    log_file = wiki_dir / "wiki" / "log.md"
    if log_file.is_file():
        try:
            content = log_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("- Source: raw/"):
                    src_name = line.replace("- Source: raw/", "").strip()
                    ingested.add(src_name)
                    # handle extension stripping
                    stem = src_name.rsplit(".", 1)[0]
                    ingested.add(stem)
                    ingested.add(slugify(stem))
        except Exception:
            pass

    return ingested


def discover_wikis(base_dir: Optional[Path], wiki_path: Optional[Path]) -> List[Path]:
    """Find all valid wiki root directories from base_dir or single wiki_path."""
    wikis: List[Path] = []
    if wiki_path:
        w = wiki_path.resolve()
        if (w / "wiki").is_dir() and ((w / "AGENTS.md").is_file() or (w / "wiki.yaml").is_file()):
            wikis.append(w)
        return wikis

    if base_dir:
        b = base_dir.resolve()
        if b.is_dir():
            if (b / "wiki").is_dir() and ((b / "AGENTS.md").is_file() or (b / "wiki.yaml").is_file()):
                wikis.append(b)
                return wikis
            for entry in sorted(b.iterdir()):
                if entry.is_dir() and (entry / "wiki").is_dir() and ((entry / "AGENTS.md").is_file() or (entry / "wiki.yaml").is_file()):
                    wikis.append(entry)
    return wikis


@click.command("watch")
@click.option("--base-dir", "-b", default=None, type=click.Path(exists=True, file_okay=False), help="Path to base directory containing multiple wiki vaults (e.g. ~/wikis).")
@click.option("--wiki-path", "-w", default=None, type=click.Path(exists=True, file_okay=False), help="Path to a single wiki vault.")
@click.option("--interval", "-i", default=2.0, type=float, help="Polling interval in seconds (default: 2.0).")
@click.option("--yes", "-y", is_flag=True, default=False, help="Automatically approve and commit changes without interactive prompt.")
@click.option("--lint/--no-lint", default=True, help="Automatically run integrity audit after ingesting documents (default: True).")
@click.option("--lint-fix/--no-lint-fix", default=False, help="Automatically fix issues detected during post-ingest linting (default: False).")
@click.option("--lint-interval", default=0.0, type=float, help="Periodic interval in seconds to run full lint across vaults (0 to disable, default: 0).")
def watch_cmd(
    base_dir: Optional[str],
    wiki_path: Optional[str],
    interval: float,
    yes: bool,
    lint: bool,
    lint_fix: bool,
    lint_interval: float,
):
    """Automatically watch raw/ across wikis, compile newly added documents, and run linting."""
    base_p = Path(base_dir).resolve() if base_dir else None
    wiki_p = Path(wiki_path).resolve() if wiki_path else None

    # Fallback to current working directory if neither option is given
    if not base_p and not wiki_p:
        cwd = Path(".").resolve()
        if (cwd / "wiki").is_dir() and ((cwd / "AGENTS.md").is_file() or (cwd / "wiki.yaml").is_file()):
            wiki_p = cwd
        else:
            console.print("[red]Error:[/red] Please provide --base-dir or --wiki-path (or run from inside a valid wiki vault).")
            raise click.Abort()

    discovered = discover_wikis(base_p, wiki_p)
    if not discovered:
        console.print("[red]Error:[/red] No valid LLM Wiki vaults found.")
        raise click.Abort()

    try:
        provider = LLMProvider()
    except Exception as e:
        console.print(f"[red]Error initializing LLM Provider:[/red] {e}")
        return

    known_states: Dict[Path, Dict[str, tuple[float, int]]] = {}
    ingested_names_map: Dict[Path, Set[str]] = {}
    last_lint_times: Dict[Path, float] = {}
    failure_counts: Dict[tuple[Path, str], int] = {}
    retry_after: Dict[tuple[Path, str], float] = {}
    MAX_FAILURE_ATTEMPTS = 5

    for w_dir in discovered:
        raw_dir = w_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        known_states[w_dir] = get_raw_files_state(raw_dir)
        ingested_names_map[w_dir] = get_already_ingested(w_dir)
        last_lint_times[w_dir] = time.time()

    vault_names = ", ".join(w.name for w in discovered)
    target_desc = str(base_p) if base_p else str(wiki_p)
    banner = f"""
[bold cyan]LLM Wiki File Watcher Active[/bold cyan]

Watching:      [bold yellow]{target_desc}[/bold yellow]
Vaults ({len(discovered)}):   [bold]{vault_names}[/bold]
Mode:          [green]{"Auto-approve & commit" if yes else "Interactive review"}[/green]
Auto-Lint:     [cyan]{"Enabled" if lint else "Disabled"}[/cyan]{" (auto-fix)" if lint_fix else ""}
Interval:      {interval}s{" (Periodic lint: " + str(lint_interval) + "s)" if lint_interval > 0 else ""}

[dim]Drop any document (.md, .txt, .pdf) into raw/ in any watched vault to compile & lint automatically.[/dim]
[dim]Press Ctrl+C to stop watching.[/dim]
"""
    console.print(Panel(banner.strip(), border_style="cyan"))

    total_files = sum(len(s) for s in known_states.values())
    console.print(f"[dim]Tracking {total_files} existing file(s) across {len(discovered)} vault(s). Ready for new drops...[/dim]\n")

    try:
        while True:
            time.sleep(interval)

            # Dynamically refresh vaults if watching a base directory
            if base_p:
                current_wikis = discover_wikis(base_p, None)
                for w_dir in current_wikis:
                    if w_dir not in known_states:
                        raw_dir = w_dir / "raw"
                        raw_dir.mkdir(parents=True, exist_ok=True)
                        known_states[w_dir] = get_raw_files_state(raw_dir)
                        ingested_names_map[w_dir] = get_already_ingested(w_dir)
                        last_lint_times[w_dir] = time.time()
                        console.print(f"[bold green]✨ Discovered new vault:[/bold green] [bold cyan]{w_dir.name}[/bold cyan]")
            else:
                current_wikis = discovered

            for w_dir in current_wikis:
                raw_dir = w_dir / "raw"
                if not raw_dir.is_dir():
                    continue

                current_state = get_raw_files_state(raw_dir)
                known = known_states.get(w_dir, {})
                ingested = ingested_names_map.get(w_dir, set())

                new_or_modified = []
                for rel_path, (mtime, size) in current_state.items():
                    file_key = (w_dir, rel_path)
                    file_obj = raw_dir / rel_path
                    file_stem = file_obj.stem

                    # Check if file has exceeded failure threshold -> quarantine to raw/failed/
                    if failure_counts.get(file_key, 0) >= MAX_FAILURE_ATTEMPTS:
                        failed_dir = raw_dir / "failed"
                        failed_dir.mkdir(parents=True, exist_ok=True)
                        dest = failed_dir / file_obj.name
                        try:
                            file_obj.rename(dest)
                            console.print(
                                f"[bold red]⛔ Quarantined failing source after {MAX_FAILURE_ATTEMPTS} attempts:[/bold red] "
                                f"{file_obj.name} -> raw/failed/{file_obj.name}\n"
                            )
                        except Exception as q_err:
                            console.print(f"[bold red]Failed to quarantine {file_obj.name}:[/bold red] {q_err}\n")
                            retry_after[file_key] = time.time() + 3600.0
                        failure_counts.pop(file_key, None)
                        retry_after.pop(file_key, None)
                        continue

                    # Check backoff window
                    if time.time() < retry_after.get(file_key, 0.0):
                        continue

                    from lib.utils import slugify
                    is_ingested = (
                        file_stem in ingested
                        or file_obj.name in ingested
                        or slugify(file_stem) in ingested
                    )
                    if not is_ingested:
                        new_or_modified.append(file_obj)
                    elif rel_path in known and (mtime > known[rel_path][0] or size != known[rel_path][1]):
                        # File was modified by user: reset failure counter to provide fresh attempts
                        failure_counts.pop(file_key, None)
                        retry_after.pop(file_key, None)
                        new_or_modified.append(file_obj)

                known_states[w_dir] = current_state

                # Process detected files for this vault
                ingested_any = False
                for file_path in new_or_modified:
                    # Wait briefly to ensure file download/write completed (Syncthing safety)
                    prev_size = -1
                    prev_mtime = -1.0
                    for _ in range(15):
                        try:
                            st = file_path.stat()
                            cur_size = st.st_size
                            cur_mtime = st.st_mtime
                            if cur_size == prev_size and cur_mtime == prev_mtime and cur_size > 0:
                                break
                            prev_size = cur_size
                            prev_mtime = cur_mtime
                        except OSError:
                            pass
                        time.sleep(1.0)

                    console.print(f"\n[bold green]⚡ New source detected in {w_dir.name}:[/bold green] [bold cyan]{file_path.name}[/bold cyan]")
                    console.print(f"[dim]Starting automated compilation into {w_dir.name}...[/dim]")

                    rel_name = str(file_path.relative_to(raw_dir))
                    file_key = (w_dir, rel_name)

                    try:
                        applied = asyncio.run(run_ingest(
                            source_file=file_path,
                            wiki_dir=w_dir,
                            provider=provider,
                            auto_approve=yes,
                        ))
                        if applied:
                            ingested.add(file_path.stem)
                            ingested.add(file_path.name)
                            failure_counts.pop(file_key, None)
                            retry_after.pop(file_key, None)
                            console.print(
                                f"[bold green]✓ Compiled and committed {len(applied)} wiki page(s) for {file_path.name} in {w_dir.name}![/bold green]\n"
                            )
                            ingested_any = True
                        else:
                            count = failure_counts.get(file_key, 0) + 1
                            failure_counts[file_key] = count
                            backoff = min(300.0, 5.0 * (2 ** (count - 1)))
                            retry_after[file_key] = time.time() + backoff
                            console.print(
                                f"[yellow]No modifications were applied for {file_path.name}. "
                                f"Backing off {int(backoff)}s (attempt {count}/{MAX_FAILURE_ATTEMPTS})[/yellow]\n"
                            )
                    except LLMProviderError as e:
                        count = failure_counts.get(file_key, 0) + 1
                        failure_counts[file_key] = count
                        backoff = min(300.0, 5.0 * (2 ** (count - 1)))
                        retry_after[file_key] = time.time() + backoff
                        console.print(f"[bold red]LLM Provider Error in {w_dir.name}:[/bold red] {e}")
                        console.print(
                            f"[yellow]Will retry {file_path.name} in {int(backoff)}s "
                            f"(attempt {count}/{MAX_FAILURE_ATTEMPTS})[/yellow]\n"
                        )
                    except Exception as e:
                        count = failure_counts.get(file_key, 0) + 1
                        failure_counts[file_key] = count
                        backoff = min(300.0, 5.0 * (2 ** (count - 1)))
                        retry_after[file_key] = time.time() + backoff
                        console.print(f"[bold red]Ingestion error for {file_path.name} in {w_dir.name}:[/bold red] {e}")
                        console.print(
                            f"[yellow]Will retry {file_path.name} in {int(backoff)}s "
                            f"(attempt {count}/{MAX_FAILURE_ATTEMPTS})[/yellow]\n"
                        )

                # If any files were ingested and lint is enabled, run post-ingest linting!
                if ingested_any and lint:
                    console.print(f"[bold cyan]🔍 Running automatic post-ingestion lint for {w_dir.name}...[/bold cyan]")
                    try:
                        report = asyncio.run(run_lint(
                            wiki_dir=w_dir,
                            provider=provider,
                            fix=lint_fix,
                            auto_approve=yes,
                        ))
                        broken = len(report.get("broken_links", []))
                        orphans = len(report.get("orphan_pages", []))
                        semantic = len(report.get("semantic_issues", []))
                        console.print(
                            f"[bold green]✓ Post-ingestion lint completed for {w_dir.name}:[/bold green] "
                            f"{broken} broken link(s), {orphans} orphan(s), {semantic} semantic issue(s).\n"
                        )
                        last_lint_times[w_dir] = time.time()
                    except Exception as e:
                        console.print(f"[bold yellow]⚠️ Post-ingestion lint warning for {w_dir.name}:[/bold yellow] {e}\n")

                # Scheduled periodic lint check if enabled
                if lint_interval > 0 and (time.time() - last_lint_times.get(w_dir, 0)) >= lint_interval:
                    console.print(f"\n[dim]Running scheduled periodic lint audit on {w_dir.name}...[/dim]")
                    try:
                        report = asyncio.run(run_lint(
                            wiki_dir=w_dir,
                            provider=provider,
                            fix=lint_fix,
                            auto_approve=yes,
                        ))
                        broken = len(report.get("broken_links", []))
                        orphans = len(report.get("orphan_pages", []))
                        semantic = len(report.get("semantic_issues", []))
                        console.print(
                            f"[bold green]✓ Periodic lint completed for {w_dir.name}:[/bold green] "
                            f"{broken} broken link(s), {orphans} orphan(s), {semantic} semantic issue(s).\n"
                        )
                        last_lint_times[w_dir] = time.time()
                    except Exception as e:
                        console.print(f"[bold yellow]⚠️ Periodic lint warning for {w_dir.name}:[/bold yellow] {e}\n")

    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Watcher stopped by user.[/yellow]")
