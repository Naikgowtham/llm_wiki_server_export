import pytest
from pathlib import Path
from lib.mcp_router import WikiRouterManager, resolve_page_path
from lib.config import ProvidersConfig
from lib.providers import LLMProvider


@pytest.fixture
def temp_wikis_dir(tmp_path):
    base = tmp_path / "wikis"
    base.mkdir()

    # Create mock wiki 1
    w1 = base / "ai-wiki"
    (w1 / "wiki" / "concepts").mkdir(parents=True)
    (w1 / "wiki" / "entities").mkdir(parents=True)
    (w1 / "raw").mkdir(parents=True)
    (w1 / ".llm-wiki").mkdir(parents=True)

    (w1 / "wiki.yaml").write_text(
        "domain:\n  name: AI Wiki\n  description: AI Research Knowledge Base\n"
    )

    (w1 / "wiki" / "overview.md").write_text("# AI Wiki Overview\n[[RoBERTa]] is an entity.", encoding="utf-8")
    (w1 / "wiki" / "concepts" / "lora.md").write_text(
        "---\ntitle: LoRA\ntype: concept\nconfidence: high\nsources: ['raw/paper.pdf']\n---\n# LoRA\nLow-Rank Adaptation is efficient fine-tuning.\nSee [[RoBERTa]].",
        encoding="utf-8"
    )
    (w1 / "wiki" / "entities" / "roberta.md").write_text(
        "---\ntitle: RoBERTa\ntype: entity\nconfidence: high\ncreated: '2026-10-07'\nsources: ['raw/paper.pdf']\ntags: ['nlp']\n---\n# RoBERTa\nRoBERTa is a robustly optimized BERT approach.\nIt builds upon [[BERT]] and is evaluated with [[LoRA]].",
        encoding="utf-8"
    )
    (w1 / "raw" / "paper.pdf").write_text("dummy binary content", encoding="utf-8")

    # Create mock wiki 2
    w2 = base / "vehicles-wiki"
    (w2 / "wiki" / "topics").mkdir(parents=True)
    (w2 / "raw").mkdir(parents=True)
    (w2 / "wiki.yaml").write_text(
        "domain:\n  name: Vehicles Wiki\n  description: EV Powertrains\n"
    )
    (w2 / "wiki" / "topics" / "battery.md").write_text("# Battery Tech\nEV batteries.", encoding="utf-8")

    # Create invalid folder (not a wiki)
    invalid_dir = base / "random-folder"
    invalid_dir.mkdir()

    return base


