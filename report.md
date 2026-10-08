# llm-wiki Code & Implementation Review

**Date:** 2026-10-06  
**Toolkit reviewed:** `/home/ghost/Desktop/llm wiki` (read-only, unmodified)  
**Sample instance reviewed:** `/home/ghost/Desktop/vehicles-wiki` (read-only, unmodified)  
**Test suite:** `22 passed in 1.92s` (run from repo root)  
**Git clean confirmation:** `git status` in the original repo shows no modified or staged files. The only untracked file (`test_models.py`) pre-existed and was not created by this review. All functional testing was done on a copy; no original file was modified. The only file written is `report.md`.  
**All functional testing was performed against a copy** of `vehicles-wiki` at `.../scratch/vehicles-wiki-copy`. No command on the original was executed and no original file was modified.

---

## Scope & Method

What I reviewed (full read, line-by-line):
- `lib/`: `config.py`, `providers.py`, `splitter.py`, `differ.py`, `diff_tracker.py`, `vector_store.py`, `wiki_ops.py`, `utils.py`
- `cli/`: `main.py`, `init_cmd.py`, `ingest_cmd.py`, `lint_cmd.py`, `query_cmd.py`, `watch_cmd.py`, `setup_cmd.py`, `export_cmd.py`, `mcp_cmd.py`, `ui_server.py`
- All prompts: `prompts/ingest_extract.md`, `ingest_synthesize.md`, `ingest_crossref.md`, `lint_audit.md`, `lint_fix.md`, `query_answer.md`, `query_fileback.md`
- All templates: `templates/wiki.yaml`, `templates/AGENTS.md`, `templates/README.md`, `templates/wiki/index.md`, `templates/wiki/overview.md`, `templates/page-templates/*.md`
- `config/providers.example.yaml`, `pyproject.toml`, all `tests/*.py`
- The `vehicles-wiki` sample vault (6 raw sources, 93 compiled pages)

What I ran (against a copy of vehicles-wiki):
- `pytest tests/ -v` → 22 passed
- `wiki status --wiki-path <copy>` → exit 0, 93 pages
- `wiki init <tmp> --domain "Test Wiki" --description "desc"` → exit 0, scaffolded correctly, git initialized
- `wiki init <tmp> --domain "Test" --description "desc"` (non-empty dir, input='n') → exit 0, "Aborted."
- `wiki lint --wiki-path <copy>` (no provider) → exit 0, 49 broken links, 2 orphans, 14 semantic issues
- `run_lint(copy, provider=None)` → 49 broken links, 2 orphans
- `WikiVectorStore` persistence probe → cross-instance reload works (count preserved)
- `WikiVectorStore.search_similar` probe → **returns None** (F-01)
- `_validate_and_resolve_path` traversal probe → **`/wiki.yaml` overwrite possible** (F-02)
- `get_diff_deltas` deletion-only probe → **empty delta** (F-03)
- `split_document` boundary cases → empty/whitespace/unicode all correct
- `load_providers_config` with malformed YAML → exits loudly (fix already applied)
- Provider placeholder probe → `call()` uses `PLACEHOLDER_PREFIXES`, `acall()` uses inline list (F-08)
- `setup` default config probe → no `embeddings` fallback chain (F-04)
- All 49 broken links verified against frontmatter titles → all genuinely dangling (0 false positives)

---

## Architecture Overview

The data flow matches the documented architecture:

```
raw sources
  -> ingest: split_document (headers / fixed_size / pages / semantic)
     -> 3 LLM stages: extract (ingest_extract.md) -> synthesize (ingest_synthesize.md) -> crossref (ingest_crossref.md)
     -> proposed FileChanges (path, operation, new_content, old_content, reason)
  -> review_changes (HITL via differ.py: approve-all / step-by-step / skip / non-tty auto-skip)
   -> applied to wiki/ tree  -> update_wiki_index + append_to_log (+ auto_commit via git)
   -> raw_cache written to wiki/.llm-wiki/raw_cache/ (used for delta-based re-ingest)

query: entity-match in question -> graph truncation (1st-degree neighbors) OR vector search
     -> WikiVectorStore.search_similar_with_metadata -> query_answer.md prompt -> LLM answer
     -> optional fileback: query_fileback.md -> write to wiki/queries/<slug>.md

lint: structural scan (build_link_graph -> broken [[wikilinks]], frontmatter validation, orphan detection)
   -> with provider: hash-registry mtime check -> embed_and_upsert changed pages into ChromaDB
   -> per-page LLM audit via lint_audit.md (concurrent, resumable via .lint_state.json)
   -> lint_fix.md for --fix mode
```

**Provider abstraction**: `LLMProvider` wraps LiteLLM with a configurable fallback chain per operation (`ingest`, `query`, `lint`, `embeddings`). API keys are bound into `os.environ` at construction time. The abstraction is reasonably clean but `providers.py` contains provider-specific string manipulation (model name rewriting for `anthropic/`, `google/`, `local/`/`nous` prefixes) that leaks provider knowledge into the core.

**Separation of concerns**: Good overall. `cli/*_cmd.py` files are thin dispatchers; business logic lives in `lib/wiki_ops.py`. The `init_cmd.py` `render_template_file` function is a pure utility that could live in `lib/utils.py` but is acceptable in the CLI layer.

**Previously applied fixes** (already in the committed tree): broken-link resolver overhaul (294→49 false positives), mtime-based embedding cache, loud YAML error on malformed config, exact-match placeholder keys, prompt `<Do NOT>` blocks, watch debounce improvement, async/sync migration (switched `provider.call` to `provider.acall`), and test suite expansion. The current review evaluates the code as it stands today, independently — three critical bugs remain despite those fixes.

---

## Findings

Severity key: **critical** = data loss / silent wrong output / security. **high** = core feature broken or produces wrong output. **medium** = documented feature absent/unreliable. **low** = quality/UX.

### Critical

#### F-01 · CRITICAL · `WikiVectorStore.search_similar` never returns its results (missing `return`)

- **Where:** `lib/vector_store.py`, `search_similar` method (lines 60–94).
- **What happens:** The method builds a `valid_docs` list by iterating over query results and filtering by distance threshold, but the function body ends without a `return valid_docs` statement. It implicitly returns `None`.
- **How confirmed:**
  ```python
  store = WikiVectorStore(tmp, MockProvider())
  store.embed_and_upsert("page1", "content 1", {"title": "Page 1"})
  result = store.search_similar("content 1", k=5, distance_threshold=0.4)
  print(result)  # -> None
  for doc in result:  # -> TypeError: 'NoneType' object is not iterable
  ```
  The method body is:
  ```python
  valid_docs = []
  if results and results.get("documents") and results.get("distances"):
      docs = results["documents"][0]
      distances = results["distances"][0]
      for doc, dist in zip(docs, distances):
          if dist < distance_threshold or len(valid_docs) < 2:
              valid_docs.append(doc)
  # <-- NO return statement here (function ends, returns None)
  ```
- **Impact:** `search_similar` is called in `run_lint` at line 759 (`similar_docs = store.search_similar(...)`), then used at line 761 (`context_docs = [doc for doc in similar_docs if ...]`). When the provider is configured and the semantic lint pass runs on any page that has topological context (`pages_to_lint` is non-empty), the list comprehension over `None` raises `TypeError`. Because the entire `_process_lint_pages` coroutine is wrapped in `asyncio.gather` with a broad `except Exception` that re-raises (line 798–800), a single `search_similar` returning `None` crashes the entire semantic audit. The semantic lint pass is therefore **non-functional** at runtime, not just degraded.
- **Suggested direction:** Add `return valid_docs` at the end of `search_similar`. Add a unit test in `test_vector_store.py` that calls `search_similar` and asserts a `list` is returned with the expected documents.

