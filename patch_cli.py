import re
import os

files_to_patch = {
    'cli/ingest_cmd.py': ('applied = run_ingest(', 'applied = asyncio.run(run_ingest('),
    'cli/query_cmd.py': ('answer, filed_path = run_query(', 'answer, filed_path = asyncio.run(run_query('),
    'cli/lint_cmd.py': ('report = run_lint(', 'report = asyncio.run(run_lint('),
    'cli/watch_cmd.py': ('applied = run_ingest(', 'applied = asyncio.run(run_ingest(')
}

for fp, (search, replace) in files_to_patch.items():
    with open(fp, 'r') as f:
        content = f.read()
    
    if "import asyncio" not in content:
        content = "import asyncio\n" + content
        
    content = content.replace(search, replace)
    
    # Also need to close the parenthesis for asyncio.run!
    # For ingest_cmd:
    if fp == 'cli/ingest_cmd.py':
        # Find the end of run_ingest call
        content = re.sub(r'(applied = asyncio\.run\(run_ingest\([\s\S]*?use_hybrid=hybrid,\n\s*)(\))', r'\1)\2', content)
    elif fp == 'cli/watch_cmd.py':
        content = re.sub(r'(applied = asyncio\.run\(run_ingest\([\s\S]*?use_hybrid=False,\n\s*)(\))', r'\1)\2', content)
    elif fp == 'cli/query_cmd.py':
        content = re.sub(r'(answer, filed_path = asyncio\.run\(run_query\([\s\S]*?use_hybrid=hybrid,\n\s*)(\))', r'\1)\2', content)
    elif fp == 'cli/lint_cmd.py':
        content = re.sub(r'(report = asyncio\.run\(run_lint\([\s\S]*?fix=fix,\n\s*)(\))', r'\1)\2', content)
        
    with open(fp, 'w') as f:
        f.write(content)
