# Setting up the LLM Wiki MCP Server

This guide explains how to expose your LLM Wiki as a Model Context Protocol (MCP) server so that AI agents (like Antigravity, Cursor, or Cline) can natively search and interact with your knowledge base.

## 1. Prerequisites

The `llm wiki` uses `tobi/qmd` for fast, local hybrid search (BM25 + vector similarity).

First, install [Bun](https://bun.sh/) if you haven't already:
```bash
curl -fsSL https://bun.sh/install | bash
```
*(Make sure to source your profile or restart your terminal so `bun` is in your `$PATH`.)*

Next, install `tobi/qmd` globally:
```bash
bun install -g node-gyp
bun install -g https://github.com/tobi/qmd
```
*(Note: Installing `node-gyp` first ensures that native SQLite dependencies compile correctly during the qmd installation.)*

## 2. Index Your Wiki

Before the MCP server can search your wiki, it needs to be indexed into `qmd`. Run the following command, pointing to your compiled `wiki/` directory:

```bash
# Add the wiki to the qmd index
qmd collection add /path/to/your/wiki-instance/wiki
```

*Note: The CLI command `wiki mcp` automates this process when launching the server.*

## 3. Configure Your Agent

To connect an AI agent to this MCP server, you need to configure its MCP settings. 

### For Antigravity (AGY)

Create or edit the global MCP configuration file at `~/.gemini/config/mcp_config.json`:

```json
{
  "mcpServers": {
    "llm-wiki-search": {
      "command": "/home/ghost/.bun/bin/qmd",
      "args": ["mcp"],
      "env": {
        "PATH": "/home/ghost/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      }
    }
  }
}
```

*Important details for Antigravity:*
- Ensure the `command` points to the absolute path of the `qmd` binary (usually inside `~/.bun/bin/`).
- Because Antigravity's Language Server spawns the process in a strict environment, providing the explicit `PATH` inside the `env` block is crucial so `qmd` can execute `bun` under the hood.

### For Claude Desktop / Cursor

Add a similar configuration to your `claude_desktop_config.json` or Cursor MCP settings using Stdio transport:

```json
{
  "mcpServers": {
    "llm-wiki": {
      "command": "qmd",
      "args": ["mcp"]
    }
  }
}
```

## 4. How It Works

Once configured, your agent will automatically:
1. Connect to the `qmd` process via standard input/output (Stdio).
2. Discover tools like `qmd_query`, `qmd_search`, and `qmd_get`.
3. Inject these tools into its system prompt.
4. Execute searches and fetch specific Markdown pages on the fly when answering your questions!

## 5. Managing Multiple Wikis (Multiple Global MCP Servers)

If you maintain multiple wikis on the same system (e.g., a "Vehicles Wiki" and a "Tech Wiki") and want your AI agents to have simultaneous access to all of them, the recommended approach is configuring **Multiple Global MCP Servers**.

You can register each wiki as an independent MCP server in your `mcp_config.json` file. The agent will automatically prefix the exposed tools with the server name (e.g., `vehicles_wiki_query` vs `tech_wiki_query`), allowing it to route its searches to the correct knowledge base contextually.

Example `~/.gemini/config/mcp_config.json` for multiple wikis:

```json
{
  "mcpServers": {
    "vehicles-wiki": {
      "command": "/home/ghost/.bun/bin/qmd",
      "args": ["mcp", "-d", "/path/to/vehicles-wiki/wiki"],
      "env": {
        "PATH": "/home/ghost/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      }
    },
    "tech-wiki": {
      "command": "/home/ghost/.bun/bin/qmd",
      "args": ["mcp", "-d", "/path/to/tech-wiki/wiki"],
      "env": {
        "PATH": "/home/ghost/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      }
    }
  }
}
```

By providing the `-d /path/to/wiki` argument, you explicitly constrain each `qmd` MCP instance to serve only its respective wiki directory. The AI agent will seamlessly integrate these parallel toolsets into its prompt.
