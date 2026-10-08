import re

with open('lib/providers.py', 'r') as f:
    content = f.read()

search = r'(elif model_id\.startswith\("groq "\):\n\s+clean_model = "groq/" \+ model_id\.split\("/", 1\)\[1\])'
replace = r'\1\n                elif model_id.startswith("nous/"):\n                    clean_model = model_id.replace("nous/", "")'
content = re.sub(search, replace, content)

with open('lib/providers.py', 'w') as f:
    f.write(content)
