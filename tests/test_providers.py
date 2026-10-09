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