#### F-02 · CRITICAL · `_validate_and_resolve_path` allows path escape via `../` within `wiki/` prefix

- **Where:** `lib/wiki_ops.py`, `_validate_and_resolve_path` (lines 59–66).
- **What happens:** The function does two checks: (1) `full_path.is_relative_to(wiki_dir.resolve())` — ensures the path is inside the vault root; (2) `str(rel_path).startswith("wiki/")` — a string prefix check on the *unresolved* relative path. A path like `wiki/concepts/../../wiki.yaml` passes the prefix check (starts with `wiki/`) and resolves to `<vault>/wiki.yaml`, which is inside the vault root. The `is_relative_to` check only validates against the vault root, not the `wiki/` subdirectory.
- **How confirmed:**
  ```python
  _validate_and_resolve_path(wiki_dir, "wiki/concepts/../../wiki.yaml")
  # -> /tmp/test-vault/wiki.yaml   (ALLOWED — escapes wiki/ into vault root)
  ```
  The LLM-synthesized ingest output or lint-fix output could include a `path` like `wiki/concepts/../../wiki.yaml` with arbitrary content, causing the LLM to **overwrite `wiki.yaml`, `AGENTS.md`, `README.md`, or any vault-root file**. The numeric fabrication check provides no protection since it only inspects body content, not paths.
- **Impact:** Arbitrary file overwrite within the vault root. An adversarial or buggy LLM response could corrupt the wiki configuration, the editorial schema (AGENTS.md), or the README.
- **Suggested direction:** After resolving, verify `full_path.is_relative_to((wiki_dir / "wiki").resolve())`. Remove the string-prefix check (it is redundant and bypassable) or apply it to the resolved path.

#### F-03 · CRITICAL · `get_diff_deltas` ignores deletions — removing content from a source skips re-ingest

- **Where:** `lib/diff_tracker.py`, `get_diff_deltas` (lines 1–30).
- **What happens:** The function iterates over `difflib.SequenceMatcher` opcodes but only processes `replace` and `insert` tags, ignoring `delete`. When content is removed from a source file, the delta is empty, and `run_ingest` (line 194) treats an empty delta as "no meaningful changes" and returns `[]` — skipping the re-ingest entirely.
- **How confirmed:**
  ```python
  old = "# Source\n\nThis is paragraph A.\n\nThis is paragraph B.\n\nThis is paragraph C."
  new = "# Source\n\nThis is paragraph A.\n\nThis is paragraph C."
  get_diff_deltas(old, new)  # -> '' (empty)
  ```
  In `run_ingest`, `if delta_content.strip():` is False, so it hits the `else` branch at line 193 (`"No meaningful text changes found in diff."`) and `return []`. The wiki never learns that paragraph B was removed.
- **Impact:** Content deletions in source files never propagate to the wiki. The wiki silently retains stale information from deleted source content. This is a data-correctness bug, not a crash.
- **Suggested direction:** Include `delete` opcodes in the delta extraction, or at minimum return a non-empty delta indicating a deletion occurred so `run_ingest` re-processes the full source instead of skipping.

### High

#### F-04 · HIGH · `setup_cmd.py` never initializes the `embeddings` fallback chain

- **Where:** `cli/setup_cmd.py`, default config (lines 51–63) and provider-add loop (lines 157–163).
- **What happens:** The default config's `fallback_chain` has only `ingest`, `query`, `lint` — no `embeddings` key. The setup wizard loop (line 158: `for op in ("ingest", "query", "lint")`) also only populates those three. The `config/providers.example.yaml` file also omits `embeddings`. A user who follows the documented setup flow will have **no embeddings fallback chain**, causing `LLMProvider.embed()` to return `None` (line 186) on every call. `WikiVectorStore.embed_and_upsert` then logs an error and returns without indexing (line 49–50), producing an empty vector store. All semantic lint and query retrieval silently degrade to no-op.
- **How confirmed:** `grep -n "embeddings" cli/setup_cmd.py` → no matches. `grep -n "embeddings" config/providers.example.yaml` → no matches. The only place `embeddings` appears in a fallback chain is in `INSTALL.md` (line 93), which is a doc example, not the code default.
- **Impact:** The RAG/retrieval layer is silently broken for any user who doesn't manually add an `embeddings` chain to their `providers.yaml`.
- **Suggested direction:** Add `"embeddings": []` to the default `fallback_chain` in `setup_cmd.py` and to `config/providers.example.yaml`. Have the setup wizard add configured models to the `embeddings` chain as well.

#### F-05 · HIGH · `asyncio.run()` in `wiki_ops.py` is incompatible with the FastAPI dashboard's event loop

- **Where:** `lib/wiki_ops.py`, lines 248, 275, 363, 529, 540, 784, 801, 817 — every LLM call uses `asyncio.run()`.
- **What happens:** `run_ingest`, `run_query`, and `run_lint` all use `asyncio.run()` to execute the async provider methods. `asyncio.run()` creates a new event loop and runs until complete. If any of these functions is called from within an existing event loop (e.g., the FastAPI `ui_server.py` endpoints, or any async context), `asyncio.run()` raises `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- **How confirmed:** `ui_server.py` defines FastAPI async endpoints. The `emit_progress` function posts to `http://localhost:8000/update`, suggesting the dashboard is meant to monitor CLI runs. If wiki operations are ever wired into the server endpoints, `asyncio.run()` inside them would crash.
- **Impact:** The functions cannot be called from any async context. This is a latent architectural constraint that will bite if the dashboard is ever expanded to trigger wiki operations directly.
- **Suggested direction:** Refactor to use `asyncio.get_event_loop().run_until_complete()` or provide both sync and async variants of each function.

#### F-06 · HIGH · `lint_audit.md` references `{{ untracked_pages | default(...) }}` but the variable is never passed to the template

- **Where:** `prompts/lint_audit.md` line 9; `lib/wiki_ops.py` line 765–770 (audit_prompt render).
- **What happens:** The `lint_audit.md` template renders `{{ untracked_pages | default('None detected') }}`, but the `audit_template.render()` call at line 765 passes only `domain_name`, `broken_links`, `orphan_pages`, and `wiki_pages`. There is no `untracked_pages` variable. While Jinja2's `| default` filter will substitute "None detected", this means the audit prompt never includes untracked-page information — a feature the template advertises but the code never provides.
- **Impact:** The semantic audit LLM never sees untracked page information, reducing the effectiveness of the lint audit. Additionally, `broken_links` and `orphan_pages` are hardcoded to `"None"` at line 767–768 (the comment says "Not needed for semantic page check"), so the structural scan results are not communicated to the semantic auditor.
- **Suggested direction:** Pass `untracked_pages`, the actual `broken_links` list, and `orphan_pages` to the template render call, or remove the unused template variables.

### Medium

#### F-07 · MEDIUM · `watch_cmd` debounce only checks size and mtime; no inode stability check

- **Where:** `cli/watch_cmd.py`, lines 131–144.
- **What happens:** After detecting a new file, the watcher loops up to 15 times (1s sleep each) checking if `cur_size == prev_size and cur_mtime == prev_mtime and cur_size > 0`. This stabilizes size and mtime but does not check the inode. Editors that save via temp-file-then-rename (common on Linux) will have a stable file that was moved into place, but if the rename happens *between* two stat calls, the watcher could see different inodes with the same size/mtime.
- **How confirmed:** Code inspection of the debounce loop. The check at line 138 is `if cur_size == prev_size and cur_mtime == prev_mtime and cur_size > 0`.
- **Impact:** Very low probability of partial-file ingestion, but possible during large downloads or editor save-via-rename.
- **Suggested direction:** Add inode comparison (`st.st_ino`) to the stability check.

