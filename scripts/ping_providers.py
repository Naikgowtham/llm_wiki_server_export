import litellm
import time
from lib.config import load_providers_config
from pathlib import Path

config = load_providers_config()
print("Ping Testing Configured Providers...\n" + "="*40)

PLACEHOLDER_PREFIXES = ("sk-ant-", "sk-...", "AIza", "your-", "...")

for p_name, p_data in config.providers.items():
    api_key = p_data.get("api_key", "")
    api_base = p_data.get("api_base", "")
    models = p_data.get("models", [])
    
    if not models:
        continue
        
    model = models[0] # Test the first model
    
    if api_key and any(api_key.startswith(p) for p in PLACEHOLDER_PREFIXES):
        print(f"[{p_name}] {model}: SKIPPED (Placeholder API key detected)")
        continue
        
    print(f"[{p_name}] {model}: Pinging...", end="", flush=True)
    
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "timeout": 10,
        "max_tokens": 10
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
        
    # some models like local qwen need custom logic, but litellm handles "ollama/..."
    
    t0 = time.time()
    try:
        res = litellm.completion(**kwargs)
        t = time.time() - t0
        print(f" SUCCESS! ({t:.2f}s)")
    except Exception as e:
        print(f" FAILED! ({e})")
        
