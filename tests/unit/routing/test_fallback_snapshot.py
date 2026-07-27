from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler


def test_routing_snapshot_keys() -> None:
    fh = FallbackHandler(ProviderRegistry())
    fh._rotation_counters["openai"] = 2
    snap = fh.routing_snapshot()
    assert snap["rotation_counters"]["openai"] == 2
    assert "account_strategy" in snap
    assert "sticky" in snap
