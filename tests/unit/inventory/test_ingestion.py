from janus.inventory.ingestion import enforce_batch_size, validate_key_value


def test_validate_key_value_rejects_short_keys():
    assert validate_key_value("short") is not None


def test_validate_key_value_rejects_urls():
    assert validate_key_value("https://example.com/secret-key-value") is not None


def test_validate_key_value_accepts_normal_key():
    assert validate_key_value("sk-proj-" + "a" * 16) is None


def test_enforce_batch_size():
    assert enforce_batch_size(1) is None
    assert enforce_batch_size(10_000) is not None
