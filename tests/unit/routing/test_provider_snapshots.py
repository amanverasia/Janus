from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from janus.api import routes
from janus.providers.base import Provider
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.provider_snapshots import (
    ProviderSnapshot,
    acquire_provider_snapshot,
    close_provider_snapshots,
    install_provider_snapshot,
    release_provider_snapshot,
)


class FakeProvider:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


def _app_with_provider(provider: FakeProvider) -> FastAPI:
    app = FastAPI()
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    app.state.providers = {"provider": cast(Provider, provider)}
    app.state.registry = registry
    app.state.fallback_handler = handler
    return app


def _request(app: FastAPI) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "app": app,
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [],
    }
    return Request(scope)


async def test_stream_holds_snapshot_until_body_finishes(monkeypatch) -> None:
    old_provider = FakeProvider()
    app = _app_with_provider(old_provider)

    async def fake_handle(
        client_format: str,
        body: dict[str, Any],
        request: Request,
        snapshot: ProviderSnapshot,
    ) -> StreamingResponse:
        assert client_format == "openai"
        assert body == {"model": "test"}
        assert request.app is app
        assert snapshot.providers["provider"] is old_provider

        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            yield b"second"

        return StreamingResponse(chunks())

    monkeypatch.setattr(routes, "_handle_with_snapshot", fake_handle)
    response = await routes._handle("openai", {"model": "test"}, _request(app))
    assert isinstance(response, StreamingResponse)
    old_snapshot = app.state.provider_snapshot
    assert old_snapshot.leases == 1

    new_provider = FakeProvider()
    new_registry = ProviderRegistry()
    await install_provider_snapshot(
        app,
        ProviderSnapshot(
            providers={"provider": cast(Provider, new_provider)},
            registry=new_registry,
            handler=FallbackHandler(new_registry),
        ),
        providers_to_close=[cast(Provider, old_provider)],
    )

    assert old_provider.close_count == 0
    assert [chunk async for chunk in response.body_iterator] == [b"first", b"second"]
    assert old_provider.close_count == 1
    assert old_snapshot.leases == 0
    assert old_snapshot not in app.state.retired_provider_snapshots


async def test_shutdown_closes_current_and_still_leased_retired_snapshots() -> None:
    old_provider = FakeProvider()
    app = _app_with_provider(old_provider)
    old_snapshot = acquire_provider_snapshot(app)
    new_provider = FakeProvider()
    new_registry = ProviderRegistry()
    await install_provider_snapshot(
        app,
        ProviderSnapshot(
            providers={"provider": cast(Provider, new_provider)},
            registry=new_registry,
            handler=FallbackHandler(new_registry),
        ),
        providers_to_close=[cast(Provider, old_provider)],
    )

    assert old_snapshot.leases == 1
    assert old_provider.close_count == 0
    await close_provider_snapshots(app)

    assert old_provider.close_count == 1
    assert new_provider.close_count == 1
    assert app.state.retired_provider_snapshots == []


async def test_reused_executor_waits_for_leases_from_every_generation() -> None:
    reused_provider = FakeProvider()
    app = _app_with_provider(reused_provider)
    first_snapshot = acquire_provider_snapshot(app)
    second_registry = ProviderRegistry()
    await install_provider_snapshot(
        app,
        ProviderSnapshot(
            providers={"provider": cast(Provider, reused_provider)},
            registry=second_registry,
            handler=FallbackHandler(second_registry),
        ),
        providers_to_close=[],
    )
    replacement = FakeProvider()
    third_registry = ProviderRegistry()
    await install_provider_snapshot(
        app,
        ProviderSnapshot(
            providers={"provider": cast(Provider, replacement)},
            registry=third_registry,
            handler=FallbackHandler(third_registry),
        ),
        providers_to_close=[cast(Provider, reused_provider)],
    )

    assert reused_provider.close_count == 0
    await release_provider_snapshot(app, first_snapshot)

    assert reused_provider.close_count == 1
