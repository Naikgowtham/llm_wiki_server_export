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

