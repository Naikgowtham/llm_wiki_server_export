import pytest
import asyncio
from pathlib import Path
from cli.mcp_router_cmd import create_mcp_router_server


@pytest.fixture
def mock_vault(tmp_path):
    base = tmp_path / "wikis"
    vault = base / "test-wiki"
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "wiki.yaml").write_text("domain:\n  name: Test\n  description: Mock description\n")
    (vault / "wiki" / "concepts" / "test.md").write_text("# Test\nContent here.")
    return base


def test_create_mcp_router_server(mock_vault):
    server = create_mcp_router_server(mock_vault)
    assert server is not None

    # Verify registered tools
    tools = asyncio.run(server.list_tools())
    tool_names = [t.name for t in tools]

    expected = [
        "list_wikis",
        "get_wiki_skeleton",
        "get_wiki_status",
        "query_wiki",
        "read_wiki_page",
        "get_page_links",
        "get_entity_info",
        "add_inbox_note",
        "append_to_log",
        "search_wiki",
        "search_all_wikis",
        "get_recent_changes",
        "get_graph_insights",
        "get_router_health",
    ]

    for exp in expected:
        assert exp in tool_names, f"Expected tool '{exp}' not found in registered tools: {tool_names}"


def test_mcp_router_sse_app_generation(mock_vault):
    server = create_mcp_router_server(mock_vault)
    app = server.sse_app(host="100.99.243.66")
    assert app is not None