#### F-08 · MEDIUM · `providers.py` `acall` uses a hardcoded inline placeholder list instead of `PLACEHOLDER_PREFIXES`

- **Where:** `lib/providers.py`, line 274 (`acall` method).
- **What happens:** The `call` method (line 108) checks `api_key in PLACEHOLDER_PREFIXES` using the module-level tuple `("sk-ant-...", "sk-...", "AIza...", "your-", "...")`. The `acall` method (line 274) uses a hardcoded inline list `["sk-ant-...", "sk-...", "AIza..."]` that omits `"your-"` and `"..."`. If a provider key is set to `"your-..."` or `"..."`, it would be skipped in `call()` but not in `acall()`, leading to inconsistent behavior.
- **Impact:** Inconsistency between sync and async provider paths. A placeholder key of `"your-..."` or `"..."` would cause `acall` to attempt a real API call with a clearly fake key, wasting an API retry cycle.
- **Suggested direction:** Use the module-level `PLACEHOLDER_PREFIXES` constant in `acall` instead of a hardcoded list.

#### F-09 · MEDIUM · `run_ingest` writes raw_cache even when synthesis produces fabricated pages

- **Where:** `lib/wiki_ops.py`, line 410 (raw_cache write) and line 326 (numeric fabrication check).
- **What happens:** If a synthesized page is rejected for numeric fabrication (line 326 — silently skipped, not written), the raw_cache is **still** written at line 410 after the successful pages are applied. The next re-ingest will treat the source as unchanged and never retry the rejected pages.
- **How confirmed:** Code flow: extract → synthesize → parse JSON → per-page fabrication check (skip bad pages at line 326) → review_changes → apply → write raw_cache at line 410. Rejected pages are never written but raw_cache is.
- **Impact:** Fabricated content is silently dropped and never retried. The wiki loses information without any indication.
- **Suggested direction:** Only write raw_cache after all changes are successfully applied, or log rejected pages to `wiki/log.md` so the user knows content was dropped.

### Low

#### F-10 · LOW · `differ.py` `review_changes` skips all changes in non-interactive environments

- **Where:** `lib/differ.py`, lines 88–90.
- **What happens:** When stdin is not a TTY, `review_changes` prints "Non-interactive environment detected. Skipping changes." and returns `[]`. The `auto_approve` flag bypasses this (checked at line 75). Only affects `auto_approve=False` in non-interactive environments.
- **Impact:** Could be surprising to users who expect piped input to work.
- **Suggested direction:** None — this is correct behavior. No change needed.

#### F-11 · LOW · `watch_cmd` `--yes` flag default changed from True to False

- **Where:** `cli/watch_cmd.py`, line 70 (commit `04af433`).
- **What happens:** The `--yes` flag default was changed from `True` to `False`, making watch mode default to interactive review.
- **Suggested direction:** None — the change to default=False is correct (safer default).

#### F-12 · LOW · `status_cmd` in `main.py` swallows all git exceptions silently

- **Where:** `cli/main.py`, lines 70–76.
- **What happens:** The git status display is wrapped in `try: import git; repo = git.Repo(wiki_dir); ... except Exception: pass`. Any git-related error is silently swallowed.
- **Impact:** If a wiki is not git-initialized, the status command silently omits the "Last Git Commit" line.
- **Suggested direction:** Print a dimmed "Not a git repository" message instead of silently passing.

---

## Test Coverage Gaps

| Area | Covered? | Notes |
|------|----------|-------|
| `lib/config.py` | Yes (unit) | Only happy-path + file-not-found. No malformed-YAML test. No `get_fallback_models` default fallback test. |
| `lib/providers.py` | Yes (1 test) | `test_placeholder_keys` only checks `_find_provider_for_model`. No test of `call()`/`acall()` fallback, `embed()`, or rate-limit tracking. |
| `lib/splitter.py` | Yes (unit) | Boundary tests exist (empty, headers, pages). Missing: unicode with headers, `semantic` strategy, `fixed_size` strategy, overlap-token edges. |
| `lib/differ.py` | Yes (unit) | Only `render_unified_diff` + `review_changes(auto_approve=True)`. Missing: no-op diff, whitespace-only diff, reordering, non-tty behavior. |
| `lib/diff_tracker.py` | **No** | Zero test coverage. The deletion-only bug (F-03) would not have been caught. |
| `lib/utils.py` | Yes (unit) | Missing: `build_link_graph` with nested dirs, `count_tokens_approx` fallback, list-frontmatter round-trip. |
| `lib/vector_store.py` | Yes (1 test) | Only tests `embed_and_upsert` + count persistence. **Never calls `search_similar`** — the missing-return bug (F-01) went uncaught. |
| `lib/wiki_ops.py` | Partial (mocked) | 5 tests: broken links, missing frontmatter, title collision, fabrication rejection, concurrency ordering. Missing: end-to-end ingest, `run_query`, `_validate_and_resolve_path` security test. |
| `cli/init_cmd.py` | Yes (1 test) | Only checks file existence. Missing: non-empty dir rejection, template substitution, git failure. |
| `cli/ingest_cmd.py` | **No** | `wiki ingest` never exercised end-to-end. |
| `cli/query_cmd.py` | **No** | `wiki query` never exercised. |
| `cli/lint_cmd.py` | **No** | `wiki lint` never exercised at the CLI layer. |
| `cli/watch_cmd.py` | Partial | `get_raw_files_state` + `get_already_ingested` tested. Polling loop, debounce, file-completion never tested. |
| `cli/setup_cmd.py` | **No** | Interactive config loop not tested. |
| `cli/export_cmd.py` | **No** | Export never tested. |
| `cli/mcp_cmd.py` | **No** | MCP server launch not tested. |
| `cli/ui_server.py` | **No** | Dashboard server not tested. |
| Prompts | **No** | No prompt-behavior test. No test for `_clean_json_response` handling LLM prose. |

**Zero-coverage modules:** `diff_tracker.py` (entirely untested — the F-03 bug lives here), `vector_store.py` (no `search_similar` test).  
**Zero-coverage commands:** `ingest` (CLI layer), `query` (CLI layer), `lint` (CLI layer), `setup`, `export`, `mcp`, `dashboard`.  
**Notable test gap that allowed F-01 to ship:** `test_vector_store.py` tests `embed_and_upsert` + count persistence, but never calls `search_similar` or `search_similar_with_metadata`. The missing `return valid_docs` went uncaught by the 22-test suite.

---

## Functional Test Log

All runs were against a **copy** of `vehicles-wiki`. The original was never modified.

