import re

with open('cli/setup_cmd.py', 'r') as f:
    content = f.read()

# Replace default config initialization
search_defaults = r'''        "fallback_chain": \{
            "ingest": \[\],
            "query": \[\],
            "lint": \[\],
        \},'''

replace_defaults = '''        "fallback_chain": {
            "ingest_extract": [],
            "ingest_synth": [],
            "ingest_crossref": [],
            "query_answer": [],
            "query_fileback": [],
            "lint_audit": [],
            "lint_fix": [],
            "embeddings": ["local/ollama/nomic-embed-text", "google/gemini/text-embedding-004"],
        },'''
content = re.sub(search_defaults, replace_defaults, content)

# Replace loop operations
search_loop = r'for op in \("ingest", "query", "lint"\):'
replace_loop = r'for op in ("ingest_extract", "ingest_synth", "ingest_crossref", "query_answer", "query_fileback", "lint_audit", "lint_fix", "embeddings"):'
content = re.sub(search_loop, replace_loop, content)

with open('cli/setup_cmd.py', 'w') as f:
    f.write(content)
