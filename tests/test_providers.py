import pytest
from lib.providers import LLMProvider
from lib.config import ProvidersConfig

def test_placeholder_keys():
    config = ProvidersConfig(
        providers={
            "test1": {"api_key": "sk-ant-...", "models": ["model1"]},
            "test2": {"api_key": "sk-real-key", "models": ["model2"]},
            "test3": {"api_key": "", "__placeholder": True, "models": ["model3"]},
        }
    )
    pm = LLMProvider(config)
    assert pm._find_provider_for_model("model1") is not None
    assert pm._find_provider_for_model("model2") is not None
    assert pm._find_provider_for_model("model3") is not None


def test_rate_limit_cooldown_tracker():
    from lib.providers import RateLimitCooldownTracker
    import time

    RateLimitCooldownTracker.clear()
    assert not RateLimitCooldownTracker.is_cooling_down("m1")

    # Mark cooling down
    RateLimitCooldownTracker.mark_cooling_down("m1", cooldown_seconds=0.5)
    assert RateLimitCooldownTracker.is_cooling_down("m1")

    models = ["m1", "m2", "m3"]
    filtered = RateLimitCooldownTracker.filter_available_models(models)
    assert filtered == ["m2", "m3"]

    # When all models are cooling down, should bypass so it doesn't deadlock
    RateLimitCooldownTracker.mark_cooling_down("m2", cooldown_seconds=0.5)
    RateLimitCooldownTracker.mark_cooling_down("m3", cooldown_seconds=0.5)
    assert RateLimitCooldownTracker.filter_available_models(["m1", "m2", "m3"]) == ["m1", "m2", "m3"]

    # Wait for cooldown to expire
    time.sleep(0.6)
    assert not RateLimitCooldownTracker.is_cooling_down("m1")
    assert RateLimitCooldownTracker.filter_available_models(["m1", "m2", "m3"]) == ["m1", "m2", "m3"]
    RateLimitCooldownTracker.clear()


def test_quota_exhaustion_does_not_bypass():
    """Bug #4 regression test: daily quota exhaustion must never be unbanned via the deadlock bypass."""
    from lib.providers import RateLimitCooldownTracker

    RateLimitCooldownTracker.clear()
    RateLimitCooldownTracker.mark_cooling_down("m1", cooldown_seconds=3600.0, is_quota_exhausted=True)
    RateLimitCooldownTracker.mark_cooling_down("m2", cooldown_seconds=3600.0, is_quota_exhausted=True)

    assert RateLimitCooldownTracker.is_cooling_down("m1")
    assert RateLimitCooldownTracker.is_quota_exhausted("m1")

    # When all candidate models have quota exhausted, must NOT un-ban them
    filtered = RateLimitCooldownTracker.filter_available_models(["m1", "m2"])
    assert filtered == []

    # If mixed: m1 is quota exhausted, m2 is short rate limit
    RateLimitCooldownTracker.clear()
    RateLimitCooldownTracker.mark_cooling_down("m1", cooldown_seconds=3600.0, is_quota_exhausted=True)
    RateLimitCooldownTracker.mark_cooling_down("m2", cooldown_seconds=30.0, is_quota_exhausted=False)
    filtered = RateLimitCooldownTracker.filter_available_models(["m1", "m2"])
    assert filtered == ["m2"]
    RateLimitCooldownTracker.clear()


def test_quota_exhaustion_fails_fast():
    """Bug #4 regression test: when all models are quota exhausted, operations fail fast immediately."""
    from lib.providers import LLMProvider, RateLimitCooldownTracker, LLMProviderError

    RateLimitCooldownTracker.clear()
    RateLimitCooldownTracker.mark_cooling_down("test/m1", cooldown_seconds=3600.0, is_quota_exhausted=True)

    config = ProvidersConfig(
        providers={"test": {"api_key": "dummy", "models": ["m1"]}},
        fallback_chain={"query": ["test/m1"]}
    )
    pm = LLMProvider(config)
    with pytest.raises(LLMProviderError, match="cooling down or quota-exhausted"):
        pm.call([{"role": "user", "content": "hi"}], operation="query")

    RateLimitCooldownTracker.clear()


def test_environment_setup(monkeypatch):
    """Test environment variable exports for various providers."""
    import os
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)

    config = ProvidersConfig(
        providers={
            "anthropic": {"api_key": "sk-ant-live-real-key-123", "models": ["claude-3"]},
            "openai": {"api_key": "sk-proj-live-real-key-456", "models": ["gpt-4o"]},
            "google": {"api_key": "AIzaSyLiveRealKey789", "api_base": "https://custom.gemini", "models": ["gemini-pro"]},
            "local": {"api_base": "http://127.0.0.1:11434", "models": ["qwen"]},
        }
    )
    pm = LLMProvider(config)
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-live-real-key-123"
    assert os.environ.get("OPENAI_API_KEY") == "sk-proj-live-real-key-456"
    assert os.environ.get("GEMINI_API_KEY") == "AIzaSyLiveRealKey789"
    assert os.environ.get("GEMINI_API_BASE") == "https://custom.gemini"
    assert os.environ.get("OLLAMA_API_BASE") == "http://127.0.0.1:11434"


