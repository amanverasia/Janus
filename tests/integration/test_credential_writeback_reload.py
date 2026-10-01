from __future__ import annotations

import json
import time
from typing import Any

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.dashboard.reload import reload_providers
from janus.inventory.credential_store import SqliteCredentialStore
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key


async def test_inventory_backed_oauth_provider_gets_credential_store(tmp_path: Any) -> None:
    app = create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )
    db_path = app.state.db_path
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "codex",
            "prefix": "codex",
            "api_type": "codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "models": ["gpt-5.1-codex"],
        },
    )
    blob = json.dumps(
        {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600}
    )
    key = await create_upstream_key(db_path, provider_id="codex", key_value=blob)
    await update_upstream_key(
        db_path,
        str(key["id"]),
        {"status": "active", "is_valid": 1, "is_usable": 1, "usability_status": "usable"},
    )

    await reload_providers(app)

    backed = [
        provider
        for provider in app.state.providers.values()
        if isinstance(getattr(provider, "_credential_store", None), SqliteCredentialStore)
    ]
    assert backed, "inventory-backed Codex provider should have a credential store"
    assert backed[0]._credential_store.upstream_key_id == str(key["id"])


async def test_second_reload_keeps_credential_store_on_reused_provider(tmp_path: Any) -> None:
    app = create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )
    db_path = app.state.db_path
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "codex",
            "prefix": "codex",
            "api_type": "codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "models": ["gpt-5.1-codex"],
        },
    )
    blob = json.dumps(
        {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600}
    )
    key = await create_upstream_key(db_path, provider_id="codex", key_value=blob)
    await update_upstream_key(
        db_path,
        str(key["id"]),
        {"status": "active", "is_valid": 1, "is_usable": 1, "usability_status": "usable"},
    )

    await reload_providers(app)
    first = {
        pid: provider
        for pid, provider in app.state.providers.items()
        if isinstance(getattr(provider, "_credential_store", None), SqliteCredentialStore)
    }
    assert first

    await reload_providers(app)
    second = {
        pid: provider
        for pid, provider in app.state.providers.items()
        if isinstance(getattr(provider, "_credential_store", None), SqliteCredentialStore)
    }

    assert second.keys() == first.keys()
    for pid, provider in second.items():
        assert provider is first[pid]
        assert provider._credential_store.upstream_key_id == str(key["id"])
