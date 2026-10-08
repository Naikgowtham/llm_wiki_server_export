import re

with open('lib/providers.py', 'r') as f:
    content = f.read()

# The block to search for is:
#                 elif model_id.startswith("local/"):
#                     clean_model = model_id.replace("local/", "")
#                 elif p_data and "nous" in p_data.get("api_base", ""):

search_str = r'(elif model_id\.startswith\("local/"\):\n\s+clean_model = model_id\.replace\("local/", ""\))'
replace_str = r'\1\n                elif model_id.startswith("groq "):' + '\n                    clean_model = "groq/" + model_id.split("/", 1)[1]'

new_content = re.sub(search_str, replace_str, content)

with open('lib/providers.py', 'w') as f:
    f.write(new_content)