def test_find_provider_inferences():
    """Test model name detection and provider inference."""
    config = ProvidersConfig(
        providers={
            "anthropic": {"api_key": "a", "models": ["claude-3-opus"]},
            "openai": {"api_key": "b", "models": ["gpt-4o"]},
            "google": {"api_key": "c", "models": ["gemini-1.5-pro"]},
            "local": {"api_base": "http://localhost", "models": ["ollama/llama3"]},
            "groq": {"api_key": "d", "models": ["groq/llama3-70b"]},
        }
    )
    pm = LLMProvider(config)
    assert pm._find_provider_for_model("anthropic/claude-3-opus") == config.providers["anthropic"]
    assert pm._find_provider_for_model("gemini-something") == config.providers["google"]
    assert pm._find_provider_for_model("claude-instant") == config.providers["anthropic"]
    assert pm._find_provider_for_model("gpt-4-turbo") == config.providers["openai"]
    assert pm._find_provider_for_model("qwen-2.5") == config.providers["local"]
    assert pm._find_provider_for_model("groq/llama3-70b") == config.providers["groq"]
    assert pm._find_provider_for_model("unknown-model-xyz") is None


def test_call_success_mocked(monkeypatch):
    """Test completion call with mocked litellm."""
    from unittest.mock import MagicMock
    import litellm

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Test completion output"
    mock_resp.choices[0].message.reasoning_content = None

    monkeypatch.setattr(litellm, "completion", lambda **kwargs: mock_resp)

    config = ProvidersConfig(
        providers={"test": {"api_key": "valid_key", "models": ["test/model"]}},
        fallback_chain={"query": ["test/model"]}
    )
    pm = LLMProvider(config)
    res = pm.call([{"role": "user", "content": "hi"}], operation="query")
    assert res == "Test completion output"


def test_call_reasoning_content_fallback(monkeypatch):
    """Test completion using reasoning_content when content is blank (e.g. DeepSeek R1)."""
    from unittest.mock import MagicMock
    import litellm

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = ""
    mock_resp.choices[0].message.reasoning_content = "Reasoning result"

    monkeypatch.setattr(litellm, "completion", lambda **kwargs: mock_resp)

    config = ProvidersConfig(
        providers={"test": {"api_key": "valid_key", "models": ["test/model"]}},
        fallback_chain={"query": ["test/model"]}
    )
    pm = LLMProvider(config)
    res = pm.call([{"role": "user", "content": "hi"}], operation="query")
    assert res == "Reasoning result"


def test_call_fallback_on_rate_limit(monkeypatch):
    """Test fallback chain fails over from model 1 to model 2 on 429."""
    from unittest.mock import MagicMock
    import litellm
    from lib.providers import RateLimitCooldownTracker

    RateLimitCooldownTracker.clear()
    calls = []

    def mock_completion(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if "m1" in model:
            raise Exception("RateLimitError 429: Rate limit reached")
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "Fallback success"
        return resp

    monkeypatch.setattr(litellm, "completion", mock_completion)

    config = ProvidersConfig(
        providers={"test": {"api_key": "valid_key", "models": ["m1", "m2"]}},
        fallback_chain={"query": ["test/m1", "test/m2"]}
    )
    pm = LLMProvider(config)
    res = pm.call([{"role": "user", "content": "hi"}], operation="query")
    assert res == "Fallback success"
    # m1 should have been attempted twice before falling back to m2
    assert "test/m1" in calls
    assert "test/m2" in calls
    assert RateLimitCooldownTracker.is_cooling_down("test/m1")
    RateLimitCooldownTracker.clear()


@pytest.mark.anyio
async def test_acall_success_and_fallback(monkeypatch):
    """Test async completion with mocked litellm."""
    from unittest.mock import MagicMock
    import litellm
    from lib.providers import RateLimitCooldownTracker

    RateLimitCooldownTracker.clear()

    async def mock_acompletion(**kwargs):
        model = kwargs["model"]
        if "failing" in model:
            raise Exception("503 Service Unavailable")
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "Async success"
        return resp

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)

    config = ProvidersConfig(
        providers={"test": {"api_key": "valid_key", "models": ["failing", "working"]}},
        fallback_chain={"query": ["test/failing", "test/working"]}
    )
    pm = LLMProvider(config)
    res = await pm.acall([{"role": "user", "content": "hi"}], operation="query")
    assert res == "Async success"
    RateLimitCooldownTracker.clear()


def test_embed_success(monkeypatch):
    """Test vector embedding via provider."""
    from unittest.mock import MagicMock
    import litellm

    mock_resp = MagicMock()
    mock_resp.data = [{"embedding": [0.1, 0.2, 0.3, 0.4]}]

    monkeypatch.setattr(litellm, "embedding", lambda **kwargs: mock_resp)

    config = ProvidersConfig(
        providers={"test": {"api_key": "valid_key", "models": ["text-embedding-3-small"]}},
        fallback_chain={"embeddings": ["test/text-embedding-3-small"]}
    )
    pm = LLMProvider(config)
    vec = pm.embed("hello world")
    assert vec == [0.1, 0.2, 0.3, 0.4]


def test_stream_completion(monkeypatch):
    """Test real-time token streaming generator."""
    from unittest.mock import MagicMock
    import litellm

    def chunk(delta_text):
        c = MagicMock()
        c.choices = [MagicMock()]
        c.choices[0].delta.content = delta_text
        return c

    mock_stream = [chunk("Hello "), chunk("world"), chunk("!")]
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: iter(mock_stream))

    config = ProvidersConfig(
        providers={"test": {"api_key": "valid_key", "models": ["test/model"]}},
        fallback_chain={"query": ["test/model"]}
    )
    pm = LLMProvider(config)
    output = "".join(list(pm.stream_call([{"role": "user", "content": "hi"}], operation="query")))
    assert output == "Hello world!"



