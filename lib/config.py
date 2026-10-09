"""Configuration loader and validator for LLM Wiki."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class ProvidersConfig:
    providers: Dict[str, Any] = field(default_factory=dict)
    fallback_chain: Dict[str, List[str]] = field(default_factory=dict)
    token_limits: Dict[str, int] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def get_fallback_models(self, operation: str) -> List[str]:
        """Return ordered list of models for given operation (e.g. 'ingest', 'query', 'lint', 'embeddings')."""
        if operation in self.fallback_chain:
            return self.fallback_chain[operation]
        # Generic default if specific operation not mapped
        for op in ("query", "ingest", "lint", "embeddings"):
            if op in self.fallback_chain:
                return self.fallback_chain[op]
        return ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]

    def get_token_limit(self, limit_name: str, default: int = 8000) -> int:
        return self.token_limits.get(limit_name, default)


@dataclass
class WikiConfig:
    domain_name: str = "Knowledge Wiki"
    domain_slug: str = "wiki"
    domain_description: str = "LLM-maintained knowledge base"
    page_types: List[str] = field(default_factory=lambda: ["source", "concept", "entity", "topic", "comparison", "query"])
    required_frontmatter: List[str] = field(default_factory=lambda: [
        "title", "type", "tags", "sources", "created", "updated", "confidence"
    ])
    confidence_thresholds: Dict[str, int] = field(default_factory=lambda: {"high": 3, "medium": 1})
    ingest_settings: Dict[str, Any] = field(default_factory=lambda: {
        "chunk_strategy": "headers",
        "chunk_max_tokens": 6000,
        "overlap_tokens": 500,
        "auto_commit": True,
        "max_page_words": 800,
    })
    lint_settings: Dict[str, Any] = field(default_factory=lambda: {
        "stale_days": 30,
        "min_citations_per_page": 1,
        "max_orphan_tolerance": 5,
    })
    raw: Dict[str, Any] = field(default_factory=dict)


def find_providers_config(start_path: Optional[Path] = None) -> Optional[Path]:
    """Search for providers.yaml in prioritized locations."""
    candidates = []

    # 1. User home config (highest priority for real credentials)
    home_config = Path.home() / ".config" / "llm-wiki" / "providers.yaml"
    candidates.append(home_config)

    # 2. Start path or current directory
    if start_path:
        candidates.append(start_path / "providers.yaml")
        candidates.append(start_path / "config" / "providers.yaml")

    cwd = Path.cwd()
    candidates.append(cwd / "providers.yaml")
    candidates.append(cwd / "config" / "providers.yaml")

    # 3. Default toolkit directory
    toolkit_dir = Path(__file__).resolve().parent.parent
    candidates.append(toolkit_dir / "config" / "providers.yaml")

    for path in candidates:
        if path.is_file():
            return path
    return None


def load_providers_config(config_path: Optional[Path] = None) -> ProvidersConfig:
    """Load and parse providers.yaml into ProvidersConfig dataclass."""
    path = config_path or find_providers_config()
    if not path or not path.is_file():
        # Fallback to empty config
        return ProvidersConfig()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        import sys
        print(f"Error: Malformed YAML in providers config at {path}: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        data = {}

    return ProvidersConfig(
        providers=data.get("providers", {}),
        fallback_chain=data.get("fallback_chain", {}),
        token_limits=data.get("token_limits", {}),
        raw=data,
    )


def load_wiki_config(wiki_dir: Path) -> WikiConfig:
    """Load wiki.yaml from the root of a wiki instance."""
    config_file = wiki_dir / "wiki.yaml"
    if not config_file.is_file():
        return WikiConfig()

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        data = {}

    domain = data.get("domain", {})
    return WikiConfig(
        domain_name=domain.get("name", "Knowledge Wiki"),
        domain_slug=domain.get("slug", "wiki"),
        domain_description=domain.get("description", "LLM-maintained knowledge base"),
        page_types=data.get("page_types", ["source", "concept", "entity", "topic", "comparison", "query"]),
        required_frontmatter=data.get("required_frontmatter", [
            "title", "type", "tags", "sources", "created", "updated", "confidence"
        ]),
        confidence_thresholds=data.get("confidence", {"high": 3, "medium": 1}),
        ingest_settings=data.get("ingest", {
            "chunk_strategy": "headers",
            "chunk_max_tokens": 6000,
            "overlap_tokens": 500,
            "auto_commit": True,
            "max_page_words": 800,
        }),
        lint_settings=data.get("lint", {
            "stale_days": 30,
            "min_citations_per_page": 1,
            "max_orphan_tolerance": 5,
        }),
        raw=data,
    )
