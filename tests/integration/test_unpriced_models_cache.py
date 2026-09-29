from types import SimpleNamespace

import pytest

from janus.dashboard.reload import reload_pricing
from janus.storage.database import close_connection_pools, get_connection, init_db
from janus.storage.usage import get_unpriced_models, invalidate_unpriced_models_cache


@pytest.fixture(autouse=True)
def reset_cache():
    invalidate_unpriced_models_cache()
    yield
    invalidate_unpriced_models_cache()


async def _seed_zero_cost_usage(db_path, model):
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO usage (timestamp, model, provider_id, input_tokens, output_tokens)
               VALUES (datetime('now'), ?, 'p1', 100, 50)""",
            (model,),
        )
        await db.commit()


async def test_reload_pricing_invalidates_unpriced_models_cache(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await _seed_zero_cost_usage(db_path, "model-a")

    first = await get_unpriced_models(db_path)
    assert [row["model"] for row in first] == ["model-a"]

    await _seed_zero_cost_usage(db_path, "model-b")
    cached = await get_unpriced_models(db_path)
    assert [row["model"] for row in cached] == ["model-a"]

    app = SimpleNamespace(state=SimpleNamespace(db_path=db_path))
    await reload_pricing(app)

    refreshed = await get_unpriced_models(db_path)
    assert sorted(row["model"] for row in refreshed) == ["model-a", "model-b"]

    await close_connection_pools(db_path)
