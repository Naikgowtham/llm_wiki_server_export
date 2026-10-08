import sys
from lib.providers import LLMProvider
import traceback

def main():
    provider = LLMProvider()
    
    # We will grab all models from fallback_chain['ingest'] to test
    models_to_test = provider.config.get_fallback_models("ingest")
    
    print(f"Models to test: {models_to_test}")
    
    messages = [{"role": "user", "content": "Respond with the word 'SUCCESS' and nothing else."}]
    
    success_count = 0
    
    for model in models_to_test:
        print(f"\n--- Testing model: {model} ---")
        
        # Ensure correct prefixing if needed, but provider.acall/call should handle it
        # Actually we'll test via the direct provider._find_provider_for_model and call litellm directly 
        # to see exactly what works, or just use provider.call overriding the model.
        
        try:
            # We'll use the synchronous call for simplicity, but override the model
            # wait, LLMProvider has no way to force a specific model in `call()` except by fallback chain.
            # Let's write a small custom litellm call mirroring what LLMProvider does.
            import litellm
            litellm.suppress_debug_info = True
            
            p_data = provider._find_provider_for_model(model)
            
            clean_model = model
            if model.startswith("anthropic/"):
                clean_model = model.replace("anthropic/", "")
            elif model.startswith("google/") or model.startswith("google-"):
                # LLMProvider does replacing google/ with gemini/ 
                clean_model = model.replace("google/", "gemini/")
                # wait, google 3 uses 'gemini/gemini-3.8-flash', the model already starts with 'gemini/'.
            elif model.startswith("local/"):
                clean_model = model.replace("local/", "")
            elif p_data and "nous" in p_data.get("api_base", ""):
                clean_model = f"openai/{model}" if not model.startswith("openai/") else model
            elif p_data and "cloudflare" in p_data.get("api_base", ""):
                if not clean_model.startswith("cloudflare/"):
                    clean_model = f"cloudflare/{clean_model}"
                    
            kwargs = {
                "model": clean_model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 10,
            }
            
            if p_data:
                k = p_data.get("api_key")
                b = p_data.get("api_base")
                if k and not k.startswith("sk-ant-") and not k.startswith("sk-..."):
                    kwargs["api_key"] = k
                if b:
                    kwargs["api_base"] = b
                    
            print(f"Calling litellm with model={kwargs['model']}")
            response = litellm.completion(**kwargs)
            content = response.choices[0].message.content.strip()
            print(f"Response: {content}")
            if "SUCCESS" in content.upper():
                print("✅ TEST PASSED")
                success_count += 1
            else:
                print("❌ UNEXPECTED RESPONSE")
                
        except Exception as e:
            print(f"❌ TEST FAILED: {str(e)}")
            # traceback.print_exc()

if __name__ == "__main__":
    main()
