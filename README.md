# LLM Wiki Toolkit

A reusable Python CLI toolkit for building and maintaining LLM-powered knowledge wikis on Obsidian.

Inspired by [Andrej Karpathy's LLM Wiki concept](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Core Idea

Instead of retrieving raw document chunks at query time (RAG), the LLM **incrementally compiles and maintains a persistent wiki** — structured, interlinked markdown files. Knowledge is compiled once and kept current, not re-derived on every query.

> *"Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase."*

---

## 🏗 Architecture & Code Structure

The project is split into two halves: the **Toolkit** (this repository) containing the engine, and the **Wiki Instances** (Obsidian Vaults) which are generated and maintained by the toolkit.

```text
Toolkit (this repo)          Wiki Instance (Obsidian vault)
├── lib/                     ├── raw/          # Immutable sources
│   ├── config.py            ├── wiki/         # LLM-maintained pages
│   ├── differ.py            │   ├── .llm-wiki/# ChromaDB vectors & state
│   ├── providers.py         │   ├── index.md  # Master TOC
│   ├── splitter.py          │   ├── log.md    # Operation log
│   ├── utils.py             │   ├── overview.md
│   ├── vector_store.py      │   ├── sources/, concepts/, entities/
│   └── wiki_ops.py          │   └── topics/, comparisons/, queries/
├── cli/                     ├── .obsidian/    # Vault config & custom CSS
├── prompts/                 ├── page-templates/ # Markdown structures
├── templates/               ├── AGENTS.md     # LLM Editorial Schema
└── tests/                   └── wiki.yaml     # Instance config
```

### The `lib/` Core Engine

- **`providers.py`**: A robust wrapper around LiteLLM. It loads user-configured API keys (from `~/.config/llm-wiki/providers.yaml`) and supports **fallback chains**. If an API call rate-limits, it seamlessly falls back to Google or a local Ollama model.
- **`vector_store.py`**: Manages a local ChromaDB instance embedded in the vault for **Retrieval-Augmented Validation**. Provides lightning-fast semantic search for the linting engine.
- **`splitter.py`**: Handles document chunking using strategies like hierarchical headers.
- **`differ.py`**: The Human-in-the-loop (HITL) review engine. Renders a terminal UI for diffing proposed changes.
- **`utils.py`**: Handles markdown frontmatter parsing and wikilink extraction.
- **`wiki_ops.py`**: The orchestrator. Ties prompt templates, the vector DB, and the LLM provider together.

---

## 🧠 The Prompt Pipeline (How it works)

The true magic of the toolkit lies in the multi-stage LLM prompting pipeline defined in `prompts/`.

### 1. Ingestion (`wiki ingest`)
When a raw file is added, it passes through three distinct LLM operations:

1. **Extraction (`ingest_extract.md`)**: The raw document is chunked, and each chunk is fed to the LLM to extract a strict JSON payload containing entities, concepts, factual claims, and caveats. We force the LLM to include section references (`§2.1`) for every claim to prevent hallucinations.
2. **Synthesis (`ingest_synthesize.md`)**: The JSON extractions are concatenated and passed back to the LLM alongside the current `wiki/index.md`. The LLM decides what new pages need to be created (e.g., `concepts/regenerative-braking.md`). It generates full markdown pages adhering to strict rules (minimum word counts, kebab-case filenames, YAML frontmatter, and `[source: ...]` citations).
3. **Cross-Reference (`ingest_crossref.md`)**: The LLM analyzes existing wiki pages against the new extractions to find related pages and automatically update them with `[[wikilinks]]` and new information, ensuring the wiki is highly interconnected.

### 2. Querying (`wiki query`)
- **Answering (`query_answer.md`)**: The user asks a question. The toolkit loads the *compiled wiki pages* (not the raw sources) as context. The LLM answers the question, citing specific wiki pages.
- **File-back (`query_fileback.md`)**: If the user passes `--file-back`, the LLM converts the answer into a permanent markdown note and saves it to `wiki/queries/` so the knowledge is preserved.

### 3. Linting (`wiki lint`)
- **Structural Audit**: Python code scans the vault for broken `[[wikilinks]]` and orphan pages.
- **Retrieval-Augmented Semantic Audit (`lint_audit.md`)**: The engine embeds pages into ChromaDB and retrieves dynamically similar context. It prompts the LLM to identify contradictions, missing citations, or stale information without blowing up the context window. Resumable if interrupted.
- **Auto-Fix (`lint_fix.md`)**: If issues are found, the LLM proposes JSON patches to fix them.

---

## 💻 CLI Commands

Powered by `click`, the CLI provides a simple interface to manage the wiki lifecycle.

| Command | Description |
|---------|-------------|
| `wiki init <path>` | Scaffolds a new Obsidian vault, copying `templates/`, initializing git, and setting up custom CSS. |
| `wiki setup` | Interactive terminal wizard to configure LLM providers and API keys. |
| `wiki ingest <file>` | Runs the extract -> synthesize -> crossref pipeline on a raw document. Generates diffs for human review. |
| `wiki ingest --all` | Scans `raw/` and automatically ingests all files that aren't already in the `wiki/log.md`. |
| `wiki query "Q"` | Asks a grounded question against the compiled wiki. Pass `--file-back` to save it. |
| `wiki lint --fix` | Runs the structural and semantic audit, proposing LLM-generated fixes. |
| `wiki status` | Prints a summary of the vault (page counts, missing sources). |
| `wiki watch` | Runs as a daemon, polling `raw/` for new files and automatically compiling them in the background. |

---

## 🛡️ Trust & Verification Stack

Since LLMs hallucinate, the toolkit is built around a rigorous trust stack defined in the `AGENTS.md` constitution:

1. **Source provenance**: Every single factual claim must end in an inline citation (e.g., `[source: raw/article.md, §2.1]`).
2. **Confidence thresholds**: Pages are tagged with `confidence: low/medium/high`. A single source defaults to `medium`; it takes 3+ corroborating sources to achieve `high`.
3. **Immutable sources**: Files in `raw/` are NEVER modified by the LLM. If the wiki drifts, it can be entirely recompiled from scratch.
4. **Git history**: Every ingest, query file-back, and lint fix is automatically committed to git, providing a full audit trail and rollback capability.
5. **Human-in-the-loop**: The `differ.py` terminal UI ensures no file is written to the user's Obsidian vault without explicit human approval.

## Quick Start

```bash
cd ~/Desktop/llm\ wiki
pip install -e .

# Configure providers interactively
wiki setup

# Create a new wiki
wiki init ~/Documents/my-wiki --domain "My Knowledge Base"

# Ingest a document
wiki ingest ~/Documents/my-wiki/raw/my-article.md --wiki-path ~/Documents/my-wiki
```

## License
MIT

## 🚀 Recent Updates (V3 Agentic Features)

- **Semantic Chunking (Idea 6):** Mathematically detects topic shifts using cosine similarity between sliding sentence windows, preventing context fragmentation during ingestion.
- **Graph-Based Query Truncation (Idea 8):** When a query targets a specific entity, context retrieval is aggressively constrained to its 1st-degree topological neighbors, dramatically reducing context window bloat and improving precision.
- **Pre-LLM Context Pruning (Idea 4):** Strips HTML, heavily formatted markdown boilerplate, and simplifies URLs prior to embedding or prompting, saving tokens.
- **Topological Graph Linting (Idea 17):** Forces the LLM to cross-check explicit topological `[[wikilinks]]` during the semantic audit phase to detect logical contradictions between connected nodes.
- **Hybrid Search via `qmd` (Idea 10 & 15):** The CLI now integrates with `tobi/qmd` for BM25 + Vector hybrid search. `wiki query` automatically attempts `qmd search` subprocess calls before falling back to ChromaDB. Added `wiki mcp` command to expose the vault as an MCP server.
- **`llm.txt` Export (Idea 14):** Added `wiki export --format llm.txt` command to flatten the deeply interlinked wiki into a single, LLM-optimized markdown file for external AI ingestion.
- **Live Progress Dashboard (Idea 22):** Added a lightweight FastAPI + SSE background web server via `wiki dashboard` for rich, live-updating progress bars during long-running tasks.

## 🚀 Previous Updates (v0.1.1)

- **F-01 Fixed Broken Links Resolver:** Overhauled the wiki link resolution logic in `lib/wiki_ops.py` to correctly map `[[wikilinks]]` against the actual YAML `title` attributes rather than relying solely on file stems, eliminating false-positive "broken link" reports.
- **F-02 Test Suite Expansion:** Added comprehensive test coverage for `providers.py` (API placeholder detection and fallback chains), `vector_store.py` (ChromaDB persistence caching), and `cli/main.py` (CLI commands).
- **F-03 Vector Store Persistence:** Implemented `.mtime_cache.json` for the ChromaDB vector store. The lint process now intelligently checks file modification times and caches embeddings across sessions, saving compute and API costs.
- **F-04 Strict YAML Configuration:** `lib/config.py` now explicitly catches `yaml.YAMLError` and fails loudly rather than silently ignoring malformed `providers.yaml` configurations.
- **F-05 Robust Provider Selection:** Removed fragile `.startswith("sk-")` heuristics in `lib/providers.py` to prevent valid keys from being accidentally skipped during provider fallback operations.
