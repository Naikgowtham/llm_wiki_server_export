import re

with open('lib/providers.py', 'r') as f:
    content = f.read()

# Fix Cloudflare prefix logic
cf_search = r'(elif p_data and "nous" in p_data\.get\("api_base", ""\):\n\s+clean_model = f"openai/\{model_id\}" if not model_id\.startswith\("openai/"\) else model_id)'
cf_replace = r'\1\n                elif p_data and "cloudflare" in p_data.get("api_base", ""):\n                    clean_model = f"cloudflare/{model_id}" if not model_id.startswith("cloudflare/") else model_id'
content = re.sub(cf_search, cf_replace, content)

# Fix num_ctx logic
num_ctx_search = r'(if "ollama" in clean_model or "qwen" in clean_model:\n\s+kwargs\["num_ctx"\] = 8192)'
num_ctx_replace = r'if "local/" in model_id or "localhost" in str(p_data.get("api_base", "")):\n                    kwargs["num_ctx"] = 8192'
content = re.sub(num_ctx_search, num_ctx_replace, content)

with open('lib/providers.py', 'w') as f:
    f.write(content)

