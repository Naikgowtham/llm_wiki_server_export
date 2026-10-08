"""Multi-Wiki MCP Router core library for discovery, caching, querying, and safe contributions."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lib.config import WikiConfig, load_wiki_config
from lib.providers import LLMProvider, RateLimitCooldownTracker
from lib.utils import build_link_graph, extract_wikilinks, parse_frontmatter, slugify, today_str
from lib.vector_store import WikiVectorStore

logger = logging.getLogger(__name__)


def resolve_page_path(wiki_dir: Path, page_slug: str) -> Optional[Path]:
    """Resolve a page slug to a real file path inside wiki/ with fuzzy fallback and path traversal safety."""
    wiki_folder = (wiki_dir / "wiki").resolve()
    if not wiki_folder.is_dir():
        return None

    target = page_slug.strip()
    if not target.lower().endswith(".md"):
        target_with_ext = f"{target}.md"
    else:
        target_with_ext = target

    # 1. Direct path relative to wiki/
    direct_path = (wiki_folder / target_with_ext).resolve()
    if direct_path.is_file() and direct_path.is_relative_to(wiki_folder):
        return direct_path

    direct_raw = (wiki_folder / target).resolve()
    if direct_raw.is_file() and direct_raw.is_relative_to(wiki_folder):
        return direct_raw

    # 2. Base name / stem matching across wiki/
    base_name = Path(target_with_ext).name.lower()
    base_stem = Path(target).stem.lower()

    for md_file in wiki_folder.rglob("*.md"):
        if md_file.name.lower() == base_name or md_file.stem.lower() == base_stem:
            return md_file

    return None


class WikiRouterManager:
    """Manages multi-wiki discovery, caching, and read/query operations."""

    def __init__(self, base_dir: Path, provider: Optional[LLMProvider] = None):
        self.base_dir = Path(base_dir).resolve()
        self.provider = provider or LLMProvider()
        self._store_cache: Dict[str, WikiVectorStore] = {}
        self._config_cache: Dict[str, WikiConfig] = {}

    def resolve_wiki(self, wiki_name: str) -> Optional[Path]:
        """Resolve a wiki folder by name inside base_dir, ensuring security boundaries."""
        if not wiki_name or "/" in wiki_name or "\\" in wiki_name or ".." in wiki_name:
            return None
        target = self.base_dir / wiki_name
        if not target.is_dir():
            return None
        if not (target / "wiki").is_dir():
            return None
        return target

    def get_wiki_config(self, wiki_name: str) -> WikiConfig:
        """Get or cache WikiConfig for a given vault."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        if wiki_name not in self._config_cache:
            self._config_cache[wiki_name] = load_wiki_config(wiki_dir)
        return self._config_cache[wiki_name]

    def get_vector_store(self, wiki_name: str) -> WikiVectorStore:
        """Get or initialize cached WikiVectorStore for a given vault."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        if wiki_name not in self._store_cache:
            self._store_cache[wiki_name] = WikiVectorStore(wiki_dir, self.provider)
        return self._store_cache[wiki_name]

    def list_wikis(self) -> List[Dict[str, Any]]:
        """Scan base_dir dynamically and return summaries for all discovered wikis."""
        wikis: List[Dict[str, Any]] = []
        if not self.base_dir.is_dir():
            return wikis

        for entry in sorted(self.base_dir.iterdir()):
            if entry.is_dir() and (entry / "wiki").is_dir():
                try:
                    cfg = load_wiki_config(entry)
                    page_count = len(list((entry / "wiki").rglob("*.md")))
                    wikis.append({
                        "name": entry.name,
                        "description": cfg.domain_description,
                        "page_count": page_count,
                    })
                except Exception as e:
                    logger.warning("Error inspecting wiki folder %s: %s", entry.name, e)
                    wikis.append({
                        "name": entry.name,
                        "description": "LLM-maintained knowledge base",
                        "page_count": len(list((entry / "wiki").rglob("*.md"))),
                    })
        return wikis

    def get_wiki_skeleton(self, wiki_name: str, category: Optional[str] = None) -> Dict[str, Any]:
        """Return table of contents / skeleton of pages grouped by category."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        wiki_folder = wiki_dir / "wiki"
        categories: Dict[str, List[str]] = {}

        # Standard subfolders inside wiki/
        known_dirs = ["concepts", "entities", "topics", "comparisons", "sources", "queries"]
        for kd in known_dirs:
            folder = wiki_folder / kd
            if folder.is_dir():
                pages = [f.stem for f in sorted(folder.glob("*.md"))]
                if pages:
                    categories[kd] = pages

        # Any other top-level directories
        for sub in sorted(wiki_folder.iterdir()):
            if sub.is_dir() and sub.name not in known_dirs and not sub.name.startswith("."):
                pages = [f.stem for f in sorted(sub.glob("*.md"))]
                if pages:
                    categories[sub.name] = pages

        # Root pages (overview, index, log)
        root_pages = [f.stem for f in sorted(wiki_folder.glob("*.md"))]
        if root_pages:
            categories["root"] = root_pages

        if category:
            cat_clean = category.lower().strip()
            return {
                "wiki_name": wiki_name,
                "categories": {cat_clean: categories.get(cat_clean, [])},
            }

        return {
            "wiki_name": wiki_name,
            "categories": categories,
        }

    def get_wiki_status(self, wiki_name: str) -> Dict[str, Any]:
        """Return structural metrics and category page counts."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        wiki_folder = wiki_dir / "wiki"
        raw_folder = wiki_dir / "raw"

        def _count(sub: str) -> int:
            p = wiki_folder / sub
            return len(list(p.glob("*.md"))) if p.is_dir() else 0

        raw_sources = 0
        if raw_folder.is_dir():
            for f in raw_folder.rglob("*"):
                if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf"):
                    raw_sources += 1

        total_pages = len(list(wiki_folder.rglob("*.md")))

        return {
            "wiki_name": wiki_name,
            "total_pages": total_pages,
            "raw_sources": raw_sources,
            "concepts": _count("concepts"),
            "entities": _count("entities"),
            "topics": _count("topics"),
            "comparisons": _count("comparisons"),
            "last_updated": today_str(),
        }

    def read_wiki_page(self, wiki_name: str, page_slug: str) -> str:
        """Fetch raw markdown text for a page with flexible resolution."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        page_file = resolve_page_path(wiki_dir, page_slug)
        if not page_file or not page_file.is_file():
            raise FileNotFoundError(f"Page '{page_slug}' not found in wiki '{wiki_name}'.")

        return page_file.read_text(encoding="utf-8")

    def get_page_links(self, wiki_name: str, page_slug: str) -> Dict[str, Any]:
        """Return outgoing [[wikilinks]] and incoming backlinks for a note."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        page_file = resolve_page_path(wiki_dir, page_slug)
        if not page_file or not page_file.is_file():
            raise FileNotFoundError(f"Page '{page_slug}' not found in wiki '{wiki_name}'.")

        content = page_file.read_text(encoding="utf-8")
        outgoing = sorted(list(set(extract_wikilinks(content))))

        # Build full link graph to compute backlinks
        link_graph = build_link_graph(wiki_dir / "wiki")
        page_stem = page_file.stem
        backlinks: List[str] = []

        clean_stem = slugify(page_stem)
        for src, targets in link_graph.items():
            if src.lower() == page_stem.lower() or slugify(src) == clean_stem:
                continue
            if any(
                t.lower() == page_stem.lower()
                or slugify(t) == clean_stem
                or t.lower() == page_slug.lower()
                or slugify(t) == slugify(page_slug)
                for t in targets
            ):
                backlinks.append(src)

        return {
            "page": page_stem,
            "outgoing_links": outgoing,
            "backlinks": sorted(list(set(backlinks))),
        }

    def get_entity_info(self, wiki_name: str, entity_name: str) -> Dict[str, Any]:
        """Return deep metadata, sources list, and summary for an entity/concept."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        page_file = resolve_page_path(wiki_dir, entity_name)
        if not page_file or not page_file.is_file():
            raise FileNotFoundError(f"Entity/Concept '{entity_name}' not found in wiki '{wiki_name}'.")

        content = page_file.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(content)

        # Extract first non-heading paragraph as summary
        summary = ""
        for line in body.splitlines():
            line_s = line.strip()
            if line_s and not line_s.startswith("#") and not line_s.startswith("---"):
                summary = line_s
                break

        # Citations / sources
        sources = fm.get("sources", [])
        if isinstance(sources, str):
            sources = [sources]

        links_data = self.get_page_links(wiki_name, entity_name)

        return {
            "entity": page_file.stem,
            "type": fm.get("type", "unknown"),
            "confidence": fm.get("confidence", "unknown"),
            "created": fm.get("created", ""),
            "updated": fm.get("updated", ""),
            "tags": fm.get("tags", []),
            "sources": sources,
            "summary": summary,
            "outgoing_links": links_data["outgoing_links"],
            "backlinks": links_data["backlinks"],
        }

    async def query_wiki(self, wiki_name: str, query: str) -> str:
        """Execute semantic RAG query against the specified wiki."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        store = self.get_vector_store(wiki_name)
        from lib.wiki_ops import run_query

        answer, _ = await run_query(
            question=query,
            wiki_dir=wiki_dir,
            provider=self.provider,
            file_back=False,
            store=store,
        )
        return answer

    def add_inbox_note(self, wiki_name: str, title: str, content: str, tags: Optional[List[str]] = None) -> str:
        """Safely record an inbox note into raw/inbox/ avoiding Syncthing merge conflicts."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        inbox_dir = wiki_dir / "raw" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)

        date_prefix = today_str()
        slug = slugify(title) or "note"
        filename = f"{date_prefix}-{slug}.md"
        target_path = inbox_dir / filename

        counter = 1
        while target_path.exists():
            filename = f"{date_prefix}-{slug}-{counter}.md"
            target_path = inbox_dir / filename
            counter += 1

        tags_yaml = "\n".join([f"  - {t}" for t in (tags or [])])
        tags_block = f"\ntags:\n{tags_yaml}" if tags else "\ntags: []"

        file_body = (
            f"---\n"
            f"title: \"{title}\"\n"
            f"created: \"{date_prefix}\"\n"
            f"type: source{tags_block}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"{content.strip()}\n"
        )

        target_path.write_text(file_body, encoding="utf-8")
        return f"Successfully created inbox note at raw/inbox/{filename}"

    def append_to_log(self, wiki_name: str, entry_text: str) -> str:
        """Append a timestamped research finding to wiki/log.md."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        from lib.wiki_ops import append_to_log
        append_to_log(wiki_dir, "Agent Research", "Remote Agent Entry", [entry_text.strip()])
        return f"Successfully appended entry to wiki/log.md in {wiki_name}"

    def search_wiki(self, wiki_name: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Lightweight semantic search across a wiki's vector store without LLM synthesis."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        store = self.get_vector_store(wiki_name)
        raw_results = store.search_similar_with_metadata(query, k=limit)
        results: List[Dict[str, Any]] = []
        for r in raw_results:
            content = r.get("content", "").strip()
            snippet = content[:500] + "..." if len(content) > 500 else content
            results.append({
                "wiki": wiki_name,
                "path": r.get("path", ""),
                "title": r.get("title", ""),
                "snippet": snippet,
                "distance": r.get("distance"),
            })
        return results

    def search_all_wikis(self, query: str, limit_per_wiki: int = 3) -> Dict[str, Any]:
        """Federated semantic search across all registered wikis."""
        wikis = self.list_wikis()
        all_results: List[Dict[str, Any]] = []
        searched_wikis: List[str] = []

        for w in wikis:
            w_name = w["name"]
            searched_wikis.append(w_name)
            try:
                hits = self.search_wiki(w_name, query, limit=limit_per_wiki)
                all_results.extend(hits)
            except Exception as e:
                logger.warning("Error searching wiki %s: %s", w_name, e)

        # Sort all results by distance if present
        all_results.sort(key=lambda x: (x.get("distance") is None, x.get("distance", 1.0)))

        return {
            "query": query,
            "searched_wikis": searched_wikis,
            "total_hits": len(all_results),
            "results": all_results,
        }

    def get_recent_changes(self, wiki_name: Optional[str] = None, days: int = 7, limit: int = 20) -> List[Dict[str, Any]]:
        """Return pages created or modified within the last N days across one or all wikis."""
        cutoff_ts = time.time() - (days * 86400)
        target_wikis: List[str] = []
        if wiki_name and wiki_name.lower() not in ("all", "*"):
            wiki_dir = self.resolve_wiki(wiki_name)
            if not wiki_dir:
                raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")
            target_wikis = [wiki_name]
        else:
            target_wikis = [w["name"] for w in self.list_wikis()]

        recent: List[Dict[str, Any]] = []
        for w_name in target_wikis:
            w_dir = self.resolve_wiki(w_name)
            if not w_dir:
                continue
            wiki_folder = w_dir / "wiki"
            if not wiki_folder.is_dir():
                continue

            for md_file in wiki_folder.rglob("*.md"):
                if md_file.name.startswith("."):
                    continue
                try:
                    stat = md_file.stat()
                    mtime = stat.st_mtime
                    if mtime >= cutoff_ts or days <= 0:
                        content = md_file.read_text(encoding="utf-8")
                        fm, _ = parse_frontmatter(content)
                        rel_path = str(md_file.relative_to(wiki_folder))
                        mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                        recent.append({
                            "wiki": w_name,
                            "path": rel_path,
                            "title": fm.get("title", md_file.stem),
                            "type": fm.get("type", "page"),
                            "modified": mtime_iso,
                            "_timestamp": mtime,
                            "created": fm.get("created", ""),
                            "confidence": fm.get("confidence", "normal"),
                        })
                except Exception as e:
                    logger.debug("Failed reading file %s: %s", md_file, e)

        recent.sort(key=lambda x: x["_timestamp"], reverse=True)
        for item in recent:
            item.pop("_timestamp", None)
        return recent[:limit]

    def get_graph_insights(self, wiki_name: str) -> Dict[str, Any]:
        """Compute knowledge graph metrics: hub pages, orphans, dead links, and connectivity."""
        wiki_dir = self.resolve_wiki(wiki_name)
        if not wiki_dir:
            raise ValueError(f"Wiki '{wiki_name}' not found in {self.base_dir}")

        wiki_folder = wiki_dir / "wiki"
        if not wiki_folder.is_dir():
            raise ValueError(f"No 'wiki' folder found in {wiki_dir}")

        link_graph = build_link_graph(wiki_folder)
        all_pages = {f.stem.lower(): f.stem for f in wiki_folder.rglob("*.md") if not f.name.startswith(".")}
        all_page_stems = set(all_pages.keys())

        incoming_counts: Dict[str, int] = {stem: 0 for stem in all_page_stems}
        dead_links_count: Dict[str, int] = {}
        total_links = 0

        for src, targets in link_graph.items():
            for tgt in targets:
                total_links += 1
                tgt_stem = Path(tgt).stem.lower()
                tgt_slug = slugify(tgt)

                found_stem = None
                if tgt_stem in all_page_stems:
                    found_stem = tgt_stem
                else:
                    for s in all_page_stems:
                        if slugify(s) == tgt_slug:
                            found_stem = s
                            break

                if found_stem:
                    if src.lower() != found_stem:
                        incoming_counts[found_stem] = incoming_counts.get(found_stem, 0) + 1
                else:
                    dead_links_count[tgt] = dead_links_count.get(tgt, 0) + 1

        sorted_hubs = sorted(
            [{"page": all_pages.get(stem, stem), "backlinks": count} for stem, count in incoming_counts.items()],
            key=lambda x: x["backlinks"],
            reverse=True,
        )

        system_roots = {"overview", "index", "log", "readme", "home"}
        orphans = [
            all_pages.get(stem, stem)
            for stem, count in incoming_counts.items()
            if count == 0 and stem.lower() not in system_roots
        ]

        sorted_dead_links = sorted(
            [{"target": link, "mention_count": cnt} for link, cnt in dead_links_count.items()],
            key=lambda x: x["mention_count"],
            reverse=True,
        )

        total_nodes = len(all_page_stems)
        density = round(total_links / total_nodes, 2) if total_nodes > 0 else 0.0

        return {
            "wiki_name": wiki_name,
            "total_pages": total_nodes,
            "total_links": total_links,
            "link_density": density,
            "top_hubs": sorted_hubs[:10],
            "orphans": sorted(orphans),
            "dead_links": sorted_dead_links[:10],
        }

    def get_router_health(self) -> Dict[str, Any]:
        """Return runtime health, cached vector stores, and provider cooldowns."""
        wikis = self.list_wikis()
        cached_stores = list(self._store_cache.keys())
        cooldowns = RateLimitCooldownTracker.get_cooldowns()

        return {
            "status": "healthy",
            "base_dir": str(self.base_dir),
            "total_wikis": len(wikis),
            "wikis": [w["name"] for w in wikis],
            "cached_vector_stores": cached_stores,
            "active_rate_limit_cooldowns": cooldowns,
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
