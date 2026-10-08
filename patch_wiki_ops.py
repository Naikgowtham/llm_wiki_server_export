import re

with open('lib/wiki_ops.py', 'r') as f:
    content = f.read()

# Make the specific replacements based on the prompt variable names to ensure we hit the right ones.
content = re.sub(r'resp = await provider.acall\(\[\{"role": "user", "content": prompt_str\}\], operation="ingest"\)',
                 r'resp = await provider.acall([{"role": "user", "content": prompt_str}], operation="ingest_extract")',
                 content)

content = re.sub(r'synth_resp = asyncio.run\(provider.acall\(\[\{"role": "user", "content": synth_prompt\}\], operation="ingest"\)\)',
                 r'synth_resp = asyncio.run(provider.acall([{"role": "user", "content": synth_prompt}], operation="ingest_synth"))',
                 content)

content = re.sub(r'cross_resp = asyncio.run\(provider.acall\(\[\{"role": "user", "content": cross_prompt\}\], operation="ingest"\)\)',
                 r'cross_resp = asyncio.run(provider.acall([{"role": "user", "content": cross_prompt}], operation="ingest_crossref"))',
                 content)

content = re.sub(r'answer = asyncio.run\(provider.acall\(\[\{"role": "user", "content": prompt_str\}\], operation="query"\)\)',
                 r'answer = asyncio.run(provider.acall([{"role": "user", "content": prompt_str}], operation="query_answer"))',
                 content)

content = re.sub(r'fb_content = asyncio.run\(provider.acall\(\[\{"role": "user", "content": fb_prompt\}\], operation="query"\)\)',
                 r'fb_content = asyncio.run(provider.acall([{"role": "user", "content": fb_prompt}], operation="query_fileback"))',
                 content)

content = re.sub(r'resp = await provider.acall\(\[\{"role": "user", "content": audit_prompt\}\], operation="lint"\)',
                 r'resp = await provider.acall([{"role": "user", "content": audit_prompt}], operation="lint_audit")',
                 content)

content = re.sub(r'fix_resp = asyncio.run\(provider.acall\(\[\{"role": "user", "content": fix_prompt\}\], operation="lint"\)\)',
                 r'fix_resp = asyncio.run(provider.acall([{"role": "user", "content": fix_prompt}], operation="lint_fix"))',
                 content)

with open('lib/wiki_ops.py', 'w') as f:
    f.write(content)
