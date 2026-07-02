import pytest

from janus.inventory.ingestion import KeyIngestEntry, ingest_upstream_key
from janus.storage.database import init_db
from janus.storage.upstream_keys import create_upstream_key, get_upstream_key


@pytest.mark.asyncio
async def test_ingest_rejects_duplicate_across_providers(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-" + "x" * 16)

    result = await ingest_upstream_key(
        db_path,
        KeyIngestEntry(key="sk-proj-" + "x" * 16),
        chosen_provider="groq",
    )
    assert result["status"] == "exists"


@pytest.mark.asyncio
async def test_ingest_updates_unidentified_key(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    record = await create_upstream_key(
        db_path,
        provider_id="unidentified",
        key_value="sk-proj-" + "y" * 16,
    )
    await ingest_upstream_key(
        db_path,
        KeyIngestEntry(key="sk-proj-" + "y" * 16, provider="openai"),
        chosen_provider="auto",
    )
    updated = await get_upstream_key(db_path, record["id"])
    assert updated is not None
    assert updated["provider_id"] == "openai"
