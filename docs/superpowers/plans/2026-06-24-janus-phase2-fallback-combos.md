# Janus Phase 2: Fallback & Combos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-account rotation, named combo sequences, and rate-limit cooldown to the routing layer — so Janus can try N API keys per model and fall through an ordered model list until one succeeds.

**Architecture:** Registry stores `list[ProviderConfig]` per prefix (multi-account). FallbackHandler expands combos → models → available accounts, tracks cooldown state in-memory. Routes.py iterates the ordered attempt list with retry.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, httpx, pytest, respx.

---

### Task 1: Config schema — add ComboConfig

**Files:**
- Modify: `src/janus/config/schema.py`
- Modify: `tests/unit/config/test_schema.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/config/test_schema.py`:

```python
from janus.config.schema import ComboConfig


def test_combo_config():
    c = ComboConfig(name="my-stack", models=["glm/glm-4.7", "an/claude-sonnet-4-20250514"])
    assert c.name == "my-stack"
    assert len(c.models) == 2


def test_janus_config_has_combos():
    from janus.config.schema import JanusConfig
    config = JanusConfig(combos=[ComboConfig(name="test", models=["a/b"])])
    assert len(config.combos) == 1
    assert config.combos[0].name == "test"
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv/bin/python -m pytest tests/unit/config/test_schema.py::test_combo_config tests/unit/config/test_schema.py::test_janus_config_has_combos -v
```

- [ ] **Step 3: Implement**

Add to `src/janus/config/schema.py`:

```python
class ComboConfig(BaseModel):
    name: str
    models: list[str]
```

Add `combos` field to `JanusConfig`:

```python
class JanusConfig(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    providers: list[ProviderConfig] = Field(default_factory=list)
    combos: list[ComboConfig] = Field(default_factory=list)
    api_keys: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/unit/config/ -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/config/schema.py tests/unit/config/test_schema.py
git commit -m "feat: add ComboConfig to config schema"
```

---

### Task 2: Registry — multi-account support

**Files:**
- Modify: `src/janus/providers/registry.py`
- Modify: `tests/unit/providers/test_registry.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/providers/test_registry.py`:

```python
def test_multi_account_same_prefix():
    registry = ProviderRegistry()
    config1 = ProviderConfig(id="ds-1", prefix="ds", api_type="openai_compat", base_url="https://ds.com", api_key="k1", models=["m1"])
    config2 = ProviderConfig(id="ds-2", prefix="ds", api_type="openai_compat", base_url="https://ds.com", api_key="k2", models=["m1"])
    registry.register(config1)
    registry.register(config2)
    targets = registry.lookup("ds/m1")
    assert targets is not None
    assert len(targets) == 2
    assert targets[0].account_id == "ds-1"
    assert targets[1].account_id == "ds-2"


def test_lookup_returns_none_for_unknown():
    registry = ProviderRegistry()
    assert registry.lookup("no/such") is None


def test_register_combo():
    registry = ProviderRegistry()
    from janus.config.schema import ComboConfig
    registry.register_combo(ComboConfig(name="stack", models=["a/b", "c/d"]))
    result = registry.lookup_combo("stack")
    assert result == ["a/b", "c/d"]


def test_lookup_combo_unknown():
    registry = ProviderRegistry()
    assert registry.lookup_combo("nope") is None
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv/bin/python -m pytest tests/unit/providers/test_registry.py -v
```

- [ ] **Step 3: Implement**

Rewrite `src/janus/providers/registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from janus.config.schema import ComboConfig, ProviderConfig


@dataclass
class ResolvedTarget:
    prefix: str
    model: str
    provider_config: ProviderConfig
    native_format: str
    account_id: str


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, list[ProviderConfig]] = {}
        self._combos: dict[str, list[str]] = {}

    def register(self, config: ProviderConfig) -> None:
        if config.prefix not in self._providers:
            self._providers[config.prefix] = []
        self._providers[config.prefix].append(config)

    def register_combo(self, combo: ComboConfig) -> None:
        self._combos[combo.name] = combo.models

    def lookup(self, model_str: str) -> list[ResolvedTarget] | None:
        if "/" not in model_str:
            return None
        prefix, rest = model_str.split("/", 1)
        configs = self._providers.get(prefix)
        if not configs:
            return None
        results: list[ResolvedTarget] = []
        for config in configs:
            native = config.api_type.replace("_compat", "")
            results.append(ResolvedTarget(
                prefix=prefix,
                model=rest,
                provider_config=config,
                native_format=native,
                account_id=config.id,
            ))
        return results

    def lookup_combo(self, name: str) -> list[str] | None:
        return self._combos.get(name)

    @property
    def providers(self) -> dict[str, list[ProviderConfig]]:
        return self._providers

    @property
    def combos(self) -> dict[str, list[str]]:
        return self._combos
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/unit/providers/test_registry.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/providers/registry.py tests/unit/providers/test_registry.py
git commit -m "feat: multi-account registry (list per prefix) + combo support"
```

