"""Multi-provider LLM abstraction with automated fallback chains and rate-limit tracking."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from lib.config import ProvidersConfig, load_providers_config

logger = logging.getLogger(__name__)

PLACEHOLDER_PREFIXES = ("sk-ant-...", "sk-...", "AIza...", "your-", "...")


class LLMProviderError(Exception):
    """Raised when all models in the fallback chain fail."""
    pass


class RateLimitCooldownTracker:
    """Tracks rate-limited models and cooldown expirations globally across all operations and wikis."""
    _cooldowns: Dict[str, float] = {}

    @classmethod
    def is_cooling_down(cls, model_id: str) -> bool:
        expires = cls._cooldowns.get(model_id)
        if not expires:
            return False
        if time.time() >= expires:
            cls._cooldowns.pop(model_id, None)
            return False
        return True

    @classmethod
    def mark_cooling_down(cls, model_id: str, cooldown_seconds: float = 60.0):
        cls._cooldowns[model_id] = time.time() + cooldown_seconds

    @classmethod
    def clear(cls):
        cls._cooldowns.clear()

    @classmethod
    def get_cooldowns(cls) -> Dict[str, float]:
        now = time.time()
        active: Dict[str, float] = {}
        for m, exp in list(cls._cooldowns.items()):
            remaining = exp - now
            if remaining > 0:
                active[m] = round(remaining, 1)
            else:
                cls._cooldowns.pop(m, None)
        return active

    @classmethod
    def filter_available_models(cls, models: List[str]) -> List[str]:
        available = [m for m in models if not cls.is_cooling_down(m)]
        # If all candidate models are cooling down, bypass cooldown so operations don't deadlock
        return available if available else models


class LLMProvider:
    """Manages LLM completions across multiple providers and handles automatic failover."""

    _call_counter: int = 0

    def __init__(self, config: Optional[ProvidersConfig] = None):
        self.config = config or load_providers_config()
        self.rate_limits: List[Dict[str, Any]] = []
        self._setup_environment()

    def _setup_environment(self):
        """Export provider API keys and base URLs into environment variables for litellm."""
        providers = self.config.providers

        # Anthropic
        if "anthropic" in providers:
            key = providers["anthropic"].get("api_key", "")
            if key and key not in PLACEHOLDER_PREFIXES:
                os.environ["ANTHROPIC_API_KEY"] = key

        # OpenAI
        if "openai" in providers:
            key = providers["openai"].get("api_key", "")
            if key and key not in PLACEHOLDER_PREFIXES:
                os.environ["OPENAI_API_KEY"] = key

        # Google Gemini
        if "google" in providers:
            key = providers["google"].get("api_key", "")
            if key and key not in PLACEHOLDER_PREFIXES:
                os.environ["GEMINI_API_KEY"] = key
            if "api_base" in providers["google"] and providers["google"]["api_base"]:
                os.environ["GEMINI_API_BASE"] = providers["google"]["api_base"]

        # Local Ollama / custom base
        if "local" in providers and "api_base" in providers["local"]:
            os.environ["OLLAMA_API_BASE"] = providers["local"]["api_base"]

    def _find_provider_for_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Find the provider configuration dict matching the model ID."""
        providers = self.config.providers

        # Check explicit provider prefix
        for p_name, p_data in providers.items():
            if model_id.startswith(f"{p_name}/"):
                return p_data
            for m in p_data.get("models", []):
                if m == model_id or m in model_id or model_id in m:
                    return p_data

        # Infer by model name
        if "gemini" in model_id:
            return providers.get("google")
        if "claude" in model_id:
            return providers.get("anthropic")
        if "gpt" in model_id:
            return providers.get("openai")
        if "ollama" in model_id or "qwen" in model_id or "llama" in model_id:
            return providers.get("local")

        return None

    def call(
        self,
        messages: List[Dict[str, str]],
        operation: str = "query",
        temperature: float = 0.2,
        override_model: Optional[str] = None,
    ) -> str:
        """Execute a completion using the fallback chain configured for the given operation."""
        base_models = [override_model] if override_model else self.config.get_fallback_models(operation)
        if len(base_models) > 1 and not override_model:
            offset = LLMProvider._call_counter % len(base_models)
            LLMProvider._call_counter += 1
            base_models = base_models[offset:] + base_models[:offset]
        models = RateLimitCooldownTracker.filter_available_models(base_models * 2)  # Circular rotation (max 2 loops)

        try:
            import litellm
            litellm.suppress_debug_info = True
        except ImportError:
            raise LLMProviderError(
                "litellm is not installed. Please run `pip install litellm` to enable LLM completions."
            )

        last_error = None

        for model_id in models:
            # Check if this model belongs to a provider with a dummy placeholder key
            p_data = self._find_provider_for_model(model_id)
            if p_data:
                api_key = p_data.get("api_key", "")
                if p_data.get("__placeholder") or api_key in PLACEHOLDER_PREFIXES:
                    logger.debug("Skipping %s due to placeholder API key.", model_id)
                    continue

            logger.info("Attempting completion with model: %s (operation: %s)", model_id, operation)

            try:
                clean_model = model_id
                if model_id.startswith("anthropic/"):
                    clean_model = model_id.replace("anthropic/", "")
                elif model_id.startswith("google/") or model_id.startswith("google-") or model_id.startswith("google "):
                    remainder = model_id.split("/", 1)[1] if "/" in model_id else model_id
                    clean_model = remainder if remainder.startswith("gemini/") else f"gemini/{remainder}"
                elif model_id.startswith("local/"):
                    clean_model = model_id.replace("local/", "")
                elif model_id.startswith("groq "):
                    clean_model = "groq/" + model_id.split("/", 1)[1]
                elif model_id.startswith("nous/"):
                    clean_model = model_id.replace("nous/", "")
                elif p_data and "nous" in p_data.get("api_base", ""):
                    clean_model = f"openai/{model_id}" if not model_id.startswith("openai/") else model_id
                elif model_id.startswith("cloud flare") or (p_data and "cloudflare" in p_data.get("api_base", "")):
                    raw_cf = model_id.split("/", 1)[1] if ("/" in model_id and not model_id.startswith("@cf/")) else model_id
                    clean_model = f"cloudflare/{raw_cf}" if not raw_cf.startswith("cloudflare/") else raw_cf

                kwargs: Dict[str, Any] = {
                    "model": clean_model,
                    "messages": messages,
                    "temperature": 1.0 if "gemini-3" in clean_model else temperature,
                    "max_tokens": 8192,
                    "timeout": 60 if "cloudflare" in clean_model else 30,  # Rapid failover on dead/throttled endpoints
                }

                if "local/" in model_id or (p_data and "localhost" in str(p_data.get("api_base", ""))):
                    kwargs["num_ctx"] = 8192

                if p_data:
                    k = p_data.get("api_key")
                    b = p_data.get("api_base")
                    if k and k not in PLACEHOLDER_PREFIXES:
                        kwargs["api_key"] = k
                    if b:
                        if "/ai/run" in b:
                            b = b.replace("/ai/run", "/ai/v1")
                        kwargs["api_base"] = b

                for attempt in range(2):  # Only retry once before moving to next fallback
                    try:
                        response = litellm.completion(**kwargs)
                        content = response.choices[0].message.content
                        if content:
                            return content.strip()
                    except Exception as attempt_e:
                        if attempt == 1:
                            raise attempt_e
                        logger.warning("Attempt %d failed: %s. Retrying...", attempt + 1, attempt_e)
                        time.sleep(2 ** attempt)

            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = (
                    "rate_limit" in err_str
                    or "429" in err_str
                    or "resource_exhausted" in err_str
                    or "quota" in err_str
                )

                if is_rate_limit:
                    RateLimitCooldownTracker.mark_cooling_down(model_id, cooldown_seconds=60.0)
                    self.rate_limits.append({
                        "model": model_id,
                        "operation": operation,
                        "error": str(e),
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    logger.warning("Rate limit hit on model %s: %s (cooling down for 60s)", model_id, e)

                logger.warning("Model %s failed: %s. Trying next model...", model_id, e)
                last_error = e
                time.sleep(0.5)

        raise LLMProviderError(
            f"All models in fallback chain for operation '{operation}' failed. Last error: {last_error}"
        )

    def embed(self, text: str) -> Optional[List[float]]:
        """Generate embeddings using the fallback chain configured for 'embeddings'."""
        models = self.config.get_fallback_models("embeddings")
        if not models:
            logger.warning("No embeddings fallback chain configured. Returning None.")
            return None
            
        models = models * 2 # Circular rotation

        try:
            import litellm
            litellm.suppress_debug_info = True
        except ImportError:
            raise LLMProviderError("litellm is not installed.")

        last_error = None

        for model_id in models:
            p_data = self._find_provider_for_model(model_id)
            if p_data:
                api_key = p_data.get("api_key", "")
                if p_data.get("__placeholder") or api_key in PLACEHOLDER_PREFIXES:
                    continue

            logger.info("Attempting embedding with model: %s", model_id)

            try:
                clean_model = model_id
                if model_id.startswith("anthropic/"):
                    clean_model = model_id.replace("anthropic/", "")
                elif model_id.startswith("google/") or model_id.startswith("google-") or model_id.startswith("google "):
                    remainder = model_id.split("/", 1)[1] if "/" in model_id else model_id
                    clean_model = remainder if remainder.startswith("gemini/") else f"gemini/{remainder}"
                elif model_id.startswith("local/"):
                    clean_model = model_id.replace("local/", "")
                elif model_id.startswith("groq "):
                    clean_model = "groq/" + model_id.split("/", 1)[1]
                elif model_id.startswith("nous/"):
                    clean_model = model_id.replace("nous/", "")
                elif p_data and "nous" in p_data.get("api_base", ""):
                    clean_model = f"openai/{model_id}" if not model_id.startswith("openai/") else model_id
                elif p_data and "cloudflare" in p_data.get("api_base", ""):
                    clean_model = f"cloudflare/{model_id}" if not model_id.startswith("cloudflare/") else model_id

                kwargs: Dict[str, Any] = {
                    "model": clean_model,
                    "input": [text],
                    "timeout": 60,
                }

                if p_data:
                    k = p_data.get("api_key")
                    b = p_data.get("api_base")
                    if k and k not in PLACEHOLDER_PREFIXES:
                        kwargs["api_key"] = k
                    if b:
                        kwargs["api_base"] = b

                for attempt in range(2):
                    try:
                        response = litellm.embedding(**kwargs)
                        if response and response.data:
                            return response.data[0]["embedding"]
                    except Exception as attempt_e:
                        if attempt == 1:
                            raise attempt_e
                        time.sleep(2 ** attempt)

            except Exception as e:
                logger.warning("Embedding Model %s failed: %s. Trying next...", model_id, e)
                last_error = e
                time.sleep(0.5)

        logger.error("All models in embeddings fallback chain failed. Last error: %s", last_error)
        return None

    async def acall(
        self,
        messages: List[Dict[str, str]],
        operation: str = "query",
        temperature: float = 0.2,
        override_model: Optional[str] = None,
    ) -> str:
        """Execute an asynchronous completion using the fallback chain configured for the given operation."""
        import asyncio
        base_models = [override_model] if override_model else self.config.get_fallback_models(operation)
        if len(base_models) > 1 and not override_model:
            offset = LLMProvider._call_counter % len(base_models)
            LLMProvider._call_counter += 1
            base_models = base_models[offset:] + base_models[:offset]
        models = RateLimitCooldownTracker.filter_available_models(base_models * 2)

        try:
            import litellm
            litellm.suppress_debug_info = True
        except ImportError:
            raise LLMProviderError("litellm is not installed.")

        last_error = None

        for model_id in models:
            p_data = self._find_provider_for_model(model_id)
            if p_data:
                api_key = p_data.get("api_key", "")
                if p_data.get("__placeholder") or api_key in PLACEHOLDER_PREFIXES:
                    continue

            logger.info("Attempting async completion with model: %s (operation: %s)", model_id, operation)

            try:
                clean_model = model_id
                if model_id.startswith("anthropic/"):
                    clean_model = model_id.replace("anthropic/", "")
                elif model_id.startswith("google/") or model_id.startswith("google-") or model_id.startswith("google "):
                    remainder = model_id.split("/", 1)[1] if "/" in model_id else model_id
                    clean_model = remainder if remainder.startswith("gemini/") else f"gemini/{remainder}"
                elif model_id.startswith("local/"):
                    clean_model = model_id.replace("local/", "")
                elif model_id.startswith("groq "):
                    clean_model = "groq/" + model_id.split("/", 1)[1]
                elif model_id.startswith("nous/"):
                    clean_model = model_id.replace("nous/", "")
                elif p_data and "nous" in p_data.get("api_base", ""):
                    clean_model = f"openai/{model_id}" if not model_id.startswith("openai/") else model_id
                elif model_id.startswith("cloud flare") or (p_data and "cloudflare" in p_data.get("api_base", "")):
                    raw_cf = model_id.split("/", 1)[1] if ("/" in model_id and not model_id.startswith("@cf/")) else model_id
                    clean_model = f"cloudflare/{raw_cf}" if not raw_cf.startswith("cloudflare/") else raw_cf

                kwargs = {
                    "model": clean_model,
                    "messages": messages,
                    "temperature": 1.0 if "gemini-3" in clean_model else temperature,
                    "max_tokens": 8192,
                    "timeout": 60 if "cloudflare" in clean_model else 30,
                }

                if "local/" in model_id or (p_data and "localhost" in str(p_data.get("api_base", ""))):
                    kwargs["num_ctx"] = 8192

                if p_data:
                    k = p_data.get("api_key")
                    b = p_data.get("api_base")
                    if k and k not in PLACEHOLDER_PREFIXES:
                        kwargs["api_key"] = k
                    if b:
                        if "/ai/run" in b:
                            b = b.replace("/ai/run", "/ai/v1")
                        kwargs["api_base"] = b

                for attempt in range(2):
                    try:
                        response = await litellm.acompletion(**kwargs)
                        content = response.choices[0].message.content
                        if content:
                            return content.strip()
                    except Exception as attempt_e:
                        if attempt == 1:
                            raise attempt_e
                        logger.warning("Attempt %d failed: %s. Retrying...", attempt + 1, attempt_e)
                        await asyncio.sleep(2 ** attempt)

            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = (
                    "rate_limit" in err_str
                    or "429" in err_str
                    or "resource_exhausted" in err_str
                    or "quota" in err_str
                    or "503" in err_str
                    or "high demand" in err_str
                    or "temporarily unavailable" in err_str
                )
                if is_rate_limit:
                    RateLimitCooldownTracker.mark_cooling_down(model_id, cooldown_seconds=60.0)
                    self.rate_limits.append({
                        "model": model_id,
                        "operation": operation,
                        "error": str(e),
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    logger.warning("Rate limit hit on async model %s: %s (cooling down for 60s)", model_id, e)

                logger.warning("Model %s failed: %s. Trying next model...", model_id, e)
                last_error = e
                await asyncio.sleep(0.5)

        raise LLMProviderError(f"All models in async fallback chain failed. Last error: {last_error}")

    def stream_call(
        self,
        messages: List[Dict[str, str]],
        operation: str = "query",
        temperature: float = 0.2,
    ):
        """Stream completion chunks in real-time.
        
        (V5 Feature: Real-Time Streaming Output)
        """
        base_models = self.config.get_fallback_models(operation)
        models = RateLimitCooldownTracker.filter_available_models(base_models * 2)

        try:
            import litellm
            litellm.suppress_debug_info = True
        except ImportError:
            raise LLMProviderError("litellm is not installed.")

        for model_id in models:
            p_data = self._find_provider_for_model(model_id)
            if p_data:
                api_key = p_data.get("api_key", "")
                if p_data.get("__placeholder") or api_key in PLACEHOLDER_PREFIXES:
                    continue

            try:
                clean_model = model_id
                if model_id.startswith("anthropic/"):
                    clean_model = model_id.replace("anthropic/", "")
                elif model_id.startswith("google/") or model_id.startswith("google-") or model_id.startswith("google "):
                    remainder = model_id.split("/", 1)[1] if "/" in model_id else model_id
                    clean_model = remainder if remainder.startswith("gemini/") else f"gemini/{remainder}"
                elif model_id.startswith("local/"):
                    clean_model = model_id.replace("local/", "")
                elif model_id.startswith("groq "):
                    clean_model = "groq/" + model_id.split("/", 1)[1]
                elif model_id.startswith("nous/"):
                    clean_model = model_id.replace("nous/", "")
                elif p_data and "nous" in p_data.get("api_base", ""):
                    clean_model = f"openai/{model_id}" if not model_id.startswith("openai/") else model_id
                elif p_data and "cloudflare" in p_data.get("api_base", ""):
                    clean_model = f"cloudflare/{model_id}" if not model_id.startswith("cloudflare/") else model_id

                kwargs: Dict[str, Any] = {
                    "model": clean_model,
                    "messages": messages,
                    "temperature": 1.0 if "gemini-3" in clean_model else temperature,
                    "max_tokens": 8192,
                    "timeout": 30,
                    "stream": True,
                }
                if p_data:
                    k = p_data.get("api_key")
                    b = p_data.get("api_base")
                    if k and k not in PLACEHOLDER_PREFIXES:
                        kwargs["api_key"] = k
                    if b:
                        kwargs["api_base"] = b

                response = litellm.completion(**kwargs)
                for chunk in response:
                    content = getattr(chunk.choices[0].delta, "content", None) or ""
                    if content:
                        yield content
                return
            except Exception as e:
                logger.warning("Streaming with model %s failed: %s. Trying next...", model_id, e)
                continue

