import yaml
import litellm

def main():
    with open('/home/ghost/.config/llm-wiki/providers.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    litellm.suppress_debug_info = True
    
    for provider_name, provider_data in config.get('providers', {}).items():
        api_key = provider_data.get('api_key')
        api_base = provider_data.get('api_base')
        models = provider_data.get('models', [])
        
        if not models:
            continue
            
        model = models[0]
        
        # Format model name for litellm
        clean_model = model
        if provider_name == 'anthropic':
            clean_model = f"anthropic/{model}" if not model.startswith("anthropic/") else model
        elif provider_name == 'openai':
            clean_model = f"openai/{model}" if not model.startswith("openai/") else model
        elif 'google' in provider_name:
            clean_model = model.replace('google/', 'gemini/')
        elif provider_name == 'groq':
            # Use a valid Groq model to test the KEY itself, not the fake ones the user put
            clean_model = "groq/llama-3.1-8b-instant"
        elif provider_name == 'cloud flare':
            clean_model = f"cloudflare/{model}" if not model.startswith("cloudflare/") else model
            
        kwargs = {
            "model": clean_model,
            "messages": [{"role": "user", "content": "Say 'OK'"}],
            "max_tokens": 5,
        }
        
        if api_key and not api_key.startswith("sk-ant-...") and not api_key.startswith("sk-..."):
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
            
        print(f"\n--- Testing Key: {provider_name} ---")
        if api_key:
            print(f"Key preview: {api_key[:8]}...")
            
        try:
            if 'ollama' in clean_model or not api_key:
                print("Skipping local/placeholder key.")
                continue
                
            response = litellm.completion(**kwargs)
            print("✅ KEY WORKED")
        except Exception as e:
            err_str = str(e)
            if "AuthenticationError" in err_str or "unauthorized" in err_str.lower() or "invalid api key" in err_str.lower():
                print("❌ KEY FAILED (Authentication Error)")
            elif "model_not_found" in err_str or "NotFoundError" in err_str:
                 print("⚠️ KEY WORKED (But model not found / valid)")
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                 print("⚠️ KEY LIKELY VALID (But service unavailable / overloaded)")
            else:
                 print(f"❌ TEST FAILED: {err_str[:200]}")

if __name__ == "__main__":
    main()