---

### Task 3: FallbackHandler — cooldown + attempt resolution

**Files:**
- Modify: `src/janus/routing/fallback.py`
- Modify: `tests/unit/routing/test_resolver.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/routing/test_resolver.py`:

```python
import time
from janus.routing.fallback import FallbackHandler, COOLDOWN_DURATIONS


def test_resolve_single_model_multi_account():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(id="ds-1", prefix="ds", api_type="openai_compat", base_url="https://ds.com", api_key="k1", models=["m1"]))
    registry.register(ProviderConfig(id="ds-2", prefix="ds", api_type="openai_compat", base_url="https://ds.com", api_key="k2", models=["m1"]))
    handler = FallbackHandler(registry)
    attempts = handler.resolve_attempts("ds/m1")
    assert len(attempts) == 2


def test_resolve_combo_expansion():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(id="a", prefix="a", api_type="openai_compat", base_url="https://a.com", api_key="k", models=["b"]))
    registry.register(ProviderConfig(id="c", prefix="c", api_type="anthropic", base_url="https://c.com", api_key="k", models=["d"]))
    from janus.config.schema import ComboConfig
    registry.register_combo(ComboConfig(name="stk", models=["a/b", "c/d"]))
    handler = FallbackHandler(registry)
    attempts = handler.resolve_attempts("stk")
    assert len(attempts) == 2
    assert attempts[0].model == "b"
    assert attempts[1].model == "d"


def test_cooldown_filters_account():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(id="ds-1", prefix="ds", api_type="openai_compat", base_url="https://ds.com", api_key="k1", models=["m1"]))
    registry.register(ProviderConfig(id="ds-2", prefix="ds", api_type="openai_compat", base_url="https://ds.com", api_key="k2", models=["m1"]))
    handler = FallbackHandler(registry)
    handler.mark_cooldown("ds-1", "rate_limit")
    attempts = handler.resolve_attempts("ds/m1")
    assert len(attempts) == 1
    assert attempts[0].account_id == "ds-2"


def test_cooldown_expiry():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(id="x", prefix="x", api_type="openai_compat", base_url="https://x.com", api_key="k", models=["m"]))
    handler = FallbackHandler(registry)
    handler.mark_cooldown("x", "network", duration=0.0)
    time.sleep(0.01)
    attempts = handler.resolve_attempts("x/m")
    assert len(attempts) == 1


def test_all_accounts_exhausted_raises():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(id="x", prefix="x", api_type="openai_compat", base_url="https://x.com", api_key="k", models=["m"]))
    handler = FallbackHandler(registry)
    handler.mark_cooldown("x", "rate_limit", duration=9999.0)
    with pytest.raises(ValueError, match="No available"):
        handler.resolve_attempts("x/m")


def test_unknown_model_raises():
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    with pytest.raises(ValueError, match="Unknown model"):
        handler.resolve_attempts("no/such")


def test_retry_after_override():
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    handler.mark_cooldown("x", "rate_limit", retry_after=120.0)
    assert not handler.is_available("x")
```

- [ ] **Step 2: Run test to verify failure**

```bash
.venv/bin/python -m pytest tests/unit/routing/test_resolver.py -v
```

- [ ] **Step 3: Implement**

Rewrite `src/janus/routing/fallback.py`:

```python
from __future__ import annotations

import time

from janus.providers.registry import ProviderRegistry, ResolvedTarget

COOLDOWN_DURATIONS: dict[str, float] = {
    "rate_limit": 60.0,
    "server_error": 30.0,
    "auth_error": 300.0,
    "network": 15.0,
}


class FallbackHandler:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self._cooldowns: dict[str, float] = {}

    def resolve_attempts(self, model_str: str) -> list[ResolvedTarget]:
        combo_models = self.registry.lookup_combo(model_str)
        if combo_models is not None:
            all_attempts: list[ResolvedTarget] = []
            for m in combo_models:
                targets = self.registry.lookup(m)
                if targets:
                    all_attempts.extend(t for t in targets if self.is_available(t.account_id))
            if not all_attempts:
                raise ValueError(f"No available providers for combo '{model_str}'")
            return all_attempts

        targets = self.registry.lookup(model_str)
        if targets is None:
            raise ValueError(f"Unknown model: {model_str}")
        available = [t for t in targets if self.is_available(t.account_id)]
        if not available:
            raise ValueError(f"No available providers for '{model_str}' (all accounts cooled down)")
        return available

    def mark_cooldown(
        self,
        account_id: str,
        error_type: str,
        retry_after: float | None = None,
        duration: float | None = None,
    ) -> None:
        if duration is not None:
            cooldown = duration
        elif retry_after is not None:
            cooldown = retry_after
        else:
            cooldown = COOLDOWN_DURATIONS.get(error_type, 60.0)
        self._cooldowns[account_id] = time.monotonic() + cooldown

    def is_available(self, account_id: str) -> bool:
        expiry = self._cooldowns.get(account_id)
        if expiry is None:
            return True
        return time.monotonic() >= expiry
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/unit/routing/ -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/routing/fallback.py tests/unit/routing/test_resolver.py
git commit -m "feat: FallbackHandler with combo expansion, multi-account, cooldown"
```

---

### Task 4: Error classification helper

**Files:**
- Create: `src/janus/routing/errors.py`
- Create: `tests/unit/routing/test_errors.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/routing/test_errors.py
import httpx
from janus.routing.errors import classify_error, is_fallback_eligible, ErrorType


def test_classify_429():
    assert classify_error(429) == ErrorType.RATE_LIMIT


def test_classify_500():
    assert classify_error(500) == ErrorType.SERVER_ERROR


def test_classify_401():
    assert classify_error(401) == ErrorType.AUTH_ERROR


def test_classify_403():
    assert classify_error(403) == ErrorType.AUTH_ERROR


def test_classify_400_not_eligible():
    assert classify_error(400) == ErrorType.CLIENT_ERROR
    assert not is_fallback_eligible(400)


def test_classify_429_eligible():
    assert is_fallback_eligible(429)


def test_classify_500_eligible():
    assert is_fallback_eligible(500)


def test_classify_200_not_eligible():
    assert not is_fallback_eligible(200)


def test_network_error_eligible():
    assert is_fallback_eligible(httpx.ConnectError("test"))


def test_timeout_eligible():
    assert is_fallback_eligible(httpx.TimeoutException("test"))
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/unit/routing/test_errors.py -v
```

- [ ] **Step 3: Implement**

```python
# src/janus/routing/errors.py
from __future__ import annotations

import enum
import httpx


class ErrorType(str, enum.Enum):
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    AUTH_ERROR = "auth_error"
    NETWORK = "network"
    CLIENT_ERROR = "client_error"
    UNKNOWN = "unknown"


def classify_error(status_code: int) -> ErrorType:
    if status_code == 429:
        return ErrorType.RATE_LIMIT
    if status_code >= 500:
        return ErrorType.SERVER_ERROR
    if status_code in (401, 403):
        return ErrorType.AUTH_ERROR
    if status_code >= 400:
        return ErrorType.CLIENT_ERROR
    return ErrorType.UNKNOWN


def is_fallback_eligible(error: int | Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(error, int):
        return error in (429, 401, 403) or error >= 500
    return False
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/unit/routing/test_errors.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/routing/errors.py tests/unit/routing/test_errors.py
git commit -m "feat: error classification and fallback eligibility"
```

---

### Task 5: Update routes.py — fallback retry loop

