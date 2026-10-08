import re

with open('lib/wiki_ops.py', 'r') as f:
    content = f.read()

# Make run_ingest async
content = content.replace("def run_ingest(", "async def run_ingest(")
# In run_ingest, _process_chunks is called via asyncio.run
content = content.replace("chunks_data = asyncio.run(_process_chunks())", "chunks_data = await _process_chunks()")
content = content.replace("synth_resp = asyncio.run(provider.acall", "synth_resp = await provider.acall")
content = content.replace("cross_resp = asyncio.run(provider.acall", "cross_resp = await provider.acall")

# Make run_query async
content = content.replace("def run_query(", "async def run_query(")
content = content.replace("answer = asyncio.run(provider.acall", "answer = await provider.acall")
content = content.replace("fb_content = asyncio.run(provider.acall", "fb_content = await provider.acall")

# Make run_lint async
content = content.replace("def run_lint(", "async def run_lint(")
content = content.replace("report = asyncio.run(_process_lint_pages())", "report = await _process_lint_pages()")
content = content.replace("fix_resp = asyncio.run(provider.acall", "fix_resp = await provider.acall")

with open('lib/wiki_ops.py', 'w') as f:
    f.write(content)
