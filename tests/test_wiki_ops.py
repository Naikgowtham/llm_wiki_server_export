"""Unit tests for orchestrator logic in wiki_ops.py."""

import json
from pathlib import Path
from typing import Optional

import pytest

from lib.wiki_ops import run_lint, run_ingest, get_template
from lib.config import load_wiki_config
from lib.providers import LLMProvider, LLMProviderError


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    """Fixture to scaffold a mock LLM Wiki vault."""
    # Create structure
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "sources").mkdir()
    (tmp_path / "wiki" / "concepts").mkdir()
    
    # Create config
    (tmp_path / "wiki.yaml").write_text(
        "required_frontmatter: [title, type, tags]\n"
        "lint:\n"
        "  max_orphan_tolerance: 5\n"
    )
    
    return tmp_path


def test_lint_broken_links_with_anchor(wiki_dir: Path):
    """Test F-09 anchor handling: [[Page Name#Heading]] should resolve if Page Name exists."""
    # Valid page
    page1 = (wiki_dir / "wiki" / "concepts" / "battery.md")
    page1.write_text("---\ntitle: Battery\ntype: concept\ntags: [a]\n---\nBody.")
    
    # Page with links
    page2 = (wiki_dir / "wiki" / "concepts" / "ev.md")
    page2.write_text("---\ntitle: EV\ntype: concept\ntags: [a]\n---\nLinks to [[Battery#Charging]] and [[Fake#Section]].")
    
    import asyncio
    report = asyncio.run(run_lint(wiki_dir, provider=None, fix=False, auto_approve=False, reset=False))
    broken_links = report.get("broken_links", [])
    
    # [[Battery#Charging]] should be valid. [[Fake#Section]] should be broken.
    assert not any("Battery" in link for link in broken_links)
    assert any("Fake#Section" in link for link in broken_links)


def test_lint_missing_frontmatter(wiki_dir: Path):
    """Test F-05 required frontmatter validation."""
    page1 = (wiki_dir / "wiki" / "concepts" / "bad.md")
    page1.write_text("---\ntitle: Bad Page\ntype: concept\n---\nMissing tags.")
    
    import asyncio
    report = asyncio.run(run_lint(wiki_dir, provider=None, fix=False, auto_approve=False, reset=False))
    semantic_issues = report.get("semantic_issues", [])
    
    assert any(i["category"] == "frontmatter" and "tags" in i["description"] for i in semantic_issues)


class MockLLMProvider(LLMProvider):
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.config = None 

    def call(self, messages, operation="ingest", **kwargs):
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
        else:
            resp = "[]"
        return f"```json\n{resp}\n```"
        
    async def acall(self, messages, operation="ingest", **kwargs):
        import asyncio
        await asyncio.sleep(0.01)
        return self.call(messages, operation=operation, **kwargs)
        

def test_ingest_title_collision_rejection(wiki_dir: Path, monkeypatch):
    """Test N-01 title collision rejection during ingest."""
    # 1. Scaffold an existing page with title "Engine Specs"
    page = (wiki_dir / "wiki" / "concepts" / "engine.md")
    page.write_text("---\ntitle: Engine Specs\n---\nOld.")
    
    # 2. Mock provider to propose a NEW file with the same title
    mock_synth = json.dumps([
        {
            "path": "wiki/concepts/new-engine.md",
            "operation": "create",
            "reason": "duplicate",
            "content": "---\ntitle: Engine Specs\n---\nNew."
        }
    ])
    provider = MockLLMProvider(["[]", mock_synth, "[]"])
    
    # 3. Create dummy raw file
    raw_file = (wiki_dir / "raw" / "source.md")
    raw_file.write_text("# Dummy Source\n")
    
    # 4. Mock load_wiki_config to prevent NoneType errors on config
    from lib.config import WikiConfig
    monkeypatch.setattr("lib.wiki_ops.load_wiki_config", lambda d: WikiConfig(domain_name="test"))
    
    # Run ingest
    import asyncio
    applied = asyncio.run(run_ingest(raw_file, wiki_dir, provider=provider, auto_approve=True))
    
    # The file should be rejected because title 'engine specs' is already at 'wiki/concepts/engine.md'
    assert len(applied) == 0
    assert not (wiki_dir / "wiki" / "concepts" / "new-engine.md").exists()