**Files:**
- Modify: `src/janus/api/routes.py`
- Modify: `src/janus/api/deps.py` (add handler to app state)
- Modify: `src/janus/app.py` (wire combos + handler)
- Modify: `tests/integration/test_api.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_api.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_fallback_on_429():
    """First account 429s, second account succeeds."""
    from janus.providers.registry import ProviderRegistry
    from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="t1", prefix="test", api_type="openai_compat", base_url="https://fake.local/v1", api_key="k1", models=["m1"]))
    reg.register(ProviderConfig(id="t2", prefix="test", api_type="openai_compat", base_url="https://fake2.local/v1", api_key="k2", models=["m1"]))
    cfg = JanusConfig(server=ServerSettings(port=0))
    app = create_app(reg, cfg)

    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post("https://fake2.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "r", "object": "chat.completion", "model": "m1",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_all_providers_exhausted_returns_503():
    from janus.providers.registry import ProviderRegistry
    from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings

    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="t1", prefix="test", api_type="openai_compat", base_url="https://fake.local/v1", api_key="k1", models=["m1"]))
    cfg = JanusConfig(server=ServerSettings(port=0))
    app = create_app(reg, cfg)

    with respx.mock:
        respx.post("https://fake.local/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "down"})
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]}
            r = await client.post("/v1/chat/completions", json=payload)
            assert r.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_combo_expansion():
    """Combo name resolves to ordered model list."""
    from janus.providers.registry import ProviderRegistry
    from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings, ComboConfig

    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="a", prefix="a", api_type="openai_compat", base_url="https://a.local/v1", api_key="k", models=["b"]))
    reg.register_combo(ComboConfig(name="stk", models=["a/b"]))
    cfg = JanusConfig(server=ServerSettings(port=0))
    app = create_app(reg, cfg)

    respx.post("https://a.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "r", "object": "chat.completion", "model": "b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "combo works"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "stk", "messages": [{"role": "user", "content": "hi"}]}
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "combo works"


@pytest.mark.asyncio
async def test_models_lists_combos():
    from janus.providers.registry import ProviderRegistry
    from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings, ComboConfig

    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="a", prefix="a", api_type="openai_compat", base_url="https://a.local/v1", api_key="k", models=["b"]))
    reg.register_combo(ComboConfig(name="stk", models=["a/b"]))
    cfg = JanusConfig(server=ServerSettings(port=0))
    app = create_app(reg, cfg)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert "a/b" in ids
        assert "stk" in ids
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/integration/test_api.py -v -k "fallback or exhausted or combo or lists_combos"
```

- [ ] **Step 3: Implement**

Update `src/janus/api/routes.py` `_handle` function to use FallbackHandler with retry loop. The full implementation:

```python
async def _handle(
    client_format: str,
    body: dict[str, Any],
    request: Request,
) -> Response:
    registry: ProviderRegistry = request.app.state.registry
    handler: FallbackHandler = request.app.state.fallback_handler

    client_adapter = FORMATS[client_format]
    canonical_req = client_adapter.parse_request(body)

    try:
        attempts = handler.resolve_attempts(canonical_req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    last_error = "Unknown error"
    for target in attempts:
        provider_adapter = _resolve_format(target.native_format)
        upstream_payload = provider_adapter.build_upstream_request(canonical_req, target.model)
        provider = _build_provider(target.provider_config)

        try:
            if canonical_req.stream:
                result = await provider.call(upstream_payload, stream=True)
                if result.status_code >= 400:
                    error_type = classify_error(result.status_code)
                    if is_fallback_eligible(result.status_code):
                        retry_after = _extract_retry_after(result)
                        handler.mark_cooldown(target.account_id, error_type.value, retry_after=retry_after)
                        last_error = f"{target.account_id}: {result.status_code}"
                        continue
                    raise HTTPException(status_code=result.status_code, detail=str(result.json_data))
                lines = result.lines
                if lines is None:
                    raise HTTPException(status_code=502, detail="No stream from upstream")
                parser = provider_adapter.stream_parser()
                emitter = client_adapter.stream_emitter()
                generator = translate_stream(lines, parser, emitter)
                return StreamingResponse(generator, media_type="text/event-stream")

            result = await provider.call(upstream_payload, stream=False)
            if result.status_code >= 400:
                error_type = classify_error(result.status_code)
                if is_fallback_eligible(result.status_code):
                    retry_after = _extract_retry_after(result)
                    handler.mark_cooldown(target.account_id, error_type.value, retry_after=retry_after)
                    last_error = f"{target.account_id}: {result.status_code}"
                    continue
                raise HTTPException(
                    status_code=result.status_code,
                    detail=str(result.json_data) if result.json_data else "Upstream error",
                )
            if result.json_data is None:
                raise HTTPException(status_code=502, detail="Empty upstream response")
            canonical_resp = provider_adapter.parse_upstream_response(result.json_data)
            client_payload = client_adapter.emit_response(canonical_resp)
            return JSONResponse(content=client_payload)

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            handler.mark_cooldown(target.account_id, "network")
            last_error = f"{target.account_id}: {type(e).__name__}"
            continue

    raise HTTPException(status_code=503, detail=f"All providers exhausted: {last_error}")


def _extract_retry_after(result: Any) -> float | None:
    """Extract Retry-After from response headers if present."""
    headers = getattr(result, "headers", None)
    if headers:
        val = headers.get("retry-after") or headers.get("Retry-After")
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return None
```

Also update `list_models` to include combos:

