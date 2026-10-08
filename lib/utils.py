"""Utility functions for LLM Wiki."""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


def today_str() -> str:
    """Return current date in ISO format YYYY-MM-DD."""
    return datetime.date.today().isoformat()


def slugify(title: str) -> str:
    """Convert a title or phrase into a clean kebab-case slug.

    Example: "Transformer Architecture & Attention" -> "transformer-architecture-attention"
    """
    cleaned = re.sub(r"[^\w\s-]", " ", title.lower())
    slug = re.sub(r"[-\s_]+", "-", cleaned).strip("-")
    return slug or "untitled"


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter and body from Markdown content.

    Returns:
        (frontmatter_dict, body_markdown)
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        return {}, content

    raw_yaml, body = match.group(1), match.group(2)
    try:
        import yaml
        data = yaml.safe_load(raw_yaml)
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    return {}, content


def render_frontmatter(frontmatter: Dict[str, Any], body: str) -> str:
    """Serialize frontmatter dictionary and prepend to body markdown."""
    import yaml

    class IndentDumper(yaml.Dumper):
        def increase_indent(self, flow=False, indentless=False):
            return super(IndentDumper, self).increase_indent(flow, False)

    # Custom dumper formatting for clean YAML list output
    dumped = yaml.dump(
        frontmatter,
        Dumper=IndentDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{dumped}\n---\n\n{body.lstrip()}"


def extract_wikilinks(content: str) -> List[str]:
    """Extract all [[target]] or [[target|label]] links from markdown content.

    Returns list of target names (unlabeled).
    """
    pattern = r"\[\[([^\[\]|\n]+)(?:\|[^\[\]\n]+)?\]\]"
    matches = re.findall(pattern, content)
    # Strip whitespace and normalize
    return [m.strip() for m in matches if m.strip()]


def extract_source_citations(content: str) -> List[str]:
    """Extract inline source citations like `[source: filename.md, §section]`."""
    pattern = r"\[source:\s*([^\]]+)\]"
    return [m.strip() for m in re.findall(pattern, content)]


def count_tokens_approx(text: str) -> int:
    """Approximate token count without strict dependency on tokenizer models.

    Uses tiktoken if available, otherwise falls back to ~4 characters per token.
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Fallback estimation: ~4 chars per token in English
        return max(1, len(text) // 4)


def build_link_graph(wiki_dir: Path) -> Dict[str, Set[str]]:
    """Scan all markdown files in wiki_dir and build an adjacency list of outgoing links.

    Returns:
        Dict mapping relative file path (or base name) to set of target link names.
    """
    graph: Dict[str, Set[str]] = {}
    if not wiki_dir.exists():
        return graph

    for md_file in wiki_dir.rglob("*.md"):
        if ".llm-wiki" in md_file.parts:
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            links = extract_wikilinks(content)
            key = md_file.stem
            graph[key] = set(links)
        except Exception:
            continue
    return graph


def read_source_file(file_path: Path) -> str:
    """Read a raw source file (.md, .txt, .pdf), converting PDF to Markdown if necessary."""
    file_path = Path(file_path)
    if file_path.suffix.lower() == ".pdf":
        try:
            import pymupdf4llm
            return pymupdf4llm.to_markdown(str(file_path))
        except ImportError:
            raise ImportError(
                "pymupdf4llm is required to ingest PDF files. Please install it with: pip install pymupdf4llm"
            )
    return file_path.read_text(encoding="utf-8")


def list_available_vaults() -> List[Path]:
    """Discover available wiki vaults in standard locations (current dir, ~/wikis, ./wikis)."""
    vaults: List[Path] = []
    seen = set()

    for base in [Path.cwd(), Path.home() / "wikis", Path.cwd() / "wikis"]:
        if not base.is_dir():
            continue
        if (base / "wiki").is_dir():
            resolved = base.resolve()
            if resolved not in seen:
                vaults.append(resolved)
                seen.add(resolved)
        else:
            for entry in sorted(base.iterdir()):
                if entry.is_dir() and (entry / "wiki").is_dir():
                    resolved = entry.resolve()
                    if resolved not in seen:
                        vaults.append(resolved)
                        seen.add(resolved)
    return vaults


def resolve_vault_path(wiki_arg: Optional[str] = None, query: Optional[str] = None) -> Optional[Path]:
    """Smart resolution of a wiki vault directory:
    1. If explicit path or vault name is given:
       - Direct path: ./path, ~/path, /abs/path
       - Vault name in ~/wikis/<name> or ./wikis/<name>
    2. If omitted or default '.':
       - Current directory if it contains a wiki/ folder
       - Check ~/wikis and ./wikis for vaults:
         - If 1 vault: use it
         - If multiple vaults and query provided: check which vault matches best
    """
    if wiki_arg and wiki_arg != ".":
        p = Path(wiki_arg).expanduser().resolve()
        if (p / "wiki").is_dir():
            return p

        # Check ~/wikis and ./wikis for exact, suffix (-wiki), or prefix match
        for base in [Path.home() / "wikis", Path.cwd() / "wikis"]:
            if not base.is_dir():
                continue
            for cand in [base / wiki_arg, base / f"{wiki_arg}-wiki"]:
                if (cand / "wiki").is_dir():
                    return cand.resolve()
            for entry in base.iterdir():
                if entry.is_dir() and (entry / "wiki").is_dir():
                    clean_name = entry.name.lower().replace("-wiki", "").replace("_wiki", "")
                    arg_clean = wiki_arg.lower().replace("-wiki", "").replace("_wiki", "")
                    if arg_clean in (entry.name.lower(), clean_name):
                        return entry.resolve()
        return None

    # wiki_arg is None or '.'
    cwd = Path.cwd().resolve()
    if (cwd / "wiki").is_dir():
        return cwd

    available = list_available_vaults()
    if not available:
        return None

    if len(available) == 1:
        return available[0]

    # Multiple vaults available: try matching query text against vault name and note titles
    if query:
        q_lower = query.lower()
        # 1. Match vault name directly
        for v in available:
            v_name = v.name.lower()
            short_name = v_name.replace("-wiki", "").replace("_wiki", "")
            if (len(short_name) >= 2 and short_name in q_lower) or v_name in q_lower:
                return v

        # 2. Match note stems inside wiki/
        for v in available:
            wiki_sub = v / "wiki"
            if wiki_sub.is_dir():
                for md in wiki_sub.rglob("*.md"):
                    stem_clean = md.stem.lower().replace("-", " ")
                    if len(stem_clean) >= 3 and stem_clean in q_lower:
                        return v

    return None


