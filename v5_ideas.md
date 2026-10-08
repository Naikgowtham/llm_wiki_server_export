# LLM Wiki V5 — Future Optimization Ideas

This document tracks advanced architectural strategies designed to drastically reduce token usage and API costs during the `wiki ingest` process, and improve the user experience, for the next major version of LLM Wiki.

## 1. Vector-Automated Cross-Referencing
Currently, the third step of the ingestion pipeline (`ingest_crossref.md`) relies on the LLM to analyze new concepts and figure out which existing wiki pages should link to them.
*   **How it works:** Leverage the local **ChromaDB** vector engine. When a new entity or concept page is synthesized, the Python engine queries ChromaDB for the most semantically related existing pages. Python regex then automatically injects the `[[wikilink]]` into those pages.
*   **How it improves the system:** Eliminates the `ingest_crossref` LLM pass entirely, saving ~33% of total ingestion tokens.
*   **Pros:** Massive cost savings, deterministic cross-referencing, faster ingestion.
*   **Cons:** Vector similarity might insert links in grammatically awkward places, losing the context-aware natural placement an LLM provides.

## 2. Asymmetric Model Routing (Cheap Extractor, Expensive Writer)
Currently, a single heavy model is used for the entire ingestion pipeline.
*   **How it works:** Explicitly route the `ingest_extract` step to a highly efficient model like `gemini-1.5-flash` or a local `ollama/qwen3:8b`. Reserve the heavy model only for the `ingest_synthesize` step where writing quality matters.
*   **How it improves the system:** Reduces the cost of massive input context chunks by 80%+ while keeping final wiki quality high.
*   **Pros:** Drastically cheaper to run, faster extraction speeds.
*   **Cons:** If the cheap model misses critical facts during extraction, the expensive synthesizer model will never see them, leading to knowledge gaps.

## 3. CI/CD Git-Hook Automation
Currently, the user must manually trigger `wiki ingest` and `wiki lint` via the CLI.
*   **How it works:** Create a `wiki install-hooks` command that writes a `.git/hooks/post-commit` file directly into the Obsidian vault. 
*   **How it improves the system:** Turns Obsidian into a Continuous Integration engine. Saving or syncing a new file silently triggers the pipeline in the background.
*   **Pros:** Seamless, invisible user experience; the wiki maintains itself automatically.
*   **Cons:** Background processing might consume API quota unexpectedly without the user explicitly initiating it.

## 4. Real-Time Streaming Output (UX)
Waiting minutes for a heavy model to synthesize a massive page creates a frozen terminal experience.
*   **How it works:** Enable `stream=True` in the LiteLLM configuration for `ingest_synthesize` and `query` operations, printing chunks as they arrive.
*   **How it improves the system:** The toolkit will stream the LLM's generated markdown directly to the terminal in real-time.
*   **Pros:** Drastically improves perceived performance and user experience.
*   **Cons:** Complicates JSON parsing because the system must wait for the full stream to complete before parsing structured data.

## 5. Multimodal Vision Ingestion (PDFs & Images)
Currently, raw sources must be text-based Markdown.
*   **How it works:** Integrate local Vision models (like `llava`). When an image is ingested, the engine asks the Vision model to convert charts into Markdown Tables or descriptive text.
*   **How it improves the system:** Allows the wiki to ingest visual data alongside text data.
*   **Pros:** Vastly expands the types of documents the wiki can ingest and understand.
*   **Cons:** Vision models are slow, expensive, and frequently hallucinate specific numbers in complex charts.

## 6. Dynamic Map of Content (MOC) Generation
A single `wiki/index.md` file will become an unreadable mega-list as the vault grows.
*   **How it works:** Train the LLM on taxonomic hierarchies. The toolkit dynamically generates Obsidian MOC dashboards (e.g., `Powertrains_Dashboard.md`) that automatically organize sub-concepts.
*   **How it improves the system:** Keeps the vault highly organized and navigable for humans.
*   **Pros:** Incredible structural clarity for large knowledge bases.
*   **Cons:** High token cost to regenerate multiple MOCs whenever new concepts are introduced.

## 7. Confidence-Weighted Lint Queues
All pages are currently treated equally during the audit phase.
*   **How it works:** Prioritize the linting queue based on the YAML frontmatter `confidence:` metric. Pages marked `contested` or `low` are linted immediately, while `high` confidence pages are audited rarely.
*   **How it improves the system:** Focuses the LLM's compute power exactly where the knowledge base is weakest.
*   **Pros:** Massively optimizes API spend by not re-verifying already proven facts.
*   **Cons:** High-confidence pages might silently drift out of date if external sources contradict them and they aren't checked frequently.

## 8. Pure Python Offloading (Zero-Token Mechanical Checks)
The `lint_audit.md` prompt still relies on the LLM to flag some mechanical issues.
*   **How it works:** Offload all mechanical tasks to pure Python. Use Python regex to find missing `[source: ...]` tags and validate index integrity.
*   **How it improves the system:** Removes mechanical instructions from the LLM prompt.
*   **Pros:** Faster, 100% deterministic, and cheaper.
*   **Cons:** Brittle; regex might fail on edge-case formatting that an LLM would easily understand.

## 9. Git Pre-Commit Micro-Linting
Batch linting the entire vault takes a long time.
*   **How it works:** Integrate `wiki lint` into a Git Pre-Commit Hook. The hook fires and *only* lints the specific files staged in that commit.
*   **How it improves the system:** Breaks the monolithic linting process down into invisible, 10-second micro-checks.
*   **Pros:** Immediate feedback loop during authoring; prevents bad data from ever entering the repo.
*   **Cons:** Slows down the `git commit` process, which can be frustrating for developers used to instant commits.
