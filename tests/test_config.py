"""Unit tests for configuration loaders."""

from pathlib import Path
from lib.config import load_providers_config, load_wiki_config


def test_load_wiki_config_from_template():
    repo_root = Path(__file__).resolve().parent.parent
    template_dir = repo_root / "templates"
    config = load_wiki_config(template_dir)
    assert "source" in config.page_types
    assert "concept" in config.page_types
    assert "entity" in config.page_types
    assert "title" in config.required_frontmatter


def test_load_providers_config_fallback():
    repo_root = Path(__file__).resolve().parent.parent
    example_path = repo_root / "config" / "providers.example.yaml"
    config = load_providers_config(example_path)
    assert "ingest" in config.fallback_chain
    assert "query" in config.fallback_chain
    assert len(config.get_fallback_models("ingest")) >= 1
