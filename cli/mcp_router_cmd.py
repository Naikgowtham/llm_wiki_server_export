"""Command to launch the Centralized Multi-Wiki MCP Router over SSE."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from rich.console import Console
import uvicorn

from lib.mcp_router import WikiRouterManager
from lib.providers import LLMProvider

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        MCPServer = None  # type: ignore

console = Console()
logger = logging.getLogger("mcp_router")


def create_mcp_router_server(base_dir: Path, provider: Optional[LLMProvider] = None) -> MCPServer:
    """Create and configure the MCPServer instance with all multi-wiki tools."""
    if MCPServer is None:
        raise RuntimeError("The 'mcp' Python SDK is not installed. Please run `pip install mcp`.")

    manager = WikiRouterManager(base_dir=base_dir, provider=provider)
    server = MCPServer(name="multi-wiki-router")

    @server.tool(description="List all available wikis on the server with descriptions and page counts.")
    def list_wikis() -> List[Dict[str, Any]]:
        return manager.list_wikis()

    @server.tool(description="Get the complete skeleton/table of contents of a wiki grouped by category (concepts, entities, topics, etc.).")
    def get_wiki_skeleton(wiki_name: str, category: Optional[str] = None) -> Dict[str, Any]:
        try:
            return manager.get_wiki_skeleton(wiki_name, category)
        except Exception as e:
            return {"error": str(e), "wiki_name": wiki_name}

    @server.tool(description="Get page and category statistics for a specific wiki.")
    def get_wiki_status(wiki_name: str) -> Dict[str, Any]:
        try:
            return manager.get_wiki_status(wiki_name)
        except Exception as e:
            return {"error": str(e), "wiki_name": wiki_name}

    @server.tool(description="Execute a semantic question-answering query against a specific wiki, returning a synthesized Markdown answer.")
    async def query_wiki(wiki_name: str, query: str) -> str:
        try:
            return await manager.query_wiki(wiki_name, query)
        except Exception as e:
            return f"Error executing query on wiki '{wiki_name}': {e}"

    @server.tool(description="Read the raw Markdown content of a specific page or note in a wiki.")
    def read_wiki_page(wiki_name: str, page_slug: str) -> str:
        try:
            return manager.read_wiki_page(wiki_name, page_slug)
        except Exception as e:
            return f"Error: {e}"

    @server.tool(description="Get outgoing [[wikilinks]] and incoming backlinks for a specific page to traverse the knowledge graph.")
    def get_page_links(wiki_name: str, page_slug: str) -> Dict[str, Any]:
        try:
            return manager.get_page_links(wiki_name, page_slug)
        except Exception as e:
            return {"error": str(e), "page": page_slug}

    @server.tool(description="Get deep metadata, summary, sources, and link connections for a specific entity or concept.")
    def get_entity_info(wiki_name: str, entity_name: str) -> Dict[str, Any]:
        try:
            return manager.get_entity_info(wiki_name, entity_name)
        except Exception as e:
            return {"error": str(e), "entity": entity_name}

    @server.tool(description="Safely create a new inbox note in raw/inbox/ of the wiki without causing Syncthing merge conflicts.")
    def add_inbox_note(wiki_name: str, title: str, content: str, tags: Optional[List[str]] = None) -> str:
        try:
            return manager.add_inbox_note(wiki_name, title, content, tags)
        except Exception as e:
            return f"Error adding inbox note to wiki '{wiki_name}': {e}"

    @server.tool(description="Append a timestamped research finding or agent note to wiki/log.md.")
    def append_to_log(wiki_name: str, entry_text: str) -> str:
        try:
            return manager.append_to_log(wiki_name, entry_text)
        except Exception as e:
            return f"Error appending to log in wiki '{wiki_name}': {e}"

    @server.tool(description="Search a wiki semantically using vector similarity without running a full LLM synthesis pass.")
    def search_wiki(wiki_name: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            return manager.search_wiki(wiki_name, query, limit)
        except Exception as e:
            return [{"error": str(e), "wiki_name": wiki_name}]

    @server.tool(description="Execute a federated semantic search across all discovered wikis on the server.")
    def search_all_wikis(query: str, limit_per_wiki: int = 3) -> Dict[str, Any]:
        try:
            return manager.search_all_wikis(query, limit_per_wiki)
        except Exception as e:
            return {"error": str(e), "query": query}

    @server.tool(description="Get recent page creations and modifications across one or all wikis.")
    def get_recent_changes(wiki_name: Optional[str] = None, days: int = 7, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            return manager.get_recent_changes(wiki_name, days, limit)
        except Exception as e:
            return [{"error": str(e)}]

    @server.tool(description="Analyze knowledge graph structure for a wiki: top authority hubs, orphan notes with no backlinks, and dead links.")
    def get_graph_insights(wiki_name: str) -> Dict[str, Any]:
        try:
            return manager.get_graph_insights(wiki_name)
        except Exception as e:
            return {"error": str(e), "wiki_name": wiki_name}

    @server.tool(description="Get router system health, cached vector store instances, and active provider rate-limit cooldowns.")
    def get_router_health() -> Dict[str, Any]:
        try:
            return manager.get_router_health()
        except Exception as e:
            return {"error": str(e)}

    # Attach manager to server for testing/introspection
    server._wiki_manager = manager  # type: ignore
    return server


@click.command("mcp-router")
@click.option("--base-dir", "-b", default=str(Path.home() / "wikis"), type=click.Path(), help="Base directory containing wiki vaults.")
@click.option("--host", "-h", default="100.99.243.66", help="Host/IP to bind the MCP server to (Tailscale IP).")
@click.option("--port", "-p", default=8765, type=int, help="Port to listen on (default: 8765).")
@click.option("--log-level", default="info", help="Log level (debug, info, warning, error).")
def mcp_router_cmd(base_dir: str, host: str, port: int, log_level: str):
    """Launch the Centralized Multi-Wiki MCP Router over SSE."""
    base_path = Path(base_dir).resolve()
    base_path.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold cyan]Starting Centralized Multi-Wiki MCP Router...[/bold cyan]")
    console.print(f"  • Base Directory: [yellow]{base_path}[/yellow]")
    console.print(f"  • Listening on:   [green]http://{host}:{port}/sse[/green]")
    console.print(f"  • Messages route: [green]http://{host}:{port}/messages/[/green]")

    server = create_mcp_router_server(base_path)
    app = server.sse_app(host=host)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
    )