def test_list_wikis(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    wikis = manager.list_wikis()
    assert len(wikis) == 2
    names = [w["name"] for w in wikis]
    assert "ai-wiki" in names
    assert "vehicles-wiki" in names
    assert "random-folder" not in names

    ai_meta = next(w for w in wikis if w["name"] == "ai-wiki")
    assert ai_meta["description"] == "AI Research Knowledge Base"
    assert ai_meta["page_count"] == 3


def test_resolve_wiki_security(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    assert manager.resolve_wiki("ai-wiki") is not None
    assert manager.resolve_wiki("vehicles-wiki") is not None
    assert manager.resolve_wiki("non-existent") is None
    assert manager.resolve_wiki("../../etc") is None


def test_resolve_page_path(temp_wikis_dir):
    ai_dir = temp_wikis_dir / "ai-wiki"
    
    # Direct with extension
    p1 = resolve_page_path(ai_dir, "overview.md")
    assert p1 is not None and p1.name == "overview.md"

    # Direct without extension
    p2 = resolve_page_path(ai_dir, "overview")
    assert p2 is not None and p2.name == "overview.md"

    # Nested with path
    p3 = resolve_page_path(ai_dir, "entities/roberta.md")
    assert p3 is not None and p3.name == "roberta.md"

    # Flexible fuzzy resolution (just stem)
    p4 = resolve_page_path(ai_dir, "roberta")
    assert p4 is not None and p4.name == "roberta.md"

    # Non-existent
    assert resolve_page_path(ai_dir, "unknown_page") is None


def test_read_wiki_page(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    content = manager.read_wiki_page("ai-wiki", "roberta")
    assert "RoBERTa is a robustly optimized BERT approach" in content

    with pytest.raises(FileNotFoundError):
        manager.read_wiki_page("ai-wiki", "missing-note")


def test_get_wiki_skeleton(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    skel = manager.get_wiki_skeleton("ai-wiki")
    assert skel["wiki_name"] == "ai-wiki"
    assert "entities" in skel["categories"]
    assert "roberta" in skel["categories"]["entities"]
    assert "concepts" in skel["categories"]
    assert "lora" in skel["categories"]["concepts"]

    # Filter by category
    filtered = manager.get_wiki_skeleton("ai-wiki", category="entities")
    assert "entities" in filtered["categories"]
    assert "concepts" not in filtered["categories"]


def test_get_wiki_status(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    status = manager.get_wiki_status("ai-wiki")
    assert status["wiki_name"] == "ai-wiki"
    assert status["total_pages"] == 3
    assert status["raw_sources"] == 1
    assert status["entities"] == 1
    assert status["concepts"] == 1


def test_get_page_links(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    links = manager.get_page_links("ai-wiki", "roberta")
    assert links["page"] == "roberta"
    # RoBERTa links to [[BERT]] and [[LoRA]]
    assert "BERT" in links["outgoing_links"]
    assert "LoRA" in links["outgoing_links"]
    # LoRA and overview link to RoBERTa
    assert "lora" in [b.lower() for b in links["backlinks"]]
    assert "overview" in [b.lower() for b in links["backlinks"]]


def test_get_entity_info(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    info = manager.get_entity_info("ai-wiki", "roberta")
    assert info["entity"] == "roberta"
    assert info["type"] == "entity"
    assert info["confidence"] == "high"
    assert "nlp" in info["tags"]
    assert "raw/paper.pdf" in info["sources"]
    assert "RoBERTa is a robustly optimized" in info["summary"]


def test_add_inbox_note(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    msg = manager.add_inbox_note(
        "ai-wiki",
        title="Attention Is All You Need",
        content="Transformer architecture introduced.",
        tags=["transformers", "attention"]
    )
    assert "Successfully created inbox note" in msg
    inbox_files = list((temp_wikis_dir / "ai-wiki" / "raw" / "inbox").glob("*.md"))
    assert len(inbox_files) == 1
    text = inbox_files[0].read_text(encoding="utf-8")
    assert "Attention Is All You Need" in text
    assert "transformers" in text


def test_add_inbox_note_yaml_escaping(temp_wikis_dir):
    """Bug #3 regression test: frontmatter must escape special characters and prevent YAML injection."""
    from lib.utils import parse_frontmatter

    manager = WikiRouterManager(temp_wikis_dir)
    evil_title = 'Untrusted "Paper" Title\ninjected_field: true\nmalicious: [1, 2]'
    msg = manager.add_inbox_note(
        "ai-wiki",
        title=evil_title,
        content="Note body.",
        tags=["ai", "dangerous: tag"]
    )
    assert "Successfully created inbox note" in msg

    inbox_files = sorted((temp_wikis_dir / "ai-wiki" / "raw" / "inbox").glob("*.md"))
    # The latest created file
    target_file = inbox_files[-1]
    text = target_file.read_text(encoding="utf-8")

    fm, body = parse_frontmatter(text)
    assert fm["title"] == evil_title
    assert "injected_field" not in fm
    assert "malicious" not in fm
    assert fm["type"] == "source"
    assert "dangerous: tag" in fm["tags"]
    assert "Note body." in body


def test_append_to_log(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    log_file = temp_wikis_dir / "ai-wiki" / "wiki" / "log.md"
    log_file.write_text("# Wiki Log\n", encoding="utf-8")
    manager.append_to_log("ai-wiki", "Discovered new benchmark paper")
    log_content = log_file.read_text(encoding="utf-8")
    assert "Discovered new benchmark paper" in log_content


def test_get_recent_changes(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    changes = manager.get_recent_changes(wiki_name="ai-wiki", days=10)
    assert len(changes) >= 3
    paths = [c["path"] for c in changes]
    assert any("roberta" in p for p in paths)

    # All wikis query
    all_changes = manager.get_recent_changes(wiki_name=None, days=10)
    assert len(all_changes) >= 4


def test_get_graph_insights(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    insights = manager.get_graph_insights("ai-wiki")
    assert insights["wiki_name"] == "ai-wiki"
    assert insights["total_pages"] == 3
    hubs = [h["page"] for h in insights["top_hubs"]]
    assert "roberta" in hubs
    dead = [d["target"] for d in insights["dead_links"]]
    assert "BERT" in dead


def test_get_router_health(temp_wikis_dir):
    manager = WikiRouterManager(temp_wikis_dir)
    health = manager.get_router_health()
    assert health["status"] == "healthy"
    assert "ai-wiki" in health["wikis"]
    assert "vehicles-wiki" in health["wikis"]
    assert isinstance(health["active_rate_limit_cooldowns"], dict)


def test_search_wiki_and_federated(temp_wikis_dir):
    class MockStore:
        def search_similar_with_metadata(self, query, k=5):
            return [{"path": "concepts/lora.md", "title": "LoRA", "content": "Low-rank adaptation", "distance": 0.1}]

    manager = WikiRouterManager(temp_wikis_dir)
    manager._store_cache["ai-wiki"] = MockStore()

    hits = manager.search_wiki("ai-wiki", "fine-tuning", limit=2)
    assert len(hits) == 1
    assert hits[0]["title"] == "LoRA"
    assert hits[0]["wiki"] == "ai-wiki"

    manager._store_cache["vehicles-wiki"] = MockStore()
    fed = manager.search_all_wikis("tuning", limit_per_wiki=1)
    assert fed["total_hits"] >= 1
    assert "ai-wiki" in fed["searched_wikis"]
    assert "vehicles-wiki" in fed["searched_wikis"]