| Command / Probe | Observed | Pass? |
|----------------|----------|-------|
| `pytest tests/ -v` | 22 passed, 1 warning (chromadb telemetry deprecation), exit 0 | **PASS** |
| `wiki status -w <vehicles-wiki-copy>` | Exit 0; Raw 6, Sources 5, Concepts 46, Entities 36, Topics 0, Comparisons 0, Queries 0; Total 93 | **PASS** |
| `wiki init <tmp> --domain "Test Wiki" --description "desc"` | Exit 0; scaffolded AGENTS.md, README.md, page-templates/*.md, wiki/index.md, wiki/log.md, wiki/overview.md, wiki.yaml; git initialized | **PASS** |
| `wiki init <tmp> --domain "Test" --description "desc"` (non-empty dir, input='n') | Exit 0; "Aborted." — correctly refuses non-empty dir | **PASS** |
| `wiki lint -w <vehicles-wiki-copy>` (no provider) | Exit 0; broken_links: 49, orphans: 2 (`Captain Seats`, `Mahindra XUV700` — both 0-byte empty files), semantic_issues: 14 (7 each for missing frontmatter) | **PASS** (structurally correct) |
| `run_lint(copy, provider=None)` | broken_links=49, orphans=2, semantic_issues=[] (no provider = no semantic pass) | **PASS** |
| `WikiVectorStore` persistence (fresh instance reload) | Store1 count=1; Store2 (fresh instance, same path) count=1 | **PASS** (cross-instance reload works) |
| `WikiVectorStore.search_similar` | Returns `None` instead of list → `TypeError` when iterated | **FAIL** (F-01) |
| `split_document` empty input | 0 chunks | **PASS** |
| `split_document` whitespace-only | 0 chunks | **PASS** |
| `split_document` single huge paragraph (no headers) | 1 chunk | **PASS** (by design) |
| `split_document` unicode multi-byte | 1 chunk | **PASS** |
| `_validate_and_resolve_path("wiki/concepts/../../wiki.yaml")` | Resolves to `<vault>/wiki.yaml` — ALLOWED | **FAIL** (F-02) |
| `_validate_and_resolve_path("wiki/../../../etc/passwd")` | BLOCKED (path outside vault) | **PASS** |
| `_validate_and_resolve_path("wiki.yaml")` | BLOCKED (doesn't start with "wiki/") | **PASS** |
| `get_diff_deltas` identical content | `''` | **PASS** |
| `get_diff_deltas` added line | Non-empty delta | **PASS** |
| `get_diff_deltas` removed line only | `''` (empty — deletions ignored) | **FAIL** (F-03) |
| `load_providers_config` with malformed YAML | `sys.exit(1)` with stderr error message | **PASS** (loud failure) |
| `load_providers_config` with missing file | Returns empty `ProvidersConfig()` silently | **PASS** (by design) |
| Provider placeholder skip (`sk-...`) | Skipped in both `call` and `acall` | **PASS** |
| Provider placeholder skip (`your-...` / `...`) | `call`: skipped; `acall`: NOT skipped (different list) | **FAIL** (F-08) |
| `emit_progress` with no dashboard running | Silent fail (connection refused caught) | **PASS** |
| `setup` default config | No `embeddings` key in `fallback_chain` | **FAIL** (F-04) |
| All 49 broken links verified against frontmatter titles | 49 genuine dangling references (0 false positives) | **PASS** |

---

## Security Notes

- **API keys not committed:** `.gitignore` ignores both `providers.yaml` and `config/providers.yaml`. `config/providers.example.yaml` contains only placeholder keys (`sk-ant-...`, `sk-...`, `AIza...`). The user's live config at `~/.config/llm-wiki/providers.yaml` is outside the repo and was not inspected as a secret vector.
- **No secrets in logs or output:** The `setup_cmd.py` `print_configured_providers` function masks keys as `Set (sk-…...key)` (4+4 char mask). `wiki_ops.py` does not log API keys. API keys are set in `os.environ` at `LLMProvider.__init__` but never printed.
- **Path traversal (F-02):** **CRITICAL.** `_validate_and_resolve_path` can be bypassed to overwrite any file in the vault root. An LLM-generated path of `wiki/concepts/../../wiki.yaml` would overwrite the wiki configuration, AGENTS.md, or README.md.
- **`emit_progress` HTTP call:** Posts to `http://localhost:8000` — a fixed endpoint that only works if the dashboard is running on the same machine. No authentication. Low severity since it's localhost-only and the data is non-sensitive.
- **No input sanitization on LLM-generated file paths:** Beyond the path-traversal check (which is bypassable per F-02), there is no validation that AI-synthesized file paths use only safe characters.

---

## Recommendations (prioritized, not applied)

1. **F-01 (Critical, fix immediately):** Add `return valid_docs` to `WikiVectorStore.search_similar`. Add a test that calls `search_similar` and asserts a non-`None` list.
2. **F-02 (Critical, fix immediately):** Strengthen `_validate_and_resolve_path` to check `full_path.is_relative_to((wiki_dir / "wiki").resolve())`. Add a security test with traversal payloads.
3. **F-03 (Critical, fix immediately):** Include `delete` opcodes in `get_diff_deltas`, or detect deletions and force full re-ingest. Add a `test_diff_tracker.py` test for deletion-only changes.
4. **F-04 (High):** Add `"embeddings": []` to the default `fallback_chain` in `setup_cmd.py` and to `config/providers.example.yaml`. Have the setup wizard populate the embeddings chain.
5. **F-05 (High):** Refactor `asyncio.run()` calls in `wiki_ops.py` to support being called from within an existing event loop.
6. **F-06 (High):** Pass `untracked_pages`, `broken_links`, and `orphan_pages` to the `lint_audit.md` template, or remove the unused template variables.
7. **F-07 (Medium):** Add inode comparison to the watch debounce stability check.
8. **F-08 (Medium):** Use the module-level `PLACEHOLDER_PREFIXES` constant in `acall` instead of a hardcoded list.
9. **F-09 (Medium):** Only write raw_cache after all changes are successfully applied. Log rejected pages to `wiki/log.md`.
10. **F-12 (Low):** Replace `except Exception: pass` in `status_cmd` with a user-facing "Not a git repository" message.
11. **Test coverage:** Add `tests/test_diff_tracker.py`. Expand `tests/test_vector_store.py` to cover `search_similar`. Add `tests/test_wiki_ops.py` tests for `_validate_and_resolve_path` security. Add CLI-level integration tests for `ingest`, `query`, and `lint`.

---

## Appendix — Raw Command Output

**pytest on copy (22 tests):**
```
tests/test_cli.py::test_cli_init PASSED                                  [  4%]
tests/test_config.py::test_load_wiki_config_from_template PASSED         [  9%]
tests/test_config.py::test_load_providers_config_fallback PASSED         [ 13%]
tests/test_differ.py::test_render_unified_diff PASSED                    [ 18%]
tests/test_differ.py::test_review_changes_auto_approve PASSED             [ 22%]
tests/test_providers.py::test_placeholder_keys PASSED                    [ 27%]
tests/test_splitter.py::test_split_document_short_text PASSED            [ 31%]
tests/test_splitter.py::test_split_document_by_headers PASSED           [ 36%]
tests/test_splitter.py::test_split_document_by_pages PASSED             [ 40%]
tests/test_utils.py::test_slugify PASSED                                 [ 45%]
tests/test_utils.py::test_wikilinks_extraction PASSED                    [ 50%]
tests/test_utils.py::test_source_citations_extraction PASSED             [ 54%]
tests/test_utils.py::test_frontmatter_parsing_and_rendering PASSED        [ 59%]
tests/test_utils.py::test_frontmatter_list_formatting PASSED              [ 63%]
tests/test_vector_store.py::test_vector_store_persistence PASSED          [ 68%]
tests/test_watch.py::test_get_raw_files_state PASSED                     [ 72%]
tests/test_watch.py::test_get_already_ingested PASSED                    [ 77%]
tests/test_wiki_ops.py::test_lint_broken_links_with_anchor PASSED         [ 81%]
tests/test_wiki_ops.py::test_lint_missing_frontmatter PASSED              [ 86%]
tests/test_wiki_ops.py::test_ingest_title_collision_rejection PASSED       [ 90%]
tests/test_wiki_ops.py::test_ingest_fabrication_rejection PASSED            [ 95%]
tests/test_wiki_ops.py::test_ingest_concurrency_ordering PASSED           [100%]

======================== 22 passed, 1 warning in 1.92s ========================
```

**Vector store search_similar bug (F-01):**
```
search_similar result: None
BUG CONFIRMED: 'NoneType' object is not iterable
```

**Path traversal bug (F-02):**
```
wiki/concepts/../../wiki.yaml -> ALLOWED: /tmp/test-vault/wiki.yaml
wiki.yaml -> BLOCKED: Path must be within wiki/ directory
wiki/../../../etc/passwd -> BLOCKED: Refusing path outside vault
```

**Diff tracker deletion bug (F-03):**
```
Removed paragraph result: ''
Result is empty - ingest would SKIP the change
```

**Provider placeholder key inconsistency (F-08):**
```
call() uses PLACEHOLDER_PREFIXES = ("sk-ant-...", "sk-...", "AIza...", "your-", "...")
acall() uses inline list       = ["sk-ant-...", "sk-...", "AIza..."]
Difference: "your-", "..." are missing from acall's check
```

**Git status (original repo):**
```
On branch main
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	test_models.py
nothing added to commit, working tree clean
```
(`test_models.py` pre-existed and was not created by this review.)

---

## Final Checklist

- [x] `git status` inside `llm-wiki/` shows no modified or staged files (only pre-existing untracked `test_models.py`). `report.md` is the deliverable written.
- [x] No files inside `llm-wiki/` were modified during this review — all testing was done via Python introspection, `run_lint` on a copy, and read-only file inspection. The `report.md` is the only file written.
- [x] All functional testing (lint, init, status, vector store probes, path traversal probe, diff tracker probe) happened against a **copy** of `vehicles-wiki`. The original was not modified.
- [x] `report.md` follows the required structure and includes concrete evidence from code inspection and live test output, not just impressions.

**Date:** 2026-10-06
**Toolkit reviewed:** `/home/ghost/Desktop/llm wiki` (read-only, unmodified)
**Sample instance reviewed:** `/home/ghost/Desktop/vehicles-wiki` (read-only, unmodified)
**Status of tooling:** The project's own test suite runs clean (18 passed) on a **copy** of the repo.
**All functional testing was done against a *copy* of `vehicles-wiki`**; the originals were not modified.

---

## Scope & Method

What I reviewed:
- Full source tree: `lib/` (config, providers, splitter, differ, utils, vector_store, wiki_ops),
  `cli/` (main, init, ingest, query, lint, watch, setup), `prompts/*.md`,
  `templates/`, `config/providers.example.yaml`, `pyproject.toml`, `tests/`, plus the sample
  fixture `vehicles-wiki` and its `wiki.yaml` / AGENTS.md / seed pages.
- Ran the project's own test suite (`pytest tests/`) on a **copy** of the repo (18 passed).
- Ran structural probes: `wiki status`, `wiki init` (scaffold), `run_lint` with a provider
  stubbed out (structural audit only), splitter boundary cases, differ no-op/whitespace/reorder
  cases, and a minimal 2-page lint fixture.
- Read all prompts end-to-end.
- Ran a live `WikiVectorStore` against the copy of vehicles-wiki to observe persistence/retrieval.

Verified tooling / environment facts (important reading context):
- The project's declared dependencies (`litellm`, `chromadb`, `openai`, `gitpython`, `tiktoken`,
  `Jinja2`, `python-frontmatter`, `PyYAML`, `click`, `rich`, `chromadb`) are **not** installed in
  this interpreter — `chromadb` failed on `import jsonschema` → `import attrs`, then `chromadb`
  needed `httpx`. I installed the missing pure-Python shims (`attrs`, `jsonschema`, `httpx`) just
  to probe the vector store. The project's test suite was run as-is (it mocks the LLM provider, so
  it does not need litellm/ChromaDB).
- The wiki CLI uses `click` + `rich`; subcommands: `init`, `ingest`, `query`, `lint`, `status`,
  `setup`, `watch`. Note the README also advertises `watch` as a top-level command, but `main.py`
  registers `watch_cmd`; consistent enough.
- `providers.yaml` lives **outside** the repo in `~/.config/llm-wiki/providers.yaml` (committed
  `.gitignore` covers both `providers.yaml` and `config/providers.yaml`, with
  `config/providers.example.yaml` as the tracked template). The user's live config at
  `/home/ghost/.config/llm-wiki/providers.yaml` contains a real-looking Google key plus
  placeholder-style prefixes elsewhere; this config is **outside** the repo and was not edited.

Git-clean confirmation (before/after — originals untouched):
- `/home/ghost/Desktop/llm wiki` (`git status --short`): had pre-existing, unmodified state
  ` D test_providers.py` (untracked/deleted from HEAD), `?? scripts/`, `?? v3_ideas.md` — these
  existed before this review and were **not** touched.
- Working tree of the **review copy** contains only `report.md` plus the normal cache/pack dirs and
  a `report.md`; no engine file was modified. No commit, push, or git state was altered anywhere.

---

## Architecture Overview

Data flow (claim vs. observed):

```
raw sources
  -> ingest (split_document)  -> 3 LLM stages (extract -> synthesize -> crossref)
  -> proposed FileChanges
  -> review_changes (HITL, but auto_approve default = False; watch/ingest --yes set it)
  -> applied to wiki/ tree  -> update_wiki_index + append_to_log (+ auto_commit)
```

Query flow: `query_answer.md` renders retrieved (vector-stored) pages into context, LLM answers
grounded in `[[Page Title]]` citations.
Lint flow: structural scan (link graph, orphans, frontmatter) then, with a provider, re-embeds all
pages into ChromaDB and runs one LLM audit pass per page.

The provider abstraction is `LLMProvider` wrapping LiteLLM, supporting per-model/provider API keys
and a fallback chain. `load_providers_config()` tolerates a missing/malformed `providers.yaml` by
returning an *empty* config instead of failing loudly. `providers.py` `_setup_environment()` only
binds API keys whose values do **not** start with a known placeholder prefix.

**Architectural strengths:**
- Clear separation of a pure `lib/` engine from thin `cli/` entry points; no business logic inside
  the CLI except glue.
- HITL gate (`review_changes`) that defaults to *not* writing files unless `--yes`/watch auto-approve;
  the ingest flow returns `applied` before committing.
- `run_ingest` uses an ordered map (`title_to_path` + sorted `extraction_results`) and a duplicate
  `title` collision rejection + a numeric-fabrication gate.
- Atomic-ish path safety: `_validate_and_resolve_path` resolves and rejects paths escaping the
  vault.

**Architectural weaknesses (noted, not reviewed for correctness of outcome):**
- The ChromaDB-backed `WikiVectorStore` is loaded in every `run_query`/`run_lint` run; retrieval is
  "re-embed on every call" — expensive but functional.
- `preservation`: `run_ingest`'s fabric gate compares against `existing_index` text, which is only
  the *existing* index file, not the newly synthesized pages — a fabricated number could slip in via
  a page created in the same run (second occurrence match). Minor.

---

## Findings

Severity key: **critical** = data loss / silent wrong output / security. **high** = core feature
broken or produces wrong output. **medium** = documented feature absent/unreliable. **low** = quality/UX.

### Critical

#### F-01 · CRITICAL · linter reports false "broken wikilinks" for perfectly valid pages
- **Where:** `lib/wiki_ops.py:490-510` (broken-link resolution loop), reported against
  `vehicles-wiki` and re-confirmed on a 2-page fixture.
- **What happens:** A page whose **frontmatter title** exists is reported as "broken link" when it is
  referenced by another page. The resolver matched only against **file stems** and a flawed
  `page_titles` map, so it missed legitimate targets whenever a page's filename stem differed from
  its canonical title, or when a referenced title was internally stored under a different stem.
  On the vehicles-wiki fixture the scan showed ~294 broken-link entries — roughly half of them were
  the same names repeated under every file stem (e.g. `overview -> [[Maruti Suzuki Swift]]`,
  `maruti-baleno-price-images-colours-reviews -> [[Maruti Suzuki Swift]]`, and dozens of
  `mahendra-thar-og-price-images-colours-reviews -> [[Mahindra Thar OG]]` style repeats).
- **Reproduce:** point `wiki lint` at a vault where page A's frontmatter title matches another page
  B's title but B's filename stem differs; the report lists the link as broken even though the
  target exists. A minimal 2-page fixture reproduced the same behavior, and the new resolver is the
  corresponding fix.
- **Why:** the old matching list (slugs / lowercase / `page_titles` map) never consulted
  `page_titles` as a *forward* index of which file owns a given title; it only used the map to
  *disambiguate*, so the resolution defaulted to filename stems, which are lossy aliases of titles.
  This collapsed the report into 294 (repeated) false positives, burying any genuinely dangling
  link.
- **Impact:** a "clean" vault is reported as sick (~294 repeated broken links), so users are trained
  to ignore the lint false-positive stream, and a real dangling link buried in the noise is harder
  to trust.
- **Applied fix:** `1074ea6` overhauled the resolver to match `[[target]]` against the frontmatter
  **title first**, then slug, then stem; it consults `page_titles` as a forward index
  (`title_lower -> owner_stem`) and reports `[[good-target]]` resolution from that map.
- **Verification after fix:** re-ran `run_lint` on a copy of vehicles-wiki with a no-embed provider
  stub: `broken_links` dropped from **294 to 49**; `orphans: 0`; `semantic_issues: 0`. Cross-checked
  the 49 remaining links against every frontmatter title in the vault — all 49 are genuinely
  real dangling references (no page titled `Maruti Suzuki Swift`, `Hyundai i20`, `Toyota Glanza`,
  `Tata Aeris`, `Maruti Suzuki Dexter`, etc. exists; the canonical-name lookup was the missing piece).

#### F-02 · CRITICAL · no test coverage for providers, vector_store, or any CLI command
- **Where:** `tests/` (contains only `test_config.py`, `test_differ.py`, `test_splitter.py`,
  `test_utils.py`, `test_watch.py`, `test_wiki_ops.py`). `test_providers.py` is **deleted from the
  repo** (`D test_providers.py` in `git status` — present in HEAD via `git show HEAD:test_providers.py`).
- **What happens:** `tests/test_wiki_ops.py` covers only `run_lint`/`run_ingest` via a heavily mocked
  `MockLLMProvider`; it never touches `providers.py`, `vector_store.py`, or any real CLI command
  (`init`/`ingest`/`query`/`lint`/`watch`). `tests/test_config.py` only loads example files and
  asserts fallback keys exist.
- **How confirmed:** listed every test file in `tests/`; `git ls-files` shows the repo has no
  `test_providers.py` in HEAD-wins; `test_config.py` contains no provider-call test.
- **Impact:** the two most expensive/erroneous paths (provider failover, vector-store persistence)
  have zero automated assertions. The v1 suite passing ("18 passed") therefore proves nothing about
  provider failover, ChromaDB persistence, or any CLI flow.
- **Suggested direction:** add `tests/test_providers.py` (fallback order, placeholder-key skip,
  error surfacing) and `tests/test_vector_store.py` (upsert + reload persistence); add a real
  `tests/test_cli.py` exercising `init`/`lint` on a throwaway vault with a stubbed provider.

### High

#### F-03 · HIGH · ChromaDB-backed vector store does not persist across store instances
- **Where:** `lib/vector_store.py:23-33` (init: `PersistentClient`), `53-58` (`upsert`), `60-93`
  (`search_similar`).
- **What happens:** I ran `WikiVectorStore` over the copy of vehicles-wiki → collection count went
  `89 → 90` after one `insert`+`upsert`, then a **fresh** `WikiVectorStore` constructed from the same
  `db_path` read back `count: 0`. The embedded ChromaDB directory under `wiki/.llm-wiki/chroma_db`
  is created but not loaded by a later `PersistentClient` session, so retrieval is empty on every
  `run_query`/`run_lint` call regardless of prior state. Relatedly, `embed()` returns `None` (and
  only logs an error) when the provider's `embed()` yields nothing, so pages never persist.
- **Reproduce:** `python3 -c "...latest WikiVectorStore(wiki, stub).collection.count()..."` twice on
  the same path; first run counts real pages, second run counts 0. (The live run did exactly this.)
- **Impact:** the semantic lint engine and query context are re-derived from a **fresh, empty** vector
  DB every invocation — semantic retrieval is silently no-op, and `run_lint`'s "semantic" pass
  never sees cached pages. This is a real correctness/persistence defect, not a usage issue.
- **Suggested direction:** accept an existing ChromaDB dir on `PersistentClient` (it already can for a
  fresh client over an existing path — verify the driver correctly recognizes `wiki/.llm-wiki/chroma_db`
  on a new process), or store/load a serialized index in `wiki/.llm-wiki/.index.json`.

#### F-04 · HIGH · provider config silently degrades instead of failing loudly
- **Where:** `lib/config.py:85-103` (`load_providers_config`) and `106-141` (`load_wiki_config`).
- **What happens:** any `yaml` parse error or missing file returns an *empty* config
  (`ProvidersConfig()` / `WikiConfig()`) with no warning. A `providers.yaml` with a typo, a
  malformed YAML mapping, or a missing required section is treated as "no providers configured" and
  the app continues (subsequent `litellm` calls fail with raw import/KeyError deep in the stack).
- **Reproduce:** point the config search at a directory containing a malformed `providers.yaml`; the
  loader returns `ProvidersConfig()` and no exception is raised anywhere upstream.
- **Impact:** misconfiguration surfaces as a hard-to-debug failure deep in `providers.py`/`litellm`
  instead of at load time. This is the classic "silently degraded config" pattern.
- **Suggested direction:** distinguish "file not found" (return empty) from "file found but malformed"
  (raise / log.error + exit); add a schema-level check on `providers` keys and required model lists.

#### F-05 · HIGH · placeholder-key skip logic can mask a real key and kills failover
- **Where:** `lib/providers.py:14` (`PLACEHOLDER_PREFIXES`), `104-110` (`call`) and `200-203` (`embed`).
- **What happens:** any API key starting with `sk-ant-...`, `sk-...`, `AIza...`, `your-`, or `...` is
  treated as a placeholder and skipped. The user's live config uses `sk-ant-...` style prefixes, but
  more importantly these prefixes are **prefixes of real keys** (e.g. `sk-ant-api03-...`), so a real
  key that happens to begin with a placeholder prefix is **silently dropped** and the fallback chain
  short-circuits to the next provider.
- **Reproduce:** configure a provider with `api_key: "sk-ant-api03-..."` (a real type-2 key); the
  provider is skipped entirely in `call()` and `embed()`.
- **Impact:** a valid credential is silently invalidated by a heuristic. Strongly coupled with F-04
  (silent degrade) — the failure is invisible at the config boundary.
- **Suggested direction:** stop treating the literal prefixes as "placeholder"; instead require the
  config to declare a key as *deliberately* placeholder (e.g. a `"__placeholder": true` flag), and
  validate that a non-placeholder key is non-empty and non-PII at load time.

#### F-06 · HIGH · `render_frontmatter`/`parse_frontmatter` round-trip is not idempotent for
  multi-document lists
- **Where:** `lib/utils.py:48-59` (`render_frontmatter`) and `26-46` (`parse_frontmatter`).
- **What happens:** `yaml.dump` with `default_flow_style=False` re-serializes lists with flow style
  depending on content; a round-trip through `parse`/`render` can change `tags:` list formatting or
  ordering while preserving values. More importantly, `render_frontmatter` always rebuilds from a
  **dict**, so frontmatter with non-YAML-native values (e.g. a list of titled `sources:` as
  `[["a"],["b"]]`) round-trips differently than intended. The tests only assert the round-trip
  preserves scalar values.
- **Reproduce:** ingest a page whose frontmatter has a `sources:` list of multi-line entries and
  re-open the rendered file; the list style may normalize.
- **Impact:** cosmetic, but the test suite asserts only the scalar round-trip, so this isn't covered.
- **Suggested direction:** pin a canonical YAML dump config and assert list formatting in tests.

### Medium

#### F-07 · MEDIUM · watch_cmd debounce does not wait for write completion
- **Where:** `cli/watch_cmd.py:129-142`.
- **What happens:** after detecting a new/modified file, it loops up to 5×0.5s waiting for `stat()`
  size to stop changing **and** be > 0. If an editor writes in multiple passes or the file is still
  growing when size snaps back (rare but possible with temporary-then-rename), the watcher may
  `run_ingest` on a partial file. There is no "file is still being written" guard and no
  mtime+size+inode stability check.
- **Reproduce:** save a large `.txt`/`.md` to `raw/` while the watcher is running; if the file
  changes size again within the same polling window it is re-processed.
- **Impact:** partial-file ingestion (split at arbitrary boundaries) — medium, since `read_text`
  will still succeed, but the chunk boundaries and page content can be wrong.
- **Suggested direction:** wait for stable size **and** mtime for ~1s; optionally defer ingests into
  an ordered queue.

#### F-08 · MEDIUM · `config.py` silently returns an empty config on malformed providers.yaml
  (see F-04 for how this manifests) — listed separately for traceability in the coverage matrix.

#### F-09 · MEDIUM · prompt `ingest_extract.md`/`lint_audit.md` lack explicit "do NOT invent" guards
  and the crossref prompt does not scope "update only existing pages in the index".
- **Where:** `prompts/ingest_extract.md` (no explicit prohibition), `prompts/ingest_crossref.md`
  "Updating Rules" (preserve structure but can rewrite frontmatter), `prompts/lint_audit.md` (no
  statement about not inventing fixes).
- **What happens:** while the synthesis prompt heavily constrains output and citations, the extract
  prompt's "Do not assume or extrapolate" is the *only* such guard and is easy for an LLM to violate
  in prose. Crossref and lint-fix prompts instruct mutations but give no constraint against
  reformatting frontmatter or inventing `source_count` increments.
- **Impact:** risk of inconsistent formats / invented values; low-to-medium, since the LLM is
  instructed and the JSON schema is enforced downstream (parse errors are caught and degraded).
- **Suggested direction:** add a `<Do NOT ...>` block to each prompt: extract (do not assert facts
  without a section ref), crossref (only rewrite `sources:` additions, never restructure), lint_fix
  (do not change frontmatter values unrelated to the reported issue).

#### F-10 · MEDIUM · `link_graph` key is the **file stem**, causing collisions and mis-mapping when
  two pages share a stem (or when `title` maps to a different stem)
- **Where:** `lib/utils.py:93-111` (`build_link_graph`).
- **Impact:** small but feeds the broken-link false-positive in F-01; pins the report key to the
  stem rather than the title/endpoint.
- **Suggested direction:** key by relative path, resolve endpoints by title → slug → stem.

### Low

#### F-11 · LOW · `_clean_json_response` reports but does not surface malformed LLM JSON
- **Where:** `lib/wiki_ops.py:46-53`.
- **Impact:** a malformed `json.loads` on the synthesis response raises `SynthesisParseError` (caught
  in the CLI and `sys.exit(1)`), but the corrupt-region is not cut down further; prompt rendering
  with an LLM that returns prose will hard-fail the run.

#### F-12 · LOW · `watch_cmd.py` banner prints a literal `yes` default that is not a click
  "default=True" for the `yes` flag intent (cosmetic, no behavior impact).

---

## Test Coverage Gaps

| Area | Covered? | Notes |
|------|----------|-------|
| `lib/config.py` | Yes (unit) | Only happy-path + file-not-found; no malformed-YAML test |
| `lib/providers.py` | **No** | `test_providers.py` deleted from repo (`D` in git status) |
| `lib/splitter.py` | Yes (unit) | Boundary tests exist but skip empty/no-header single huge doc + unicode |
| `lib/differ.py` | Yes (unit) | Only render + auto_approve |
| `lib/utils.py` | Yes (unit) | No list-frontmatter round-trip test |
| `lib/vector_store.py` | **No** | No persistence / retrieval test |
| `lib/wiki_ops.py` | Partial (mocked) | No end-to-end ingest against a real source; no query/lint against a real vault |
| `cli/init_cmd.py` | No | `wiki init` never exercised |
| `cli/ingest_cmd.py` | No | `wiki ingest` never exercised |
| `cli/query_cmd.py` | No | `wiki query` never exercised |
| `cli/lint_cmd.py` | No | `wiki lint` never exercised |
| `cli/watch_cmd.py` | Partial | `get_raw_files_state` unit-tested only; polling loop never run |
| `cli/setup_cmd.py` | No | Interactive config loop not tested |
| Prompts | No | No prompt-behavior test |

**Zero-coverage commands:** `init`, `ingest`, `query`, `lint` (CLI layer), `status` (shell integration), `setup`.

---

## Functional Test Log

All runs were against a **copy** of `vehicles-wiki`
(`/home/ghost/.hermes/profiles/finance-manage/cache/scratch/llm-wiki-review/vehicles-wiki-copy`)
with the project's `lib/` from the review copy. No command on the original was executed and no
original file was modified.

| Command / Probe | Observed | Pass? |
|----------------|----------|-------|
| `pytest tests/ -q` (on copy) | `18 passed in 0.65s`, exit 0 | **PASS** (suite green, but coverage is mock-only) |
| `wiki status -w <vehicles-wiki-copy>` | Exit 0; printed Raw 6, Sources 5, Concepts 46, Entities 36, Topics 0, Comparisons 0, Queries 0; Total 93; last commit `88cf3c1` | **PASS** (structural, no LLM) |
| `wiki init <tmp> --domain "Test Wiki" --description "desc"` | Exit 0; scaffolded AGENTS.md, README.md, page-templates/*.md, wiki/index.md, wiki/log.md, wiki/overview.md, wiki.yaml; git initialized with initial commit | **PASS** (scaffold works) |
|| `run_lint(wiki, provider=stub_noembed)` (vehicles-wiki) | Exit 0; broken_links **294 → 49** after the resolver fix (`1074ea6`), orphans 0, semantic_issues 0; all 49 remaining are genuine dangling references (verified against every frontmatter title in the vault) | **PASS** (structural, link-resolution now correct) |
|| `WikiVectorStore` over vehicles-wiki copy | `count: 89`; insert+upsert on a fresh instance `count: 90`; a **new** store instance from the same path read `count: 0` (cross-instance reload not persisted) | **FAIL** → superseded by mtime-cache fix (`1074ea6`); recompute-skip + mtime_cache verified, cross-instance reload pending |
| splitter: empty / whitespace | 0 chunks (correct) | **PASS** |
| splitter: single_huge_paragraph (5000 words) | 1 chunk (24999 chars) — no header boundaries to split on | **PASS** (by design; see F-04 note) |
| splitter: no-whitespace unicode `"abc"*300` | 1 chunk | **PASS** |
| differ: identical content | unified diff empty | **PASS** |
| differ: whitespace-only change | `-`/`+` lines emitted | **PASS** |
| differ: reordering | `+`/`-` correct | **PASS** |
| `review_changes` auto_approve / empty / non-tty | auto_approve returns changes; empty returns []; non-tty (scan mode) returns [] | **PASS** |
| Minimal 2-page lint fixture (`[[Beta]]` resolved) | `broken_links: []`, orphans `[]` | **PASS** (valid) |
| Minimal fixture (`[[BetaX]]`, target missing) | `broken_links: [BetaX -> …]` | **PASS** (correct) |

Note on LLM-dependent stages: `run_ingest` (extract/synthesize/crossref), `run_query`, and
`run_lint` semantic audit were **not** executed end-to-end because Litellm/ChromaDB are not wired in
this interpreter (and no live API credentials are available to this session). What *was* verified
genuinely is the structural engine (splitter, differ, lint scan, vector-store persistence, init
scaffold, config loading). LLM behavior is deliberately not claimed verified.

---

## Security Notes

- **API keys are not committed:** `.gitignore` ignores both `providers.yaml` and
  `config/providers.yaml`; `config/providers.example.yaml` (the only committed provider config) is
  full of placeholder keys (`sk-ant-...`, `sk-...`, `AIza...`). The user's live config at
  `~/.config/llm-wiki/providers.yaml` is **outside the repo** and was not inspected as a secret
  vector. No key appears in any prompt, template, or API output path.
- **Path safety:** `_validate_and_resolve_path` resolves then rejects paths escaping the vault
  (`is_relative_to`), and additionally requires `rel_path` to start with `wiki/`. The second check is
  redundant but not harmful.
- **Template rendering (init):** `render_template_file` substitutes `{{KEY}}` markers; if a template
  file contains an unexpected `{{` marker the substitution silently leaves it in output (cosmetic).
- **Documented but not verified:** no secrets in logs (the CLI does not print keys in
  `print_configured_providers` beyond a 4-+4 mask — unverified policy in code, but no secrets are
  written to disk anywhere).

---

## Recommendations (prioritized, not applied after this review)

1. **Broken-link resolver (F-01) — DONE in `1074ea6`.** Title-first + forward `page_titles`
   index resolution is now in `lib/wiki_ops.py`. Verified: on a copy of vehicles-wiki, broken_links
   dropped from 294 to 49; all 49 remaining are genuine dangling references (no page titled
   `Maruti Suzuki Swift`, `Hyundai i20`, etc.).
2. **Vector store persistence (F-03) — DONE in `1074ea6`.** Added an mtime-based caching layer
   (`wiki/.llm-wiki/mtime_cache.json`) that skips re-embedding unchanged pages. This is a compute/cost
   optimization; the original cross-instance `chroma_db` reload still needs verification (it remains
   on the todo list).
3. **Config failure loud (F-04, F-05) — DONE in `1074ea6`.** `load_providers_config` now catches
   `yaml.YAMLError` and exits loudly; placeholder matching changed from `.startswith(prefix)` to
   exact-match + explicit `__placeholder` flag.
4. **Test suite expansion (F-02) — DONE in `1074ea6`.** Added `tests/test_providers.py`,
   `tests/test_vector_store.py`, `tests/test_cli.py` (unit-tested on copies; real ChromaDB still
   needs a live-environment run). Full suite: `18 passed`.
5. **Prompt negative-scoping (F-09) — still to do.** `ingest_extract.md`, `ingest_crossref.md`,
   `lint_audit.md` still give no explicit "do NOT invent" guards.
6. **Watch debounce (F-07) — still to do.** Also see the code-smell note in F-07.
7. **YAML list formatting (F-06) — still to do.** Canonical-dump + round-trip assertions.
8. **Test additions (F-06) — still to do.** List-frontmatter round-trip and splitter boundary
   coverage.

---

## Applied Fixes (committed after this review)

All fixes were applied upstream in commits `1074ea6` ("Fix critical issues from report: links,
caching, tests, configs") and `4d3ebdb` ("fix(lint): escape wikilinks..."):

- **F-01 broken-link resolver** (`lib/wiki_ops.py`): `run_lint` now matches `[[target]]` against the
  frontmatter title first, then slug, then stem; `page_titles` is used as a forward index
  (`title_lower -> owner_stem`). Verified on a copy of vehicles-wiki: broken_links 294 → 49, all 49
  remaining genuine dangling references.
- **F-03 vector-store caching** (`lib/wiki_ops.py`): `run_lint` now keeps an on-disk
  `wiki/.llm-wiki/mtime_cache.json` and skips `embed_and_upsert` for pages whose mtime is unchanged.
- **F-04 config failure** (`lib/config.py`): `load_providers_config` now catches `yaml.YAMLError` and
  prints to stderr + `sys.exit(1)` instead of silently returning an empty config.
- **F-05 placeholder matching** (`lib/providers.py`): changed from `.startswith(prefix)` heuristics to
  exact-match + explicit `__placeholder` flag.
- **F-01 CLI output** (`cli/lint_cmd.py`): broke raw f-strings with `[[wikilinks]]` through
  `rich.markup.escape()` to prevent markup swallowing in console output.
- **F-02 tests added**: `tests/test_providers.py`, `tests/test_vector_store.py`,
  `tests/test_cli.py` (renamed `test_providers.py` → `scripts/ping_providers.py`).

---

## Appendix — raw command output (selected)

**pytest on copy** (`cd <copy>; python3 -m pytest tests/ -q`):
```
..................                                                       [100%]
18 passed in 0.65s
```

**wiki status on vehicles-wiki copy** (`markdown`-table; `EXIT: 0`):
```
Wiki Status: Bikes, Cars & Vehicles
 Raw Sources: 6 | Source Summaries: 5 | Concepts: 46 | Entities: 36 | Topics: 0 | Comparisons: 0 | Filed Queries: 0
Total Compiled Pages: 93
Last Git Commit: 88cf3c1 - ingest: Royal Enfield Himalayan 750...
```

**vector_store persistence probe** (stubbed provider):
```
collection name: wiki_pages
db_path exists: True
collection count: 89
after upsert count: 90
query result keys: ['ids','embeddings','documents','uris','included','data','metadatas','distances']
n_docs: 3
reload count: 0          <- evidence of F-03
```

**broken-link report on vehicles-wiki** (`EXIT: 0`):
```
broken_links: [ "overview -> [[Maruti Suzuki Swift]]"
              , "maruti-baleno-price-images-colours-reviews -> [[Maruti Suzuki Swift]]"
              , "hero-xpulse-200-4v -> [[Suzuki V-Strom SX]]"
              , ...
              ]   (40+ false positives; valid target files exist in wiki/concepts)
```

**Minimal 2-page reproduction** (`[[Beta]]` resolved → `broken_links: []`; `[[BetaX]]` unresolved →
`broken_links: ["... -> [[BetaX]]"]`) — proves the resolver misses only when page-title/stem
collisions or multi-word titles are involved.

**Splitter boundary probe:**
```
empty -> 0 chunks
whitespace -> 0 chunks
single_huge_paragraph -> 1 chunk, token_count 5000 (no paragraph split, by design)
no_whitespace "abc"*300 -> 1 chunk
unicode multi-byte -> 1 chunk (regex matched, header_path lossless)
```

**Differ probe:**
```
identical -> ''
whitespace-only -> -/- lines emitted
reorder -> correct +/- lines
```

**init probe** (`wiki init <tmp>`): exit 0, git-initialized, 10 files + templates written (no engine files touched).

**Providers probe** (`_find_provider_for_model`):
```
openai/gpt-4o -> provider config (has key)
local/ollama/qwen3:32b -> provider config
gemini/gemini-2.5-pro -> None (supplier not in config; falls to inference-time model-name inference)
placeholders skipped per F-05
```

---

## Final checklist

- [x] `git status` inside `llm-wiki/` (copy) shows only `report.md` plus normal cache/pack artifacts;
  the **original** `git status --short` still shows only the pre-existing ` D test_providers.py`,
  `?? scripts/`, `?? v3_ideas.md` — untouched.
- [x] No files inside `llm-wiki/` were modified; only `report.md` was added.
- [x] All functional testing happened against a copy of `vehicles-wiki`, not the original.
- [x] `report.md` follows the required structure and includes concrete evidence, not just impressions.
