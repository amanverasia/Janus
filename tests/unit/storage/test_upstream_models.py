from janus.storage.database import init_db
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key
from janus.storage.upstream_models import (
    list_live_model_ids_for_provider,
    list_model_ids_for_keys,
    replace_models_for_key,
)


async def test_discovered_model_id_views_are_available_only(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await replace_models_for_key(
        db_path,
        upstream_key_id="key-one",
        provider_id="openai",
        models=[{"model_id": "gpt-4.1"}, {"model_id": "gpt-4o"}],
    )
    await replace_models_for_key(
        db_path,
        upstream_key_id="key-two",
        provider_id="openai",
        models=[{"model_id": "gpt-4o"}],
    )

    assert await list_model_ids_for_keys(db_path, ["key-one", "key-two", "missing"]) == {
        "key-one": ["gpt-4.1", "gpt-4o"],
        "key-two": ["gpt-4o"],
    }
    assert await list_live_model_ids_for_provider(db_path, "openai") == ["gpt-4.1", "gpt-4o"]
    assert await list_model_ids_for_keys(db_path, []) == {}


async def test_successful_empty_discovery_is_authoritative(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-test-empty-discovery",
    )
    await update_upstream_key(
        db_path,
        str(key["id"]),
        {"models_discovered_at": "2026-08-26 00:00:00"},
    )

    assert await list_model_ids_for_keys(db_path, [str(key["id"])]) == {str(key["id"]): []}
