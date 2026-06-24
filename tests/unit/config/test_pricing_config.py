from janus.config.schema import JanusConfig


def test_pricing_defaults_empty():
    cfg = JanusConfig()
    assert cfg.pricing == {}


def test_pricing_accepts_overrides():
    cfg = JanusConfig(
        pricing={
            "custom-model": {
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "cache_creation_per_mtok": 0.5,
                "cache_read_per_mtok": 0.1,
            }
        }
    )
    assert "custom-model" in cfg.pricing
    assert cfg.pricing["custom-model"]["input_per_mtok"] == 1.0
