"""Core wiki orchestration: ingestion, querying, linting, and persistence."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import jinja2

class SynthesisParseError(Exception):
    pass

from lib.config import WikiConfig, load_wiki_config
from lib.differ import FileChange, review_changes
from lib.providers import LLMProvider
from lib.splitter import split_document
from lib.utils import (
    build_link_graph,
    extract_source_citations,
    extract_wikilinks,
    parse_frontmatter,
    read_source_file,
    slugify,
    today_str,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def get_template(name: str) -> jinja2.Template:
    """Load a Jinja2 template from the prompts directory."""
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template {name} not found at {path}")
    content = path.read_text(encoding="utf-8")
    env = jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    return env.from_string(content)


def _clean_json_response(raw_text: str) -> str:
    """Strip markdown code fence blocks if LLM wraps JSON response in ```json ... ```."""
    if not raw_text:
        return "[]"
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    if not text.startswith("[") and not text.startswith("{"):
        idx_bracket = text.find("[")
        idx_brace = text.find("{")
        first_idx = -1
        if idx_bracket != -1 and idx_brace != -1:
            first_idx = min(idx_bracket, idx_brace)
        elif idx_bracket != -1:
            first_idx = idx_bracket
        elif idx_brace != -1:
            first_idx = idx_brace
        if first_idx != -1:
            text = text[first_idx:].strip()
        else:
            return "[]"
    return text


def parse_json_relaxed(text: str) -> Any:
    """Parse JSON with multi-layered fallback: standard json -> strict=False -> json_repair -> jiter."""
    if not text or not text.strip():
        return []
    cleaned = _clean_json_response(text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass
    try:
        import json_repair
        return json_repair.loads(cleaned)
    except Exception:
        pass
    try:
        import jiter
        return jiter.from_json(cleaned.encode("utf-8"), partial_mode="trailing-strings")
    except Exception:
        pass
    raise json.JSONDecodeError("Failed to parse JSON with all repair strategies", cleaned, 0)



def _validate_and_resolve_path(wiki_dir: Path, rel_path: str) -> Path:
    """Resolve and validate that a path remains inside the wiki directory (prevents path traversal)."""
    full_path = (wiki_dir / rel_path).resolve()
    wiki_sub_dir = (wiki_dir / "wiki").resolve()
    if not full_path.is_relative_to(wiki_sub_dir):
        raise ValueError(f"Path must be within wiki/ directory: {rel_path}")
    return full_path


def update_wiki_index(wiki_dir: Path, new_pages: List[str] = None):
    """Scan the wiki directory and rebuild or synchronize wiki/index.md."""
    index_file = wiki_dir / "wiki" / "index.md"
    if not index_file.exists():
        return

    config = load_wiki_config(wiki_dir)
    today = today_str()

    categories = {
        "sources": ("Sources", wiki_dir / "wiki" / "sources"),
        "concepts": ("Concepts", wiki_dir / "wiki" / "concepts"),
        "entities": ("Entities", wiki_dir / "wiki" / "entities"),
        "topics": ("Topics", wiki_dir / "wiki" / "topics"),
        "comparisons": ("Comparisons", wiki_dir / "wiki" / "comparisons"),
        "queries": ("Queries", wiki_dir / "wiki" / "queries"),
    }

    lines = [
        "---",
        "title: \"Wiki Index\"",
        "type: index",
        f"updated: \"{today}\"",
        "---",
        "",
        f"# {config.domain_name} — Wiki Index",
        "",
    ]

    total_pages = 0
    sources_count = 0

    sections_text = []
    for cat_key, (cat_label, cat_path) in categories.items():
        files = sorted(cat_path.glob("*.md")) if cat_path.exists() else []
        count = len(files)
        total_pages += count
        if cat_key == "sources":
            sources_count = count

        sections_text.append(f"## {cat_label} ({count})\n")
        if not files:
            sections_text.append(f"*No {cat_label.lower()} recorded yet.*\n")
        else:
            for f in files:
                try:
                    fm, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
                    title = fm.get("title", f.stem.replace("-", " ").title())
                    src_count = fm.get("source_count", 0)
                    source_str = "source" if src_count == 1 else "sources"
                    extra = f" ({src_count} {source_str})" if src_count else ""
                    sections_text.append(f"- [[{title}]] — {fm.get('type', '')}{extra}")
                except Exception:
                    sections_text.append(f"- [[{f.stem}]]")
            sections_text.append("")

    lines.append(f"> Total pages: {total_pages} | Sources ingested: {sources_count} | Last updated: {today}\n")
    lines.extend(sections_text)

    index_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    
    # V5: Automatically generate category Maps of Content (MOCs)
    try:
        generate_mocs(wiki_dir)
    except Exception as e:
        logger.debug("Failed auto-generating MOCs in update_wiki_index: %s", e)


def generate_mocs(wiki_dir: Path) -> List[Path]:
    """Generate dynamic Map of Content (MOC) dashboards for categories.
    
    (V5 Feature: Dynamic MOC Generation)
    """
    wiki_folder = wiki_dir / "wiki"
    if not wiki_folder.is_dir():
        return []

    link_graph = build_link_graph(wiki_folder)
    backlink_counts: Dict[str, int] = {}
    for src, targets in link_graph.items():
        for t in targets:
            stem = slugify(Path(t).stem)
            backlink_counts[stem] = backlink_counts.get(stem, 0) + 1

    categories = [
        ("concepts", "Concepts"),
        ("entities", "Entities"),
        ("topics", "Topics"),
    ]

    today = today_str()
    generated: List[Path] = []

    for cat_dir_name, cat_title in categories:
        cat_folder = wiki_folder / cat_dir_name
        if not cat_folder.is_dir():
            continue

        md_files = sorted(cat_folder.glob("*.md"))
        if len(md_files) < 1:
            continue

        pages_data = []
        for f in md_files:
            try:
                fm, body = parse_frontmatter(f.read_text(encoding="utf-8"))
                title = fm.get("title", f.stem.replace("-", " ").title())
                confidence = fm.get("confidence", "normal")
                stem_clean = slugify(f.stem)
                bl_count = backlink_counts.get(stem_clean, 0)

                summary = ""
                for line in body.splitlines():
                    ls = line.strip()
                    if ls and not ls.startswith("#") and not ls.startswith("---"):
                        summary = ls[:120] + ("..." if len(ls) > 120 else "")
                        break

                pages_data.append({
                    "title": title,
                    "stem": f.stem,
                    "confidence": confidence,
                    "backlinks": bl_count,
                    "summary": summary or "Documented in vault.",
                })
            except Exception:
                pass

        pages_data.sort(key=lambda x: (x["backlinks"], x["title"]), reverse=True)

        moc_content = [
            "---",
            f"title: \"{cat_title} Map of Content\"",
            "type: moc",
            f"updated: \"{today}\"",
            "---",
            "",
            f"# {cat_title} — Map of Content (MOC)",
            "",
            f"> Dynamic {cat_title} dashboard automatically generated by LLM Wiki V5.",
            f"> Total entries: **{len(pages_data)}** | Last updated: **{today}**",
            "",
            "| Page | Inbound Links | Confidence | Summary |",
            "| :--- | :---: | :---: | :--- |",
        ]

        for p in pages_data:
            conf_badge = f"`{p['confidence']}`"
            moc_content.append(
                f"| [[{p['title']}]] | {p['backlinks']} | {conf_badge} | {p['summary']} |"
            )

        moc_file = wiki_folder / f"{cat_title}_MOC.md"
        moc_file.write_text("\n".join(moc_content).strip() + "\n", encoding="utf-8")
        generated.append(moc_file)

    return generated


def append_to_log(wiki_dir: Path, operation: str, title: str, details: List[str] = None):
    """Append a structured entry to wiki/log.md."""
    log_file = wiki_dir / "wiki" / "log.md"
    if not log_file.exists():
        return

    today = today_str()
    entry = [f"\n## [{today}] {operation} | {title}"]
    if details:
        for d in details:
            entry.append(f"- {d}")
    entry.append("")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(entry))


def commit_if_git(wiki_dir: Path, message: str) -> bool:
    """Perform a git commit in the wiki directory if git is initialized."""
    try:
        import git
        repo = git.Repo(wiki_dir)
        repo.git.add(A=True)
        repo.index.commit(message)
        return True
    except Exception:
        return False


def vector_cross_reference_pages(
    wiki_dir: Path,
    synth_files: List[Dict[str, Any]],
    store: Optional[Any] = None,
    distance_threshold: float = 0.38,
    max_links_per_new_page: int = 3,
) -> List[FileChange]:
    """Leverage ChromaDB vector similarity to cross-reference existing pages without LLM calls.
    
    (V5 Feature: Vector-Automated Cross-Referencing)
    """
    if not store or not synth_files:
        return []

    changes: List[FileChange] = []
    modified_paths: Dict[str, str] = {}

    for new_item in synth_files:
        rel_path = new_item.get("path", "")
        new_content = new_item.get("content", "")
        fm, _ = parse_frontmatter(new_content)
        new_title = fm.get("title", Path(rel_path).stem).strip()
        new_stem = Path(rel_path).stem

        query_text = f"{new_title}\n{new_content[:500]}"
        try:
            matches = store.search_similar_with_metadata(
                query_text, k=max_links_per_new_page + 2, distance_threshold=distance_threshold
            )
        except Exception as e:
            logger.debug("Vector search error during cross-reference: %s", e)
            continue

        links_added = 0
        for match in matches:
            match_path_str = match.get("path", "")
            if not match_path_str:
                continue

            try:
                target_file = _validate_and_resolve_path(wiki_dir, match_path_str)
            except Exception:
                continue

            if not target_file.is_file():
                continue

            if target_file.stem in (new_stem, "index", "log", "overview") or target_file.name.endswith("_MOC.md"):
                continue

            content = modified_paths.get(match_path_str)
            if content is None:
                content = target_file.read_text(encoding="utf-8")

            existing_links = extract_wikilinks(content)
            existing_lower = [l.lower().strip() for l in existing_links]
            if new_title.lower() in existing_lower or new_stem.lower() in existing_lower:
                continue

            link_markdown = f"- [[{new_title}]]"
            if "## Related" in content or "## See Also" in content:
                heading = "## Related" if "## Related" in content else "## See Also"
                pattern = rf"({re.escape(heading)}[^\n]*\n)"
                content = re.sub(pattern, rf"\1{link_markdown}\n", content, count=1)
            else:
                content = content.rstrip() + f"\n\n## Related\n{link_markdown}\n"

            modified_paths[match_path_str] = content
            links_added += 1
            if links_added >= max_links_per_new_page:
                break

    for path_str, new_c in modified_paths.items():
        orig_file = wiki_dir / path_str
        old_c = orig_file.read_text(encoding="utf-8") if orig_file.is_file() else None
        changes.append(
            FileChange(
                path=path_str,
                operation="update",
                new_content=new_c,
                old_content=old_c,
                reason="Vector-automated cross-reference (V5)",
            )
        )

    return changes


async def run_ingest(
    source_file: Path,
    wiki_dir: Path,
    provider: Optional[LLMProvider] = None,
    auto_approve: bool = False,
) -> List[FileChange]:
    """Execute complete ingestion pipeline for a raw source."""
    if not provider:
        provider = LLMProvider()

    config = load_wiki_config(wiki_dir)
    source_content = read_source_file(source_file)
    source_filename = source_file.name
    source_slug = slugify(source_file.stem)
    today = today_str()

    logger.info("Splitting source %s...", source_filename)
    
    # 3. Diff-Based Incremental Ingestion
    from lib.diff_tracker import get_diff_deltas
    raw_cache_dir = wiki_dir / "wiki" / ".llm-wiki" / "raw_cache"
    raw_cache_file = raw_cache_dir / source_filename
    
    content_to_process = source_content
    if raw_cache_file.exists():
        old_content = raw_cache_file.read_text(encoding="utf-8")
        if old_content != source_content:
            logger.info("Raw file modified. Extracting diff deltas...")
            delta_content = get_diff_deltas(old_content, source_content)
            if delta_content.strip():
                content_to_process = delta_content
                logger.info(f"Using delta context ({len(content_to_process)} chars) instead of full file.")
            else:
                logger.info("No meaningful text changes found in diff.")
                return []
        else:
            logger.info("Raw file unchanged since last ingest. Skipping extraction.")
            return []
            
    chunks = split_document(
        content_to_process,
        strategy=config.ingest_settings.get("chunk_strategy", "headers"),
        max_tokens=config.ingest_settings.get("chunk_max_tokens", 2000),
        overlap_tokens=config.ingest_settings.get("overlap_tokens", 200),
        provider=provider,
    )

    if not chunks:
        logger.warning(f"No text extracted from {source_filename}. Skipping.")
        return []

    # 1. Chunked extraction
    import asyncio
    extract_template = get_template("ingest_extract.md")
    extraction_results = {}

    async def _process_chunks():
        semaphore = asyncio.Semaphore(2)
        total_chunks = len(chunks)
        completed = 0

        async def extract_chunk(chunk):
            nonlocal completed
            prompt_str = extract_template.render(
                domain_name=config.domain_name,
                source_filename=source_filename,
                chunk_index=chunk.index,
                chunk_total=chunk.total,
                header_path=chunk.header_path,
                chunk_content=chunk.content,
            )
            async with semaphore:
                resp = await provider.acall([{"role": "user", "content": prompt_str}], operation="ingest_extract")
                await asyncio.sleep(1.5)
                cleaned = _clean_json_response(resp)
                try:
                    parsed = json.loads(cleaned)
                except Exception as e:
                    logger.warning("Failed to parse chunk %d JSON: %s. Using raw excerpt.", chunk.index, e)
                    parsed = {"summary": chunk.content[:300], "raw": chunk.content[:500]}
                
                extraction_results[chunk.index] = parsed
                completed += 1
                emit_progress(f"Ingesting {source_filename}: Extracted chunk {completed}/{total_chunks}", int(completed / total_chunks * 33))

        tasks = []
        for chunk in chunks:
            tasks.append(asyncio.create_task(extract_chunk(chunk)))
            await asyncio.sleep(0.3)
        await asyncio.gather(*tasks)

    await _process_chunks()

    # Maintain order of chunks and compact high-token payloads for synthesis
    all_extractions = [res for idx, res in sorted(extraction_results.items())]
    if len(all_extractions) > 4:
        # Aggregate across chunks to maintain a dense, high-signal payload within token budgets (<3,500 tokens)
        chunk_summaries = []
        entity_map = {}
        concept_map = {}
        all_claims = []
        all_relationships = []
        all_caveats = []

        for idx, item in enumerate(all_extractions):
            if not isinstance(item, dict):
                continue
            s = item.get("summary", "").strip()
            if s:
                chunk_summaries.append(f"Section {idx + 1}: {s}")

            for ent in item.get("entities", []):
                if isinstance(ent, dict) and ent.get("name"):
                    name = ent["name"].strip()
                    if name not in entity_map:
                        entity_map[name] = {
                            "name": name,
                            "type": ent.get("type", "entity"),
                            "facts": [],
                            "count": 0,
                        }
                    entity_map[name]["count"] += 1
                    for f in ent.get("facts", []):
                        if f not in entity_map[name]["facts"] and len(entity_map[name]["facts"]) < 3:
                            entity_map[name]["facts"].append(f)

            for conc in item.get("concepts", []):
                if isinstance(conc, dict) and conc.get("name"):
                    cname = conc["name"].strip()
                    if cname not in concept_map:
                        concept_map[cname] = {
                            "name": cname,
                            "definition": conc.get("definition", ""),
                            "properties": [],
                            "count": 0,
                        }
                    concept_map[cname]["count"] += 1
                    for p in conc.get("properties", []):
                        if p not in concept_map[cname]["properties"] and len(concept_map[cname]["properties"]) < 3:
                            concept_map[cname]["properties"].append(p)

            for cl in item.get("claims", []):
                if isinstance(cl, dict) and cl.get("statement"):
                    all_claims.append(cl)

            for rel in item.get("relationships", []):
                if isinstance(rel, dict) and rel.get("subject") and rel.get("object"):
                    all_relationships.append(rel)

            for cav in item.get("contradictions_or_caveats", []):
                if cav and cav not in all_caveats:
                    all_caveats.append(cav)

        top_entities = sorted(entity_map.values(), key=lambda x: x["count"], reverse=True)[:14]
        for e in top_entities:
            e.pop("count", None)

        top_concepts = sorted(concept_map.values(), key=lambda x: x["count"], reverse=True)[:10]
        for c in top_concepts:
            c.pop("count", None)

        compact_extractions = {
            "document_section_summaries": chunk_summaries[:15],
            "prominent_entities": top_entities,
            "prominent_concepts": top_concepts,
            "key_claims": all_claims[:15],
            "key_relationships": all_relationships[:15],
            "caveats": all_caveats[:8],
        }
    else:
        compact_extractions = all_extractions

    # 2. Synthesis (new pages)
    synth_template = get_template("ingest_synthesize.md")
    index_path = wiki_dir / "wiki" / "index.md"
    existing_index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    page_templates = ""
    template_dir = wiki_dir / "page-templates"
    if template_dir.exists():
        for tpl in template_dir.glob("*.md"):
            page_templates += f"\n--- {tpl.name} ---\n{tpl.read_text(encoding='utf-8')}\n"

    synth_prompt = synth_template.render(
        domain_name=config.domain_name,
        source_filename=source_filename,
        source_slug=source_slug,
        date=today,
        combined_extractions=json.dumps(compact_extractions, indent=2),
        existing_index=existing_index,
        page_templates=page_templates,
        max_page_words=config.ingest_settings.get("max_page_words", 800),
    )
    emit_progress(f"Ingesting {source_filename}: Synthesizing pages...", 50)
    synth_resp = await provider.acall([{"role": "user", "content": synth_prompt}], operation="ingest_synth")
    synth_cleaned = _clean_json_response(synth_resp)

    proposed_changes: List[FileChange] = []
    
    # N-01: Build title-to-path map to prevent collisions
    title_to_path = {}
    for md in (wiki_dir / "wiki").rglob("*.md"):
        try:
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            if "title" in fm:
                title_to_path[fm["title"].lower().strip()] = str(md.relative_to(wiki_dir))
        except Exception:
            pass
            
    try:
        synth_files = parse_json_relaxed(synth_resp)
        if isinstance(synth_files, list):
            synth_files = [item for item in synth_files if isinstance(item, dict) and item.get("path") and item.get("content")]
        else:
            synth_files = []

        for item in synth_files:
            rel_path = item.get("path", "")
            target_path = _validate_and_resolve_path(wiki_dir, rel_path)
            old_c = target_path.read_text(encoding="utf-8") if target_path.exists() else None
            new_content = item.get("content", "")
            
            # Check title collision
            try:
                fm_new, _ = parse_frontmatter(new_content)
                new_title = fm_new.get("title", "").lower().strip()
                if not old_c and new_title and new_title in title_to_path:
                    existing_path = title_to_path[new_title]
                    if existing_path != rel_path:
                        logger.warning(f"Title '{new_title}' already exists at {existing_path}. Rejecting create at {rel_path}.")
                        continue
            except Exception:
                pass
            
            # Validate page completeness
            if len(new_content.strip()) < 100:
                logger.warning(f"Rejecting truncated page ({len(new_content.strip())} chars): {rel_path}")
                continue
            try:
                fm_valid, _ = parse_frontmatter(new_content)
                if not fm_valid.get("title") or not fm_valid.get("type"):
                    logger.warning(f"Rejecting page with missing frontmatter title/type: {rel_path}")
                    continue
            except Exception:
                logger.warning(f"Rejecting unparseable page: {rel_path}")
                continue

            # F-03: Numeric claim gate
            try:
                fm, body = parse_frontmatter(new_content)
                body_to_check = body
            except Exception:
                body_to_check = new_content
                fm = {}
                
            numbers_in_new = re.findall(r'\b\d+(?:[\.,]\d+)?\b', body_to_check)
            has_fabrication = False
            for num in numbers_in_new:
                clean_num = num.replace(",", "")
                if (not re.search(r'\b' + re.escape(num) + r'\b', source_content) 
                    and clean_num not in source_content 
                    and num not in existing_index):
                    logger.warning(f"Unverified number detected: {num} in {rel_path}. Downgrading confidence to low.")
                    has_fabrication = True
                    break
                    
            if has_fabrication:
                try:
                    fm, body = parse_frontmatter(new_content)
                    fm["confidence"] = "low"
                    from lib.utils import render_frontmatter
                    new_content = render_frontmatter(fm, body)
                except Exception:
                    pass
                    
            proposed_changes.append(
                FileChange(
                    path=rel_path,
                    operation=item.get("operation", "create" if old_c is None else "update"),
                    new_content=new_content,
                    old_content=old_c,
                    reason=item.get("reason", "Synthesized from source"),
                )
            )
    except Exception as e:
        logger.error("Failed to parse synthesis response: %s", e)
        raise SynthesisParseError(f"Failed to parse synthesis response: {e}\nRaw output starts with: {synth_resp[:200]}")

    # 3. Cross-referencing candidate existing pages (V5: Vector-Automated with LLM Fallback)
    crossref_strategy = config.ingest_settings.get("crossref_strategy", "vector")
    vector_applied = False

    if crossref_strategy in ("vector", "hybrid"):
        try:
            from lib.vector_store import WikiVectorStore
            vec_store = WikiVectorStore(wiki_dir, provider)
            v_changes = vector_cross_reference_pages(
                wiki_dir=wiki_dir,
                synth_files=synth_files,
                store=vec_store,
                distance_threshold=config.ingest_settings.get("crossref_distance_threshold", 0.4),
            )
            if v_changes:
                logger.info("Vector cross-referencing proposed %d link updates without LLM.", len(v_changes))
                proposed_changes.extend(v_changes)
                vector_applied = True
        except Exception as e:
            logger.debug("Vector cross-referencing unavailable (%s), using LLM fallback.", e)

    if not vector_applied and crossref_strategy in ("llm", "hybrid", "vector"):
        crossref_template = get_template("ingest_crossref.md")
        existing_pages = []
        for md in (wiki_dir / "wiki").rglob("*.md"):
            if md.name in ("index.md", "log.md", f"{source_slug}.md") or md.name.endswith("_MOC.md"):
                continue
            try:
                rel = str(md.relative_to(wiki_dir))
                existing_pages.append({"path": rel, "content": md.read_text(encoding="utf-8")[:1500]})
            except Exception:
                pass

        if existing_pages:
            cross_prompt = crossref_template.render(
                domain_name=config.domain_name,
                source_filename=source_filename,
                source_slug=source_slug,
                date=today,
                extractions_summary=json.dumps(all_extractions[:3], indent=2),
                candidate_pages=existing_pages[:8],
            )
            emit_progress(f"Ingesting {source_filename}: Cross-referencing pages...", 75)
            cross_resp = await provider.acall([{"role": "user", "content": cross_prompt}], operation="ingest_crossref")
            cross_cleaned = _clean_json_response(cross_resp)
            try:
                cross_files = json.loads(cross_cleaned)
                for item in cross_files:
                    rel_path = item.get("path", "")
                    target_path = _validate_and_resolve_path(wiki_dir, rel_path)
                    old_c = target_path.read_text(encoding="utf-8") if target_path.exists() else None
                    proposed_changes.append(
                        FileChange(
                            path=rel_path,
                            operation="update",
                            new_content=item.get("content", ""),
                            old_content=old_c,
                            reason=item.get("reason", "Cross-referenced with new source"),
                        )
                    )
            except Exception:
                pass

    # 4. Human-in-the-loop review
    approved_changes = review_changes(proposed_changes, auto_approve=auto_approve)

    # 5. Apply approved changes to disk
    applied: List[FileChange] = []
    for change in approved_changes:
        full_path = wiki_dir / change.path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(change.new_content, encoding="utf-8")
        applied.append(change)

    if applied:
        update_wiki_index(wiki_dir)
        append_to_log(
            wiki_dir,
            operation="ingest",
            title=source_filename,
            details=[
                f"Source: raw/{source_filename}",
                f"Modified/created files: {', '.join([c.path for c in applied])}",
            ],
        )
        if config.ingest_settings.get("auto_commit", True):
            commit_if_git(wiki_dir, f"ingest: {source_filename}")

        # Save to raw cache only when changes were actually applied to the vault
        raw_cache_dir.mkdir(parents=True, exist_ok=True)
        raw_cache_file.write_text(source_content, encoding="utf-8")
        emit_progress(f"Completed ingestion of {source_filename}", 100)
    else:
        logger.info(f"No changes applied for {source_filename}. Leaving raw cache untouched.")
        emit_progress(f"Ingestion finished without applying changes for {source_filename}", 100)

    return applied


async def run_query(
    question: str,
    wiki_dir: Path,
    provider: Optional[LLMProvider] = None,
    file_back: bool = False,
    use_hybrid: bool = False,
    store: Optional[Any] = None,
) -> Tuple[str, Optional[Path]]:
    """Execute grounded question answering against wiki pages."""
    if not provider:
        provider = LLMProvider()

    config = load_wiki_config(wiki_dir)
    today = today_str()

    # N-03: Use Vector Store to retrieve context instead of loading all pages
    if store is None:
        from lib.vector_store import WikiVectorStore
        store = WikiVectorStore(wiki_dir, provider)
    
    query_context = provider.config.get_token_limit("query_context", 16000)
    k_pages = max(5, query_context // 1000)
    
    # Idea 8: Graph-Based Query Truncation
    graph_context = []
    # Load all titles to find entities mentioned in query
    all_pages = {}
    for md in (wiki_dir / "wiki").rglob("*.md"):
        try:
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            title = fm.get("title", md.stem).lower()
            all_pages[title] = md
            all_pages[md.stem.lower()] = md
        except Exception:
            pass
            
    # Simple entity matching: check if any title is in the question
    q_lower = question.lower()
    matched_page = None
    for title, md_file in all_pages.items():
        if len(title) > 3 and title in q_lower:
            matched_page = md_file
            break
            
    if matched_page:
        logger.info("Matched entity '%s' in query. Applying Graph-Based Query Truncation.", matched_page.stem)
        link_graph = build_link_graph(wiki_dir / "wiki")
        neighbors = set(link_graph.get(matched_page.stem, set()))
        
        # Add incoming neighbors too
        for src, targets in link_graph.items():
            for t in targets:
                if t.lower() == matched_page.stem.lower() or t.lower() == all_pages.get(matched_page.stem.lower(), matched_page).stem.lower():
                    neighbors.add(src)
        
        # Load the entity page itself
        try:
            graph_context.append({
                "path": str(matched_page.relative_to(wiki_dir)),
                "title": matched_page.stem,
                "content": matched_page.read_text(encoding="utf-8")
            })
        except Exception as e:
            logger.warning("Failed to load matched page: %s", e)
        
        # Load 1st-degree neighbors
        for neighbor_slug in neighbors:
            if len(graph_context) >= k_pages:
                break
            for t, md_file in all_pages.items():
                if t == neighbor_slug.lower() or md_file.stem.lower() == neighbor_slug.lower():
                    try:
                        graph_context.append({
                            "path": str(md_file.relative_to(wiki_dir)),
                            "title": md_file.stem,
                            "content": md_file.read_text(encoding="utf-8")
                        })
                    except Exception:
                        pass
                    break

    if graph_context:
        context_pages = graph_context
    elif use_hybrid:
        try:
            import subprocess
            import shutil
            if not shutil.which("qmd"):
                raise FileNotFoundError("qmd command not found.")
            # Attempt qmd hybrid search
            qmd_result = subprocess.run(
                ["qmd", "search", question],
                cwd=str(wiki_dir),
                capture_output=True,
                text=True,
                check=True
            )
            logger.info("Used qmd hybrid search for retrieval.")
            context_pages = [{"path": "qmd_search", "title": "QMD Search Results", "content": qmd_result.stdout}]
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Hybrid search requested but qmd failed or is missing: {e}")
            raise RuntimeError(f"qmd search failed: {e}. Please install tobi/qmd to use --hybrid.")
    else:
        context_pages = store.search_similar_with_metadata(question, k=k_pages, distance_threshold=0.5)

    if not context_pages:
        logger.warning("No context pages found in vector store. Answering from general knowledge.")

    query_template = get_template("query_answer.md")
    prompt_str = query_template.render(
        domain_name=config.domain_name,
        question=question,
        context_pages=context_pages,
    )

    answer = await provider.acall([{"role": "user", "content": prompt_str}], operation="query_answer")

    filed_path: Optional[Path] = None
    if file_back:
        fileback_template = get_template("query_fileback.md")
        fb_prompt = fileback_template.render(
            domain_name=config.domain_name,
            question=question,
            answer=answer,
            date=today,
        )
        fb_content = await provider.acall([{"role": "user", "content": fb_prompt}], operation="query_fileback")
        slug = slugify(question[:50])
        queries_dir = wiki_dir / "wiki" / "queries"
        queries_dir.mkdir(parents=True, exist_ok=True)
        filed_path = queries_dir / f"{slug}.md"
        filed_path.write_text(fb_content, encoding="utf-8")

        update_wiki_index(wiki_dir)
        append_to_log(
            wiki_dir,
            operation="query",
            title=question[:60],
            details=[f"Filed back to: {filed_path.relative_to(wiki_dir)}"],
        )
        commit_if_git(wiki_dir, f"query: filed back '{question[:40]}'")
    else:
        append_to_log(
            wiki_dir,
            operation="query",
            title=question[:60],
            details=["Ad-hoc query synthesized"],
        )

    return answer, filed_path


async def run_lint(
    wiki_dir: Path,
    provider: Optional[LLMProvider] = None,
    fix: bool = False,
    auto_approve: bool = False,
    reset: bool = False,
    max_pages: Optional[int] = None,
    staged_only: bool = False,
) -> Dict[str, Any]:
    """Perform structural and semantic integrity checks on the wiki."""
    
    # N-05: Initialize config and pages_summary in outer scope
    config = load_wiki_config(wiki_dir)
    pages_summary = []
    
    staged_md_files: Optional[Set[Path]] = None
    if staged_only:
        staged_md_files = set()
        try:
            import subprocess
            res = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=d", "--cached"],
                cwd=str(wiki_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    clean_line = line.strip()
                    if clean_line.endswith(".md") and not clean_line.endswith("_MOC.md") and not clean_line.endswith(("index.md", "log.md", "overview.md")):
                        p = (wiki_dir / clean_line).resolve()
                        if p.exists() and ".llm-wiki" not in p.parts:
                            staged_md_files.add(p)
        except Exception as e:
            logger.warning("Could not retrieve staged git files: %s", e)

        # Fast path: If running in staged-only mode and no note markdown files are staged, exit immediately
        if not staged_md_files:
            return {
                "broken_links": [],
                "orphan_pages": [],
                "mechanical_issues": [],
                "semantic_issues": [],
            }

    link_graph = build_link_graph(wiki_dir / "wiki")
    all_pages: Set[str] = set()

    page_titles: Dict[str, str] = {}
    for md in (wiki_dir / "wiki").rglob("*.md"):
        if ".llm-wiki" in md.parts:
            continue
        all_pages.add(md.stem)
        try:
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            if "title" in fm and fm["title"]:
                page_titles[str(fm["title"]).lower().strip()] = md.stem
        except Exception:
            pass
    # F-02: Detect dangling sources in frontmatter
    # F-05: Check required frontmatter
    dangling_sources: List[str] = []
    frontmatter_issues: List[str] = []
    for md in (wiki_dir / "wiki").rglob("*.md"):
        if ".llm-wiki" in md.parts:
            continue
        if md.name in ("index.md", "log.md", "overview.md") or md.name.endswith("_MOC.md"):
            continue
        if staged_md_files is not None and md.resolve() not in staged_md_files:
            continue
        try:
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            for req in config.required_frontmatter:
                if req not in fm:
                    frontmatter_issues.append(f"{md.stem} -> missing frontmatter: {req}")
            
            if "sources" in fm and isinstance(fm["sources"], list):
                for source in fm["sources"]:
                    src_clean = str(source).replace("[[", "").replace("]]", "").strip()
                    if src_clean.startswith("raw/"):
                        if not (wiki_dir / src_clean).exists():
                            dangling_sources.append(f"{md.stem} -> dangling source: {src_clean}")
        except Exception:
            pass
    # Detect broken links and orphans
    broken_links: List[str] = []
    inbound_counts: Dict[str, int] = {p: 0 for p in all_pages}

    staged_stems = {p.stem for p in staged_md_files} if staged_md_files is not None else None
    for source_page, targets in link_graph.items():
        if staged_stems is not None and source_page not in staged_stems:
            continue
        for target in targets:
            # strip anchor for page matching
            page_target = target.split("#")[0]
            target_lower = page_target.lower().strip()
            target_clean = slugify(page_target)
            
            matching = []
            if target_lower in page_titles:
                matching.append(page_titles[target_lower])
            elif target_clean in all_pages:
                matching.append(target_clean)
            else:
                for p in all_pages:
                    if p.lower() == target_lower or slugify(p) == target_clean:
                        matching.append(p)
                        break
            
            if not matching:
                broken_links.append(f"{source_page} -> [[{target}]]")
            else:
                for m in matching:
                    inbound_counts[m] = inbound_counts.get(m, 0) + 1

    broken_links.extend(dangling_sources)

    orphan_pages = []
    if staged_md_files is None:
        orphan_pages = [p for p, count in inbound_counts.items() if count == 0 and p not in ("index", "log", "overview") and not p.endswith("_MOC")]

    semantic_issues = [{"category": "frontmatter", "file_path": issue.split(" -> ")[0], "description": issue.split(" -> ")[1]} for issue in frontmatter_issues]

    # V5: Pure Python Offloading for Mechanical Checks (0 LLM Tokens)
    import time
    min_citations = config.lint_settings.get("min_citations_per_page", 1)
    stale_days = config.lint_settings.get("stale_days", 30)
    cutoff_ts = time.time() - (stale_days * 86400)
    mechanical_issues = [{"category": "frontmatter", "file_path": issue.split(" -> ")[0], "description": issue.split(" -> ")[1]} for issue in frontmatter_issues]

    for md in (wiki_dir / "wiki").rglob("*.md"):
        if ".llm-wiki" in md.parts:
            continue
        if md.name in ("index.md", "log.md", "overview.md") or md.name.endswith("_MOC.md"):
            continue
        if staged_md_files is not None and md.resolve() not in staged_md_files:
            continue
        rel_str = str(md.relative_to(wiki_dir))
        try:
            content = md.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(content)
            
            # Mechanical citation check
            if min_citations > 0 and md.parent.name in ("concepts", "entities", "topics"):
                cits = extract_source_citations(body)
                fm_sources = fm.get("sources", [])
                if not cits and not fm_sources:
                    mechanical_issues.append({
                        "category": "citations",
                        "file_path": rel_str,
                        "description": "Missing [source: ...] citation or frontmatter sources",
                    })

            # Freshness / staleness check
            mtime = md.stat().st_mtime
            if mtime < cutoff_ts:
                mechanical_issues.append({
                    "category": "freshness",
                    "file_path": rel_str,
                    "description": f"Page older than {stale_days} days without update",
                })
        except Exception:
            pass

    report = {
        "broken_links": broken_links,
        "orphan_pages": orphan_pages,
        "mechanical_issues": mechanical_issues,
        "semantic_issues": semantic_issues,
    }

    # Semantic audit if provider available
    if provider:
        import time
        from lib.vector_store import WikiVectorStore
        
        store = WikiVectorStore(wiki_dir, provider)
        
        for md in (wiki_dir / "wiki").rglob("*.md"):
            if ".llm-wiki" in md.parts or md.name in ("index.md", "log.md", "overview.md") or md.name.endswith("_MOC.md"):
                continue
            if staged_md_files is not None and md.resolve() not in staged_md_files:
                continue
            try:
                fm, body = parse_frontmatter(md.read_text(encoding="utf-8"))
                pages_summary.append({
                    "path": str(md.relative_to(wiki_dir)),
                    "title": fm.get("title", md.stem),
                    "frontmatter": fm,
                    "content": body,
                    "mtime": md.stat().st_mtime,
                })
            except Exception:
                pass

        # 1. Update the Vector DB with the latest page contents
        import hashlib
        hash_registry_file = wiki_dir / "wiki" / ".llm-wiki" / "hash_registry.json"
        hash_registry = {}
        if hash_registry_file.exists():
            try:
                hash_registry = json.loads(hash_registry_file.read_text())
            except Exception:
                pass

        pages_to_lint = []
        for page in pages_summary:
            page_hash = hashlib.md5(page["content"].encode("utf-8")).hexdigest()
            page_mtime = page["mtime"]
            
            entry = hash_registry.get(page["path"])
            if isinstance(entry, dict):
                reg_hash = entry.get("hash")
                reg_mtime = entry.get("mtime")
            else:
                reg_hash = entry
                reg_mtime = None
                
            if reg_hash == page_hash:
                if reg_mtime != page_mtime:
                    hash_registry[page["path"]] = {"hash": page_hash, "mtime": page_mtime}
                continue # Skip embedding and linting, unchanged
            
            store.embed_and_upsert(page["path"], page["content"], {"title": str(page["title"])})
            hash_registry[page["path"]] = {"hash": page_hash, "mtime": page_mtime}
            pages_to_lint.append(page)
        
        # V5: Confidence-Weighted Lint Queue Prioritization
        CONFIDENCE_PRIORITY = {
            "contested": 1,
            "low": 2,
            "unknown": 3,
            "medium": 4,
            "high": 5,
        }
        pages_to_lint.sort(
            key=lambda p: (
                CONFIDENCE_PRIORITY.get(
                    str(p.get("frontmatter", {}).get("confidence", "medium")).lower().strip(), 3
                ),
                p.get("mtime", 0),
            )
        )
        if max_pages and max_pages > 0:
            pages_to_lint = pages_to_lint[:max_pages]

        hash_registry_file.parent.mkdir(parents=True, exist_ok=True)
        hash_registry_file.write_text(json.dumps(hash_registry))

        config = load_wiki_config(wiki_dir)
        
        # 2. Resumable State tracking
        state_file = wiki_dir / "wiki" / ".llm-wiki" / ".lint_state.json"
        
        if reset and state_file.exists():
            state_file.unlink()
            
        lint_state = []
        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    lint_state = json.load(f)
            except Exception:
                lint_state = []

        audit_template = get_template("lint_audit.md")
        
        # 3. Page-by-Page Retrieval Linting (Concurrent)
        # 3. Page-by-Page Retrieval Linting (Concurrent)
        import asyncio
        from threading import Lock

        state_lock = Lock()
        
        async def _process_lint_pages():
            semaphore = asyncio.Semaphore(2)
            total_pages = len(pages_summary)
            completed = 0
            
            async def process_page(page):
                nonlocal completed
                if page["path"] in lint_state:
                    return # Skip already linted pages in this session
                    
                logger.info("Semantic Linting page: %s", page["path"])
                
                # Topological graph context (Idea 17)
                page_stem = Path(page["path"]).stem
                topological_context = []
                if page_stem in link_graph:
                    for target in link_graph[page_stem]:
                        target_clean = slugify(target.split("#")[0])
                        for p in pages_summary:
                            p_stem = Path(p["path"]).stem
                            if p_stem == target_clean or p_stem.lower() == target.lower().strip() or p["title"].lower().strip() == target.lower().strip():
                                topological_context.append({"path": p["path"], "title": f"[Topological] {p['title']}", "content": p["content"]})
                                break
                
                # Get dynamically similar context (k=5, threshold=0.4)
                similar_docs = store.search_similar(page["content"], k=5, distance_threshold=0.4)
                topo_contents = {tc["content"] for tc in topological_context}
                context_docs = [doc for doc in similar_docs if doc != page["content"] and doc not in topo_contents]
                
                context_pages = topological_context + [{"path": "context", "title": "context", "content": d} for d in context_docs]
                
                audit_prompt = audit_template.render(
                    domain_name=config.domain_name,
                    broken_links="None", # Not needed for semantic page check
                    orphan_pages="None",
                    wiki_pages=[page] + context_pages
                )
                
                async with semaphore:
                    try:
                        resp = await provider.acall([{"role": "user", "content": audit_prompt}], operation="lint_audit")
                        cleaned = _clean_json_response(resp)
                        try:
                            issues = parse_json_relaxed(resp)
                            if not isinstance(issues, list):
                                issues = []
                        except Exception as json_err:
                            logger.warning("Failed to parse lint audit JSON for %s: %s", page["path"], json_err)
                            issues = []
                        
                        with state_lock:
                            if issues:
                                report["semantic_issues"].extend(issues)
                            
                            # Update state so we can resume if rate limited
                            lint_state.append(page["path"])
                            with open(state_file, "w") as f:
                                json.dump(lint_state, f)
                                
                    except Exception as e:
                        logger.warning("Semantic lint audit failed on %s: %s", page["path"], e)
                        logger.info("Saving lint state. Resume later by running `wiki lint` again.")
                        raise e
                    
                    completed += 1
                    emit_progress(f"Linting {page['path']}", int(completed / total_pages * 100))
            
            tasks = [process_page(p) for p in pages_to_lint]
            try:
                await asyncio.gather(*tasks)
            except Exception as e:
                logger.error("Linting failed fatally: %s", e)
                raise e

        await _process_lint_pages()

    # N-02: Unconditionally clear state after a completed run so next run will re-lint
    state_file = wiki_dir / "wiki" / ".llm-wiki" / ".lint_state.json"
    if state_file.exists():
        state_file.unlink()

    if fix and provider and (broken_links or orphan_pages or report["semantic_issues"]):
        fix_template = get_template("lint_fix.md")
        fix_prompt = fix_template.render(
            domain_name=config.domain_name,
            issues_to_fix=json.dumps(report, indent=2),
            affected_pages=pages_summary,
        )
        try:
            fix_resp = await provider.acall([{"role": "user", "content": fix_prompt}], operation="lint_fix")
            try:
                fix_files = parse_json_relaxed(fix_resp)
                if not isinstance(fix_files, list):
                    fix_files = []
            except Exception:
                fix_files = []
            
            proposed_changes: List[FileChange] = []
            for item in fix_files:
                rel_path = item.get("path", "")
                target_path = _validate_and_resolve_path(wiki_dir, rel_path)
                old_c = target_path.read_text(encoding="utf-8") if target_path.exists() else None
                proposed_changes.append(
                    FileChange(
                        path=rel_path,
                        operation=item.get("operation", "update"),
                        new_content=item.get("content", ""),
                        old_content=old_c,
                        reason=item.get("reason", "Lint fix"),
                    )
                )
                
            approved_changes = review_changes(proposed_changes, auto_approve=auto_approve)
            
            applied: List[FileChange] = []
            for change in approved_changes:
                full_path = wiki_dir / change.path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(change.new_content, encoding="utf-8")
                applied.append(change)
                
            if applied:
                update_wiki_index(wiki_dir)
                append_to_log(
                    wiki_dir,
                    operation="lint_fix",
                    title="Wiki Health Check Fix",
                    details=[
                        f"Modified/created files: {', '.join([c.path for c in applied])}",
                    ],
                )
                if config.ingest_settings.get("auto_commit", True):
                    commit_if_git(wiki_dir, f"lint: auto-fixed issues")
        except Exception as e:
            logger.warning("Semantic lint fix failed: %s", e)

    append_to_log(
        wiki_dir,
        operation="lint",
        title="Wiki Health Check",
        details=[
            f"Broken links: {len(broken_links)}",
            f"Orphan pages: {len(orphan_pages)}",
            f"Semantic issues: {len(report['semantic_issues'])}",
        ],
    )

    return report

def emit_progress(message: str, progress: int = 0):
    try:
        import requests
        requests.post("http://localhost:8000/update", json={"message": message, "progress": progress}, timeout=0.1)
    except Exception:
        pass
