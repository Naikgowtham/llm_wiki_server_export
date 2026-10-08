import re

with open('lib/vector_store.py', 'r') as f:
    content = f.read()

search = r'(if dist < distance_threshold or len\(valid_docs\) < 2:\n\s+valid_docs\.append\(doc\))'
replace = r'\1\n                    \n        return valid_docs'

new_content = re.sub(search, replace, content)

with open('lib/vector_store.py', 'w') as f:
    f.write(new_content)
