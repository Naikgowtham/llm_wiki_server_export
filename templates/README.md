# {{DOMAIN_NAME}} — LLM Wiki

> {{DOMAIN_DESCRIPTION}}

This wiki is maintained by an LLM agent following the schema in `AGENTS.md`.

## Structure

```
├── raw/              # Source documents (immutable)
│   └── assets/       # Downloaded images
├── wiki/             # LLM-maintained knowledge pages
│   ├── index.md      # Master table of contents
│   ├── log.md        # Operation log
│   ├── overview.md   # Domain overview
│   ├── sources/      # Source summaries
│   ├── concepts/     # Concept definitions
│   ├── entities/     # Entity profiles
│   ├── topics/       # Topic syntheses
│   ├── comparisons/  # Comparative analyses
│   └── queries/      # Filed-back query answers
├── page-templates/   # Templates for page types
├── AGENTS.md         # Editorial schema (LLM instructions)
└── wiki.yaml         # Instance configuration
```

## Usage

### Adding Sources

1. Drop files (markdown, PDF, HTML) into `raw/`
2. Run: `wiki ingest raw/<filename>`
3. Review proposed changes
4. Approve to commit

### Querying

```bash
wiki query "Your question here"
```

### Health Check

```bash
wiki lint
```

## Key Files

- **[[Wiki Index]]** — Start here. Master catalog of all pages.
- **[[Operation Log]]** — Timeline of all wiki operations.
- **[[Overview]]** — High-level domain summary.
- **AGENTS.md** — Editorial rules the LLM follows.