```python
@router.get("/models", dependencies=[Depends(require_api_key)])
async def list_models(request: Request) -> dict[str, Any]:
    registry: ProviderRegistry = request.app.state.registry
    data: list[dict[str, Any]] = []
    for prefix, configs in registry.providers.items():
        models_seen: set[str] = set()
        for config in configs:
            for model in config.models:
                if model not in models_seen:
                    models_seen.add(model)
                    data.append({
                        "id": f"{prefix}/{model}",
                        "object": "model",
                        "created": 0,
                        "owned_by": config.id,
                    })
    for combo_name in registry.combos:
        data.append({
            "id": combo_name,
            "object": "model",
            "created": 0,
            "owned_by": "combo",
        })
    return {"object": "list", "data": data}
```

Update the route handlers to pass `request`:

```python
@router.post("/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("openai", body, request)

@router.post("/messages", dependencies=[Depends(require_api_key)])
async def messages(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    return await _handle("anthropic", body, request)
```

Update `src/janus/app.py` to wire FallbackHandler and combos:

```python
from janus.routing.fallback import FallbackHandler

def create_app(
    registry: ProviderRegistry | None = None,
    config: JanusConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0")
    if registry is None:
        registry = ProviderRegistry()
    if config is None:
        config = JanusConfig()
    app.state.registry = registry
    app.state.config = config
    if config.providers:
        for pc in config.providers:
            registry.register(pc)
    if config.combos:
        for combo in config.combos:
            registry.register_combo(combo)
    app.state.fallback_handler = FallbackHandler(registry)
    app.include_router(router, prefix="/v1")
    return app
```

Add necessary imports to routes.py:
```python
from janus.routing.fallback import FallbackHandler
from janus.routing.errors import classify_error, is_fallback_eligible
import httpx
```

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/
```

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/routes.py src/janus/app.py tests/integration/test_api.py
git commit -m "feat: fallback retry loop in routes with combo + multi-account support"
```

---

### Task 6: Update existing tests for registry changes

**Files:**
- Modify: `tests/integration/test_api.py` (fix existing fixtures for multi-account registry)
- Modify: `tests/unit/routing/test_resolver.py` (fix existing Phase 1 tests)

- [ ] **Step 1: Fix Phase 1 tests that assumed single-return lookup**

The old `registry.lookup()` returned `ResolvedTarget | None`. Now it returns `list[ResolvedTarget] | None`. Update all callers.

Key tests to fix:
- `test_resolve_simple` in `test_resolver.py` — `resolve()` now uses FallbackHandler, not direct registry lookup
- Integration test fixtures — `registry.lookup()` now returns a list
- Provider `_build_provider` calls that used `target.provider_config` — still works since ResolvedTarget has it

- [ ] **Step 2: Run all tests and fix failures**

```bash
.venv/bin/python -m pytest tests/ -v --tb=short
```

- [ ] **Step 3: Commit fixes**

```bash
git add tests/
git commit -m "fix: update Phase 1 tests for multi-account registry changes"
```

---

### Task 7: Config loader — handle combos in YAML

**Files:**
- Modify: `tests/unit/config/test_loader.py`

- [ ] **Step 1: Write test**

```python
def test_load_config_with_combos():
    import tempfile, os
    yaml_text = """
server:
  port: 3000
providers:
  - id: glm
    prefix: glm
    api_type: openai_compat
    base_url: https://test.com/v1
    api_key: key
    models: [glm-4.7]
combos:
  - name: stack
    models: [glm/glm-4.7]
"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write(yaml_text)
        path = f.name
    try:
        config = load_config(path)
        assert len(config.combos) == 1
        assert config.combos[0].name == "stack"
        assert config.combos[0].models == ["glm/glm-4.7"]
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test (should pass since loader already resolves all keys)**

```bash
.venv/bin/python -m pytest tests/unit/config/test_loader.py::test_load_config_with_combos -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/unit/config/test_loader.py
git commit -m "test: config loader handles combos"
```

---

### Task 8: Full verification + push

- [ ] **Step 1: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

- [ ] **Step 2: Lint + typecheck**

```bash
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/
```

- [ ] **Step 3: Fix any issues and commit**

```bash
git add -A && git commit -m "fix: Phase 2 lint and type fixes"
```

- [ ] **Step 4: Create branch and push**

```bash
git checkout -b phase2-fallback-combos
git push origin phase2-fallback-combos
gh pr create --title "feat: Phase 2 — Fallback & Combos" --body "..."
```
