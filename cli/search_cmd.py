"""CLI command for instant zero-LLM search across wiki vaults."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lib.config import load_wiki_config
from lib.utils import parse_frontmatter, resolve_vault_path, list_available_vaults, slugify

console = Console()


def _lexical_search_vault(wiki_dir: Path, query_terms: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    """Fast lexical/regex fallback search across markdown pages in a vault."""
    wiki_folder = wiki_dir / "wiki"
    if not wiki_folder.is_dir():
        return []

    hits: List[Dict[str, Any]] = []
    lower_terms = [t.lower() for t in query_terms if t]

    for md_file in wiki_folder.rglob("*.md"):
        if ".llm-wiki" in md_file.parts or md_file.name.startswith("."):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(content)
            title = fm.get("title", md_file.stem.replace("-", " ").title())
            tags = fm.get("tags", [])
            tags_str = " ".join(tags) if isinstance(tags, list) else str(tags)

            searchable = f"{title}\n{tags_str}\n{body}".lower()
            score = 0
            for term in lower_terms:
                if term in title.lower():
                    score += 10
                if term in tags_str.lower():
                    score += 5
                matches = len(re.findall(re.escape(term), searchable))
                score += min(matches, 5)

            if score > 0:
                # Find best matching snippet from body
                best_snippet = ""
                for line in body.splitlines():
                    clean_l = line.strip()
                    if clean_l and not clean_l.startswith("#") and any(t in clean_l.lower() for t in lower_terms):
                        best_snippet = clean_l
                        break
                if not best_snippet:
                    for line in body.splitlines():
                        clean_l = line.strip()
                        if clean_l and not clean_l.startswith("#"):
                            best_snippet = clean_l[:200]
                            break

                hits.append({
                    "wiki": wiki_dir.name,
                    "title": title,
                    "path": str(md_file.relative_to(wiki_dir)),
                    "snippet": best_snippet[:300],
                    "score": score,
                    "distance": round(1.0 / (1.0 + score), 3),
                })
        except Exception:
            pass

    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:limit]


@click.command("search")
@click.argument("query", required=True)
@click.option("--wiki-path", "-w", default=".", type=str, help="Path or name of wiki vault.")
@click.option("--all", "-a", "search_all", is_flag=True, help="Search across all discovered vaults in ~/wikis.")
@click.option("--limit", "-n", default=5, type=int, help="Maximum number of results to display (default: 5).")
def search_cmd(query: str, wiki_path: str, search_all: bool, limit: int):
    """Fast, instant search across wiki notes with zero LLM API calls and sub-100ms latency."""
    terms = query.strip().split()
    if not terms:
        console.print("[red]Error:[/red] Please provide a valid search query.")
        return

    results: List[Dict[str, Any]] = []

    if search_all:
        from lib.mcp_router import WikiRouterManager
        base_dir = Path("/home/ubuntu/wikis")
        if not base_dir.is_dir():
            base_dir = Path(".").resolve().parent
        try:
            manager = WikiRouterManager(base_dir=base_dir)
            federated = manager.search_all_wikis(query, limit_per_wiki=limit)
            raw_results = federated.get("results", [])
            if raw_results:
                results.extend(raw_results)
        except Exception:
            pass

        # Fallback to lexical search across all available vaults if vector results are empty
        if not results:
            for v in list_available_vaults():
                results.extend(_lexical_search_vault(v, terms, limit=limit))
    else:
        resolved = resolve_vault_path(wiki_path, query=query)
        target_dir = resolved if resolved else Path(wiki_path).resolve()
        if not (target_dir / "wiki").is_dir():
            available = list_available_vaults()
            if available:
                console.print(f"[yellow]'{wiki_path}' is not a wiki vault. Searching available vaults instead:[/yellow]")
                for v in available:
                    results.extend(_lexical_search_vault(v, terms, limit=limit))
            else:
                console.print(f"[red]Error:[/red] No valid wiki found at '{wiki_path}'.")
                return
        else:
            # Try vector store first
            try:
                from lib.vector_store import WikiVectorStore
                from lib.providers import LLMProvider
                store = WikiVectorStore(target_dir, LLMProvider())
                raw_vector = store.search_similar_with_metadata(query, k=limit)
                for r in raw_vector:
                    content = r.get("content", "").strip()
                    results.append({
                        "wiki": target_dir.name,
                        "title": r.get("title", Path(r.get("path", "")).stem),
                        "path": r.get("path", ""),
                        "snippet": content[:300] + "..." if len(content) > 300 else content,
                        "distance": r.get("distance"),
                    })
            except Exception:
                pass

            # Fallback to fast lexical scan if vector store returned nothing
            if not results:
                results = _lexical_search_vault(target_dir, terms, limit=limit)

    if not results:
        console.print(f"[yellow]No matching notes found for query:[/yellow] [bold]\"{query}\"[/bold]")
        return

    # Sort results
    results.sort(key=lambda x: (x.get("distance") is None, x.get("distance", 1.0)))
    results = results[:limit]

    console.print(f"\n[bold green]Found {len(results)} match(es) for:[/bold green] [bold cyan]\"{query}\"[/bold cyan]\n")

    for idx, hit in enumerate(results, 1):
        wiki_label = f"[dim][{hit.get('wiki', '')}][/dim] " if search_all else ""
        dist = hit.get("distance")
        score_str = f" (dist: {dist:.3f})" if dist is not None else ""
        title = hit.get("title") or Path(hit.get("path", "")).stem
        path_str = hit.get("path", "")
        snippet = hit.get("snippet", "").strip()

        panel_content = f"[bold]{wiki_label}{title}[/bold]{score_str}\n[dim]{path_str}[/dim]\n\n{snippet}"
        console.print(Panel(panel_content, border_style="cyan"))