def test_ingest_fabrication_rejection(wiki_dir: Path, monkeypatch):
    """Test N-04 numeric fabrication rejection during ingest."""
    mock_synth = json.dumps([
        {
            "path": "wiki/concepts/fabrication.md",
            "operation": "create",
            "reason": "fabrication",
            "content": "---\ntitle: Fabrication\n---\nContains a fake number: 9999."
        }
    ])
    provider = MockLLMProvider(["[]", mock_synth, "[]"])
    
    raw_file = (wiki_dir / "raw" / "source.md")
    # 9999 is NOT in the source!
    raw_file.write_text("# Dummy Source\nIt only has the number 42.\n")
    
    from lib.config import WikiConfig
    monkeypatch.setattr("lib.wiki_ops.load_wiki_config", lambda d: WikiConfig(domain_name="test"))
    
    import asyncio
    applied = asyncio.run(run_ingest(raw_file, wiki_dir, provider=provider, auto_approve=True))
    
    assert len(applied) == 0
    assert not (wiki_dir / "wiki" / "concepts" / "fabrication.md").exists()

def test_ingest_concurrency_ordering(wiki_dir: Path, monkeypatch):
    """Test that concurrent chunk extraction maintains chunk order."""
    import time
    from lib.providers import LLMProvider
    
    class TimingMockProvider(LLMProvider):
        def __init__(self):
            self.config = None
            self.extracted_json = None
            
        def call(self, messages, operation="ingest", **kwargs):
            prompt = messages[0]["content"]
            
            if "Combined Extractions" in prompt:
                # Capture the combined extractions json string passed to synthesis
                import re
                m = re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL)
                if m:
                    self.extracted_json = m.group(1)
                return "```json\n[]\n```"
                
            elif "Chunk" in prompt:
                import re
                m = re.search(r"Chunk (\d+) of", prompt)
                idx = int(m.group(1)) if m else 1
                time.sleep((5 - idx) * 0.1)  # Later chunks finish first
                return f"```json\n[{{\"chunk_id\": {idx}}}]\n```"
                
            return "```json\n[]\n```"

        async def acall(self, messages, operation="ingest", **kwargs):
            import asyncio
            prompt = messages[0]["content"]
            
            if "Combined Extractions" in prompt:
                return self.call(messages, operation=operation, **kwargs)
                
            elif "Chunk" in prompt:
                import re
                m = re.search(r"Chunk (\d+) of", prompt)
                idx = int(m.group(1)) if m else 1
                await asyncio.sleep((5 - idx) * 0.1)  # Later chunks finish first
                return f"```json\n[{{\"chunk_id\": {idx}}}]\n```"
                
            return "```json\n[]\n```"

    provider = TimingMockProvider()
    
    raw_file = (wiki_dir / "raw" / "source.md")
    raw_file.write_text("# H1\nText 1.\n# H2\nText 2.\n# H3\nText 3.\n# H4\nText 4.\n")
    
    from lib.config import WikiConfig
    monkeypatch.setattr("lib.wiki_ops.load_wiki_config", lambda d: WikiConfig(
        domain_name="test",
        ingest_settings={"chunk_strategy": "headers", "chunk_max_tokens": 5}
    ))
    
    import asyncio
    asyncio.run(run_ingest(raw_file, wiki_dir, provider=provider, auto_approve=True))
    
    # Verify that the synthesis prompt received the chunks in the correct order (1, 2, 3, 4)
    # despite chunk 4 finishing before chunk 1.
    assert provider.extracted_json is not None
    extractions = json.loads(provider.extracted_json)
    
    assert len(extractions) >= 4
    assert extractions[0][0]["chunk_id"] == 1
    assert extractions[1][0]["chunk_id"] == 2
    assert extractions[2][0]["chunk_id"] == 3
    assert extractions[3][0]["chunk_id"] == 4
