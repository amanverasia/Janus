from janus.storage.database import get_connection, init_db
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key
from janus.storage.upstream_models import (
    list_distinct_discovered_models,
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


async def test_list_model_ids_for_keys_deduplicates_within_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-test-duplicate-models",
    )
    await update_upstream_key(
        db_path,
        str(key["id"]),
        {"models_discovered_at": "2026-08-26 00:00:00"},
    )
    await replace_models_for_key(
        db_path,
        upstream_key_id=str(key["id"]),
        provider_id="openai",
        models=[
            {"model_id": "gpt-4o"},
            {"model_id": "gpt-4.1"},
            {"model_id": "gpt-4o"},
            {"model_id": "gpt-4.1"},
            {"model_id": "gpt-4o"},
        ],
    )
    async with get_connection(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM upstream_models") as cur:
            raw_rows = (await cur.fetchone())[0]

    assert raw_rows == 5
    assert await list_model_ids_for_keys(db_path, [str(key["id"])]) == {
        str(key["id"]): ["gpt-4.1", "gpt-4o"]
    }


async def _stamp_created_at(
    db_path,
    upstream_key_id: str,
    created_at: str,
    *,
    model_id: str | None = None,
) -> None:
    async with get_connection(db_path) as db:
        if model_id is None:
            await db.execute(
                "UPDATE upstream_models SET created_at = ? WHERE upstream_key_id = ?",
                (created_at, upstream_key_id),
            )
        else:
            await db.execute(
                "UPDATE upstream_models SET created_at = ?"
                " WHERE upstream_key_id = ? AND model_id = ?",
                (created_at, upstream_key_id, model_id),
            )
        await db.commit()


async def test_distinct_discovered_models_collapses_key_duplicates(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key_ids = []
    for index in range(2):
        key = await create_upstream_key(
            db_path,
            provider_id="openai",
            key_value=f"sk-test-collapse-{index}",
        )
        key_ids.append(str(key["id"]))
    await replace_models_for_key(
        db_path,
        upstream_key_id=key_ids[0],
        provider_id="openai",
        models=[
            {"model_id": "gpt-4o", "display_name": "GPT-4o old", "context_window": 128_000},
            {"model_id": "gpt-4.1"},
        ],
    )
    await replace_models_for_key(
        db_path,
        upstream_key_id=key_ids[1],
        provider_id="openai",
        models=[
            {"model_id": "gpt-4o", "display_name": "GPT-4o new"},
            {"model_id": "gpt-4.1", "context_window": 1_000_000},
        ],
    )
    await _stamp_created_at(db_path, key_ids[0], "2026-01-01 00:00:00", model_id="gpt-4o")
    await _stamp_created_at(db_path, key_ids[0], "2026-01-02 00:00:00", model_id="gpt-4.1")
    await _stamp_created_at(db_path, key_ids[1], "2026-02-01 00:00:00", model_id="gpt-4o")
    await _stamp_created_at(db_path, key_ids[1], "2026-02-02 00:00:00", model_id="gpt-4.1")

    rows = await list_distinct_discovered_models(db_path)

    assert [(row["provider_id"], row["model_id"]) for row in rows] == [
        ("openai", "gpt-4o"),
        ("openai", "gpt-4.1"),
    ]
    by_model = {row["model_id"]: row for row in rows}
    assert by_model["gpt-4o"]["display_name"] == "GPT-4o new"
    assert by_model["gpt-4o"]["context_window"] == 128_000
    assert by_model["gpt-4.1"]["context_window"] == 1_000_000
    assert by_model["gpt-4.1"]["display_name"] is None


async def test_distinct_discovered_models_scales_with_pairs_not_key_rows(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    keys_per_provider = 20
    openai_models = [f"gpt-{index}" for index in range(50)]
    qwen_models = [f"qwen-{index}" for index in range(20)]
    key_index = 0
    for provider_id, models in (("openai", openai_models), ("qwen", qwen_models)):
        for _ in range(keys_per_provider):
            key = await create_upstream_key(
                db_path,
                provider_id=provider_id,
                key_value=f"sk-test-scale-{key_index}",
            )
            key_index += 1
            await replace_models_for_key(
                db_path,
                upstream_key_id=str(key["id"]),
                provider_id=provider_id,
                models=[{"model_id": model} for model in models],
            )
    async with get_connection(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM upstream_models") as cur:
            raw_rows = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(DISTINCT provider_id || '/' || model_id) FROM upstream_models"
        ) as cur:
            distinct_pairs = (await cur.fetchone())[0]

    rows = await list_distinct_discovered_models(db_path)

    assert raw_rows == keys_per_provider * (len(openai_models) + len(qwen_models))
    assert distinct_pairs == len(openai_models) + len(qwen_models)
    assert len(rows) == distinct_pairs
    assert rows == await list_distinct_discovered_models(db_path)
