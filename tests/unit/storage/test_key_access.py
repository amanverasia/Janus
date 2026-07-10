from janus.storage.key_access import (
    model_allowed,
    parse_allowed_models,
    parse_models_input,
    serialize_allowed_models,
)


def test_parse_allowed_models_unrestricted() -> None:
    assert parse_allowed_models(None) is None
    assert parse_allowed_models("") is None
    assert parse_allowed_models("[]") is None
    assert parse_allowed_models([]) is None


def test_parse_allowed_models_json() -> None:
    assert parse_allowed_models('["openai/gpt-4o", "openai/*"]') == [
        "openai/gpt-4o",
        "openai/*",
    ]


def test_parse_allowed_models_comma_fallback() -> None:
    assert parse_allowed_models("a, b\nc") == ["a", "b", "c"]


def test_serialize_roundtrip() -> None:
    assert serialize_allowed_models(None) is None
    assert serialize_allowed_models([]) is None
    raw = serialize_allowed_models(["openai/*", "combo"])
    assert parse_allowed_models(raw) == ["openai/*", "combo"]


def test_model_allowed_unrestricted() -> None:
    assert model_allowed("openai/gpt-4o", None) is True


def test_model_allowed_exact() -> None:
    allowed = ["openai/gpt-4o", "my-combo"]
    assert model_allowed("openai/gpt-4o", allowed) is True
    assert model_allowed("my-combo", allowed) is True
    assert model_allowed("openai/gpt-4.1", allowed) is False


def test_model_allowed_prefix_wildcard() -> None:
    allowed = ["openai/*"]
    assert model_allowed("openai/gpt-4o", allowed) is True
    assert model_allowed("openai/o1", allowed) is True
    assert model_allowed("openai", allowed) is False
    assert model_allowed("anthropic/claude", allowed) is False


def test_parse_models_input() -> None:
    assert parse_models_input("") is None
    assert parse_models_input("  ") is None
    assert parse_models_input("openai/*,\nmy-combo") == ["openai/*", "my-combo"]
