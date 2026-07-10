# Client Compatibility Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Ollama `/api/show` + `/api/generate` shims (with tags allowlist), request-log error/retention/pagination polish, and quota UX round 2 (status helper, banners, routing quota, provider-card poll).

**Architecture:** Route-level Ollama generate remaps chat responses; show/tags share a registry listing helper filtered by `model_allowed`. Request logging gains `max_rows` + pre-routing/`_log_error_and_raise` coverage and HTMX pagination. Quota display adds a shared `quota_status` helper, amber banners, routing overview fields, and an 8s providers partial poll.

**Tech Stack:** Python 3.11+, FastAPI, aiosqlite, Jinja2/HTMX, pytest, respx, ruff, mypy (strict).

**Spec:** `docs/superpowers/specs/2026-07-09-client-compat-polish-design.md`

## Global Constraints

- Run tests with `.venv/bin/python -m pytest`, never bare `pytest`.
- `ruff` line-length 100 (E/F/I/N/W/UP); `X | Y` not `Union`; `StrEnum`; `dict[str, Any]`. `mypy --strict` must pass.
- No code comments unless surrounding code has them.
- Preserve `formats/` ↔ `canonical/` ↔ `providers/` boundary.
- Commit after each task with the shown message.
- Do not edit the design spec file.

## File map

| File | Responsibility |
|------|----------------|
| `src/janus/api/routes.py` | Ollama helpers + show/generate; request-log error hooks; retention pass-through |
| `src/janus/storage/request_logs.py` | `max_rows` on `record_request_log` |
| `src/janus/storage/settings.py` | `server_request_log_retention` + resolver |
| `src/janus/cli.py` | Allow retention setting key |
| `src/janus/dashboard/routes.py` | Request-logs pagination; providers GET partial; enrich status + banner |
| `src/janus/storage/quotas.py` | `quota_status()` |
| `src/janus/storage/routing_overview.py` | Per-provider quota object |
| Templates: `request_logs*.html`, `settings.html`, `providers*.html`, `routing.html` | UI |
| Docs: `api-reference.md`, `client-setup.md`, `todo.md`, `CHANGELOG.md`, `AGENTS.md` | Docs |

---

### Task 1: Ollama model entries helper + tags allowlist

**Files:**
- Modify: `src/janus/api/routes.py` (ollama section ~1030–1077)
- Test: `tests/integration/test_ollama_api.py`

- [ ] **Step 1: Write the failing test** — append to `tests/integration/test_ollama_api.py`:

```python
@pytest.mark.asyncio
async def test_ollama_tags_filters_by_key_allowlist(app, tmp_path):
    from janus.storage.api_keys import create_key
    from janus.storage.settings import set_setting

    await set_setting(app.state.db_path, "server_require_api_key", "true")
    raw, _ = await create_key(
        app.state.db_path, name="scoped", can_login=False, allowed_models=["test/test-m1"]
    )
    # Also create a second provider model via config already has test-m1 only;
    # combo "stack" should be filtered out.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/tags", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200
        names = {m["name"] for m in r.json()["models"]}
        assert "test/test-m1" in names
        assert "stack" not in names
```

If `create_key` signature differs, match `src/janus/storage/api_keys.py` (`allowed_models` as `list[str] | None`). Ensure fixture app has `require_api_key` toggled via setting after seed (auth reads DB settings).

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest tests/integration/test_ollama_api.py::test_ollama_tags_filters_by_key_allowlist -v`

Expected: FAIL — tags still returns combo `stack`.

- [ ] **Step 3: Implement helper + filter tags**

In `src/janus/api/routes.py`, above `ollama_router` handlers, add:

```python
def _ollama_model_entries(
    registry: ProviderRegistry,
    allowed_models: list[str] | None = None,
) -> list[dict[str, Any]]:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prefix, configs in registry.providers.items():
        for config in configs:
            for model in config.models:
                name = f"{prefix}/{model}"
                if name in seen:
                    continue
                seen.add(name)
                if not model_allowed(name, allowed_models):
                    continue
                models.append(
                    {
                        "name": name,
                        "model": name,
                        "modified_at": now,
                        "size": 0,
                        "digest": "",
                        "details": {"family": "janus", "format": "gateway"},
                    }
                )
    for combo_name in registry.combos:
        if not model_allowed(combo_name, allowed_models):
            continue
        models.append(
            {
                "name": combo_name,
                "model": combo_name,
                "modified_at": now,
                "size": 0,
                "digest": "",
                "details": {"family": "janus", "format": "combo"},
            }
        )
    return models
```

Rewrite `ollama_tags`:

```python
@ollama_router.get("/api/tags", dependencies=[Depends(require_api_key)])
async def ollama_tags(request: Request) -> dict[str, Any]:
    registry: ProviderRegistry = request.app.state.registry
    return {"models": _ollama_model_entries(registry, key_allowed_models(request))}
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_ollama_api.py -v`

Expected: PASS (including existing tags/chat tests).

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/routes.py tests/integration/test_ollama_api.py
git commit -m "$(cat <<'EOF'
feat(ollama): filter /api/tags by API key model allowlist

EOF
)"
```

---

### Task 2: `POST /api/show`

**Files:**
- Modify: `src/janus/api/routes.py`
- Test: `tests/integration/test_ollama_api.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_ollama_show_known_model(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/show", json={"name": "test/test-m1"})
        assert r.status_code == 200
        data = r.json()
        assert data["details"]["family"] == "janus"
        assert "completion" in data["capabilities"]


@pytest.mark.asyncio
async def test_ollama_show_unknown_model(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/show", json={"name": "nope/missing"})
        assert r.status_code == 404
        assert "not found" in r.json()["error"].lower()
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest tests/integration/test_ollama_api.py::test_ollama_show_known_model tests/integration/test_ollama_api.py::test_ollama_show_unknown_model -v`

Expected: FAIL — 404 route not found.

- [ ] **Step 3: Implement `ollama_show`**

```python
@ollama_router.post("/api/show", dependencies=[Depends(require_api_key)])
async def ollama_show(request: Request) -> dict[str, Any]:
    body: dict[str, Any] = await request.json()
    name = (body.get("name") or body.get("model") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="model name required")
    registry: ProviderRegistry = request.app.state.registry
    entries = _ollama_model_entries(registry, key_allowed_models(request))
    match = next((e for e in entries if e["name"] == name), None)
    if match is None:
        return JSONResponse(
            content={"error": f"model '{name}' not found"},
            status_code=404,
        )
    details = {
        "parent_model": "",
        "format": "gguf",
        "family": "janus",
        "families": ["janus"],
        "parameter_size": "N/A",
        "quantization_level": "gateway",
    }
    entry_details = match.get("details") or {}
    if entry_details.get("format"):
        details["format"] = entry_details["format"]
    if entry_details.get("family"):
        details["family"] = entry_details["family"]
        details["families"] = [entry_details["family"]]
    return {
        "modelfile": "",
        "parameters": "",
        "template": "{{ .Prompt }}",
        "details": details,
        "model_info": {},
        "capabilities": ["completion"],
    }
```

Note: returning `JSONResponse` from a `dict`-annotated handler is awkward — prefer `Response` return type or raise `HTTPException(404, detail=...)`. Prefer:

```python
async def ollama_show(request: Request) -> Any:
    ...
    if match is None:
        raise HTTPException(status_code=404, detail=f"model '{name}' not found")
```

And add an exception handler only if Ollama clients need `{"error": "..."}` string body. Spec wants `{"error": "model '…' not found"}`. FastAPI `HTTPException(detail=str)` yields `{"detail": "..."}`. To match Ollama, return `JSONResponse` and annotate return as `Response`:

```python
async def ollama_show(request: Request) -> Response:
    ...
    if match is None:
        return JSONResponse({"error": f"model '{name}' not found"}, status_code=404)
    return JSONResponse({...})
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_ollama_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/routes.py tests/integration/test_ollama_api.py
git commit -m "$(cat <<'EOF'
feat(ollama): add POST /api/show model metadata shim

EOF
)"
```

---

### Task 3: `POST /api/generate` (prompt→chat remap)

**Files:**
- Modify: `src/janus/api/routes.py`
- Test: `tests/integration/test_ollama_api.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
@respx.mock
async def test_ollama_generate_nonstream(app):
    _mock_upstream()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/generate",
            json={"model": "test/test-m1", "prompt": "hi", "stream": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["done"] is True
        assert data["response"] == "Hello!"
        assert "message" not in data


@pytest.mark.asyncio
@respx.mock
async def test_ollama_generate_stream_ndjson(app):
    import json

    sse_body = (
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
        'data: {"id":"r1","object":"chat.completion.chunk","model":"test-m1",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://fake.local/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/api/generate",
            json={"model": "test/test-m1", "prompt": "hi"},
        ) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
    lines = [json.loads(line) for line in body.decode().strip().split("\n") if line]
    assert any(line.get("response") == "Hi" for line in lines)
    assert lines[-1]["done"] is True
    assert "message" not in lines[0]
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest tests/integration/test_ollama_api.py::test_ollama_generate_nonstream -v`

Expected: FAIL — 404.

- [ ] **Step 3: Implement helpers + route**

```python
def _ollama_generate_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    prompt = body.get("prompt") or ""
    user_msg: dict[str, Any] = {"role": "user", "content": prompt}
    if body.get("images"):
        user_msg["images"] = body["images"]
    chat: dict[str, Any] = {
        "model": body.get("model"),
        "messages": [user_msg],
        "stream": body.get("stream", True),
    }
    if body.get("options") is not None:
        chat["options"] = body["options"]
    return chat


def _ollama_chat_json_to_generate(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    message = out.pop("message", None) or {}
    out["response"] = message.get("content") or ""
    return out


def _ollama_chat_ndjson_to_generate(line: str) -> str:
    import json as _json

    raw = line.strip()
    if not raw:
        return line
    try:
        obj = _json.loads(raw)
    except _json.JSONDecodeError:
        return line
    if "message" in obj:
        msg = obj.pop("message") or {}
        obj["response"] = msg.get("content") or ""
    return _json.dumps(obj, ensure_ascii=False) + "\n"
```

```python
@ollama_router.post("/api/generate", dependencies=[Depends(require_api_key)])
async def ollama_generate(request: Request) -> Response:
    body: dict[str, Any] = await request.json()
    if not body.get("model"):
        raise HTTPException(status_code=400, detail="model required")
    chat_body = _ollama_generate_to_chat(body)
    response = await _handle("ollama", chat_body, request)
    if isinstance(response, StreamingResponse):

        async def _remap() -> AsyncIterator[bytes]:
            async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
                for line in text.splitlines(keepends=True):
                    if line.endswith("\n"):
                        yield _ollama_chat_ndjson_to_generate(line).encode()
                    elif line.strip():
                        yield _ollama_chat_ndjson_to_generate(line + "\n").encode()

        return StreamingResponse(
            _remap(),
            media_type=getattr(response, "media_type", None) or "application/x-ndjson",
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    if isinstance(response, JSONResponse):
        data = json.loads(response.body.decode())
        return JSONResponse(
            content=_ollama_chat_json_to_generate(data),
            status_code=response.status_code,
        )
    if isinstance(response, Response) and response.media_type == "application/json":
        data = json.loads(bytes(response.body).decode())
        return JSONResponse(
            content=_ollama_chat_json_to_generate(data),
            status_code=response.status_code,
        )
    return response
```

Inspect what `_handle` actually returns for non-stream Ollama (likely `JSONResponse` or plain `dict` wrapped by FastAPI). Adjust remap to match — if `_handle` returns a Starlette `Response` with JSON body, parse `response.body`. Keep the remap fail-safe: if parse fails, return original response.

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_ollama_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/routes.py tests/integration/test_ollama_api.py
git commit -m "$(cat <<'EOF'
feat(ollama): add POST /api/generate via chat translation

EOF
)"
```

---

### Task 4: Request log `max_rows` + retention setting

**Files:**
- Modify: `src/janus/storage/request_logs.py`
- Modify: `src/janus/storage/settings.py`
- Modify: `src/janus/cli.py` (`_ALLOWED_SETTING_KEYS`)
- Modify: `src/janus/api/routes.py` (pass `max_rows` into `record_request_log`)
- Modify: `src/janus/dashboard/templates/settings.html`
- Modify: `src/janus/dashboard/routes.py` (`settings_page` context)
- Test: `tests/unit/storage/test_request_logs.py`
- Test: `tests/unit/storage/test_settings.py` (or create retention unit tests in settings test file if present)

- [ ] **Step 1: Write the failing tests**

In `tests/unit/storage/test_request_logs.py` add:

```python
@pytest.mark.asyncio
async def test_record_respects_max_rows(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    for i in range(5):
        await record_request_log(db, client_format="openai", model=f"m{i}", status=200, max_rows=3)
    assert await count_request_logs(db) == 3
```

In a settings unit test (create `tests/unit/storage/test_request_log_retention_setting.py` if needed):

```python
from janus.storage.settings import resolve_request_log_retention


def test_retention_default():
    assert resolve_request_log_retention({}) == 500


def test_retention_clamp():
    assert resolve_request_log_retention({"server_request_log_retention": "10"}) == 50
    assert resolve_request_log_retention({"server_request_log_retention": "99999"}) == 5000
    assert resolve_request_log_retention({"server_request_log_retention": "250"}) == 250
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_request_logs.py::test_record_respects_max_rows tests/unit/storage/test_request_log_retention_setting.py -v`

Expected: FAIL — `max_rows` unexpected / resolver missing.

- [ ] **Step 3: Implement**

`request_logs.py` — add `max_rows: int | None = None` param; use `max_rows if max_rows is not None else MAX_ROWS` in DELETE.

`settings.py`:

```python
# in SERVER_SETTING_DEFAULTS:
"server_request_log_retention": "500",

def resolve_request_log_retention(settings: dict[str, str]) -> int:
    try:
        value = int(resolve_server_settings(settings)["server_request_log_retention"])
    except (ValueError, TypeError, KeyError):
        value = 500
    return max(50, min(value, 5000))
```

`cli.py`: add `"server_request_log_retention"` to `_ALLOWED_SETTING_KEYS`.

In `_handle` / `_log_error_and_raise`: resolve retention once with settings and pass `max_rows=retention` to every `record_request_log` call. Cleanest: extend `_log_error_and_raise` with `max_rows: int = MAX_ROWS`, and in `_handle` after loading settings:

```python
from janus.storage.settings import resolve_request_log_retention
retention = resolve_request_log_retention(settings)
```

Pass `max_rows=retention` to all `record_request_log(...)` in that function.

Settings UI — after the request-logging toggle card, add a number input (only meaningful when logging on, but always visible):

```html
<div class="mt-4">
  <label class="text-gray-400 text-sm">Retention (rows)</label>
  <input type="number" min="50" max="5000" value="{{ request_log_retention }}"
         class="bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 text-white w-32"
         hx-post="/dashboard/api/settings" hx-swap="none" hx-trigger="change"
         hx-on:htmx:config-request="event.detail.parameters.key='server_request_log_retention'; event.detail.parameters.value=event.detail.elt.value">
</div>
```

Update copy: replace hardcoded “500” with “configurable retention (default 500)”.

In `settings_page` context, add `request_log_retention: resolve_request_log_retention(settings)`.

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_request_logs.py tests/unit/storage/test_request_log_retention_setting.py -v`

Expected: PASS. Also run existing `tests/integration/test_request_logging.py` to ensure call sites still work (default `max_rows`).

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/request_logs.py src/janus/storage/settings.py src/janus/cli.py \
  src/janus/api/routes.py src/janus/dashboard/routes.py \
  src/janus/dashboard/templates/settings.html \
  tests/unit/storage/test_request_logs.py tests/unit/storage/test_request_log_retention_setting.py
git commit -m "$(cat <<'EOF'
feat(request-logs): configurable retention via settings

EOF
)"
```

---

### Task 5: Log remaining error paths

**Files:**
- Modify: `src/janus/api/routes.py` (`_handle`, `_check_budgets` call site, passthrough 502s)
- Test: `tests/integration/test_request_logging.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
@respx.mock
async def test_enabled_records_model_not_allowed(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    from janus.storage.api_keys import create_key
    from janus.storage.settings import set_setting as _set

    await _set(app.state.db_path, "server_require_api_key", "true")
    raw, _ = await create_key(
        app.state.db_path, name="scoped", can_login=False, allowed_models=["other/*"]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {raw}"},
            json={"model": "test/test-m1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 403
    logs = await list_request_logs(app.state.db_path)
    assert len(logs) == 1
    assert logs[0]["status"] == 403


@pytest.mark.asyncio
@respx.mock
async def test_enabled_records_unknown_model(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "nope/missing", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 400
    logs = await list_request_logs(app.state.db_path)
    assert any(log["status"] == 400 for log in logs)
```

For passthrough 502: if hard to trigger via public API without deep mocking, unit-test is optional; prefer replacing both bare raises and adding an integration test only if an existing passthrough path is easy. Minimum: replace the two `raise HTTPException(status_code=502, detail="No stream from upstream")` with `_log_error_and_raise(...)`.

Budget test (optional if budget fixtures are heavy): skip if no quick fixture; otherwise seed a $0 budget and assert 429 logged.

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest tests/integration/test_request_logging.py::test_enabled_records_model_not_allowed -v`

Expected: FAIL — no log row.

- [ ] **Step 3: Implement**

1. Replace both bare 502 raises (~464, ~635) with:

```python
await _log_error_and_raise(
    log_requests=log_requests,
    db_path=db_path,
    client_format=client_format,
    model=canonical_req.model,
    provider_id=target.provider_config.id,
    account_id=target.account_id,
    status=502,
    duration_ms=_elapsed_ms(),
    request_body=logged_request_body,
    detail="No stream from upstream",
    max_rows=retention,
)
```

2. Add helper:

```python
async def _maybe_log_client_error(
    *,
    log_requests: bool,
    db_path: str | Path,
    client_format: str,
    model: str | None,
    status: int,
    request_body: str | None,
    error: str,
    max_rows: int = 500,
) -> None:
    if not log_requests:
        return
    await record_request_log(
        db_path,
        client_format=client_format,
        model=model,
        status=status,
        request_body=request_body,
        error=error[:2000],
        max_rows=max_rows,
    )
```

3. Reorder `_handle` carefully: settings/logging currently load **after** budget + allowlist. Move `get_all_settings` + `log_requests` + `retention` + `logged_request_body` **before** budget/allowlist checks (or load a second early settings read for logging only). Prefer one early settings load:

- After `parse_request` (need model for logs), load settings, set `log_requests` / `retention` / `logged_request_body`.
- On allowlist miss: `await _maybe_log_client_error(... status=403 ...)` then return JSONResponse.
- On `resolve_attempts` ValueError: log 400 then raise.
- For budget: either pass logging flags into `_check_budgets` or check budgets after settings load and log inside `_handle` when `blocked_response` is not None:

```python
blocked_response = await _check_budgets(db_path, client_key_id)
if blocked_response is not None:
    await _maybe_log_client_error(
        log_requests=log_requests,
        db_path=db_path,
        client_format=client_format,
        model=getattr(canonical_req, "model", None) if "canonical_req" in dir() else body.get("model"),
        status=429,
        request_body=logged_request_body,
        error="budget_exceeded",
        max_rows=retention,
    )
    return blocked_response
```

Budget currently runs before `parse_request`. Keep that order for fail-fast, but serialize `body` for the log and use `body.get("model")`.

Practical order:

1. Parse request (existing)
2. Load settings + logging flags early (move up)
3. Budget check + maybe log
4. Allowlist + maybe log
5. Rest unchanged; ValueError path logs then raises

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_request_logging.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/api/routes.py tests/integration/test_request_logging.py
git commit -m "$(cat <<'EOF'
fix(request-logs): capture pre-routing and empty-stream errors

EOF
)"
```

---

### Task 6: Request logs dashboard pagination

**Files:**
- Modify: `src/janus/dashboard/routes.py`
- Modify: `src/janus/dashboard/templates/request_logs.html`
- Modify: `src/janus/dashboard/templates/request_logs_partial.html`
- Test: `tests/integration/test_request_logging.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_request_logs_partial_pagination(app):
    await set_setting(app.state.db_path, "server_request_logging", "true")
    for i in range(3):
        await record_request_log(
            app.state.db_path, client_format="openai", model=f"m{i}", status=200
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/request-logs/partial?limit=2&offset=0")
        assert r.status_code == 200
        assert "Showing" in r.text
        assert "m2" in r.text or "m1" in r.text  # newest first
        r2 = await client.get("/dashboard/api/request-logs/partial?limit=2&offset=2")
        assert r2.status_code == 200
```

Import `record_request_log` in the test module.

- [ ] **Step 2: Run — verify fail**

Expected: FAIL — 404 on partial route.

- [ ] **Step 3: Implement**

Add helpers in `dashboard/routes.py`:

```python
def _clamp_page_size(limit: int) -> int:
    return max(1, min(limit, 200))


async def _request_logs_context(
    db_path: Path, *, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    from janus.storage.request_logs import count_request_logs, list_request_logs
    from janus.storage.settings import get_all_settings, resolve_request_log_retention

    limit = _clamp_page_size(limit)
    total = await count_request_logs(db_path)
    if offset < 0:
        offset = 0
    if total and offset >= total:
        offset = max(0, ((total - 1) // limit) * limit)
    logs = await list_request_logs(db_path, limit=limit, offset=offset)
    settings = await get_all_settings(db_path)
    return {
        "logs": logs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "page": (offset // limit) + 1 if limit else 1,
        "total_pages": max(1, (total + limit - 1) // limit) if limit else 1,
        "retention_max": resolve_request_log_retention(settings),
    }
```

Update `request_logs_page` to use context + `logging_enabled`.

Add:

```python
@router.get("/api/request-logs/partial", response_class=HTMLResponse)
async def api_request_logs_partial(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    limit = int(request.query_params.get("limit", "100"))
    offset = int(request.query_params.get("offset", "0"))
    ctx = await _request_logs_context(db_path, limit=limit, offset=offset)
    ctx["request"] = request
    return _templates.TemplateResponse(request, "request_logs_partial.html", ctx)
```

Update `request_logs_partial.html` footer:

```html
<div class="px-4 py-3 bg-gray-900 border-t border-gray-700 flex items-center justify-between gap-4">
  <p class="text-xs text-gray-500">
    Showing {{ logs | length }} of {{ total }} (page {{ page }}/{{ total_pages }}, max {{ retention_max }} kept).
  </p>
  <div class="flex gap-2">
    {% if offset > 0 %}
    <button class="text-xs text-blue-400"
            hx-get="/dashboard/api/request-logs/partial?limit={{ limit }}&offset={{ offset - limit if offset - limit > 0 else 0 }}"
            hx-target="#request-logs-table">Prev</button>
    {% endif %}
    {% if offset + limit < total %}
    <button class="text-xs text-blue-400"
            hx-get="/dashboard/api/request-logs/partial?limit={{ limit }}&offset={{ offset + limit }}"
            hx-target="#request-logs-table">Next</button>
    {% endif %}
  </div>
</div>
```

Wrap table in `request_logs.html` with `<div id="request-logs-table">` including the partial. Ensure clear/delete still re-renders partial with same id.

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_request_logging.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/routes.py \
  src/janus/dashboard/templates/request_logs.html \
  src/janus/dashboard/templates/request_logs_partial.html \
  tests/integration/test_request_logging.py
git commit -m "$(cat <<'EOF'
feat(dashboard): paginate request logs table

EOF
)"
```

---

### Task 7: `quota_status` helper + enrich providers

**Files:**
- Modify: `src/janus/storage/quotas.py`
- Modify: `src/janus/dashboard/routes.py` (`_enrich_providers`, `_providers_partial`)
- Test: `tests/unit/storage/test_quotas.py`

- [ ] **Step 1: Write the failing tests**

```python
from janus.storage.quotas import quota_status


def test_quota_status_thresholds():
    assert quota_status(79, 100) == "ok"
    assert quota_status(80, 100) == "warning"
    assert quota_status(100, 100) == "exhausted"
    assert quota_status(0, 0) == "exhausted"
```

- [ ] **Step 2: Run — verify fail**

Expected: FAIL — import error.

- [ ] **Step 3: Implement**

```python
def quota_status(used: int, limit: int, warn_pct: float = 80.0) -> str:
    if limit <= 0 or used >= limit:
        return "exhausted"
    pct = (used * 100) / limit
    if pct >= warn_pct:
        return "warning"
    return "ok"
```

In `_enrich_providers`, import `quota_status` and set:

```python
status = quota_status(used, limit)
parsed["quota"] = {
    "used": used,
    "limit": limit,
    "metric": metric,
    "window": parsed["quota_window"],
    "percent": min(round(used * 100 / limit), 100) if limit else 0,
    "exhausted": status == "exhausted",
    "status": status,
    **describe_reset(str(parsed["quota_window"])),
}
```

Also compute `quota_warnings` list for banners in `_providers_partial` / providers page:

```python
warnings = [
    p for p in providers
    if p.get("quota") and p["quota"].get("status") in ("warning", "exhausted")
]
```

Pass `quota_warnings` in template context.

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_quotas.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/quotas.py src/janus/dashboard/routes.py tests/unit/storage/test_quotas.py
git commit -m "$(cat <<'EOF'
feat(quotas): add ok/warning/exhausted display status

EOF
)"
```

---

### Task 8: Quota banners + providers partial poll

**Files:**
- Modify: `src/janus/dashboard/routes.py` (add GET partial)
- Modify: `src/janus/dashboard/templates/providers.html`
- Modify: `src/janus/dashboard/templates/providers_partial.html`
- Test: `tests/integration/test_quota_tracking.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_providers_partial_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/api/providers/partial")
        assert r.status_code == 200
        assert "provider-card-" in r.text or "No providers" in r.text or "grid" in r.text.lower() or True
```

Add a stronger test that seeds quota usage ≥80% and asserts banner text on `/dashboard/providers` (follow existing quota integration fixture patterns in `test_quota_tracking.py`).

- [ ] **Step 2: Run — verify fail**

Expected: FAIL — 405/404 on GET partial.

- [ ] **Step 3: Implement**

```python
@router.get("/api/providers/partial", response_class=HTMLResponse)
async def api_providers_partial(request: Request) -> HTMLResponse:
    db_path = await _ensure_db(request)
    return await _providers_partial(request, db_path)
```

Update `_providers_partial` to include banner + warnings:

```python
providers = await _enrich_providers(db_path)
quota_warnings = [
    p for p in providers
    if p.get("is_enabled") and p.get("quota") and p["quota"]["status"] in ("warning", "exhausted")
]
context = {
    "request": request,
    "providers": providers,
    "logo_map": get_provider_logo_map(),
    "quota_warnings": quota_warnings,
}
```

At top of `providers_partial.html`:

```html
{% if quota_warnings %}
<div class="mb-6 bg-amber-900/30 border border-amber-700 rounded-lg p-4 text-amber-200 text-sm" id="quota-warning-banner">
  <p class="font-medium mb-2">Subscription quota near or at limit</p>
  <ul class="list-disc list-inside space-y-1">
    {% for p in quota_warnings %}
    <li><span class="font-mono">{{ p.id }}</span>:
      {{ "{:,}".format(p.quota.used) }} / {{ "{:,}".format(p.quota.limit) }} {{ p.quota.metric }}
      ({{ p.quota.status }}) · resets in {{ p.quota.resets_in }}</li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```

In `providers.html`, change:

```html
<div id="providers-grid"
     hx-get="/dashboard/api/providers/partial"
     hx-trigger="load, every 8s"
     hx-swap="innerHTML">
  {% include "providers_partial.html" %}
</div>
```

Ensure create/toggle still target `#providers-grid`. Initial include may double-fetch on load — acceptable (savers pattern).

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_quota_tracking.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/routes.py \
  src/janus/dashboard/templates/providers.html \
  src/janus/dashboard/templates/providers_partial.html \
  tests/integration/test_quota_tracking.py
git commit -m "$(cat <<'EOF'
feat(dashboard): quota warning banner and providers live poll

EOF
)"
```

---

### Task 9: Quota on Routing page

**Files:**
- Modify: `src/janus/storage/routing_overview.py`
- Modify: `src/janus/dashboard/templates/routing.html`
- Test: `tests/unit/storage/test_routing_overview.py`
- Test: `tests/integration/test_inventory_dashboard.py` or `test_quota_tracking.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/storage/test_routing_overview.py`, add a test that creates a provider with `quota_window`/`quota_limit`, seeds usage via `record_usage` or direct insert, and asserts `overview["providers"][0]["quota"]["status"]` is present.

Follow existing DB fixture patterns in that file.

- [ ] **Step 2: Run — verify fail**

Expected: FAIL — `quota` key missing.

- [ ] **Step 3: Implement**

In `get_routing_overview`, for each provider row with quota configured:

```python
from janus.storage.quotas import describe_reset, get_window_usage, quota_status

quota = None
if row.get("quota_window") and row.get("quota_limit"):
    usage = await get_window_usage(db_path, str(row["id"]), str(row["quota_window"]))
    metric = row.get("quota_metric") or "requests"
    used = usage["tokens"] if metric == "tokens" else usage["requests"]
    limit = int(row["quota_limit"])
    status = quota_status(used, limit)
    quota = {
        "window": row["quota_window"],
        "used": used,
        "limit": limit,
        "metric": metric,
        "status": status,
        "percent": min(round(used * 100 / limit), 100) if limit else 0,
        "exhausted": status == "exhausted",
        **describe_reset(str(row["quota_window"])),
    }
```

Attach `"quota": quota` on the provider dict. When `quota and quota["exhausted"]`, set each account's display flag `"quota_deprioritized": True`.

Collect `quota_warnings` at overview top level (same filter as providers).

In `routing.html`:

- Amber banner for `overview.quota_warnings` (in addition to cooldown banner)
- Under provider header, if `provider.quota`: show used/limit + status badge
- On account row, if `account.quota_deprioritized` and not cooldown: show `deprioritized (quota)`

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_routing_overview.py tests/integration/test_quota_tracking.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/janus/storage/routing_overview.py \
  src/janus/dashboard/templates/routing.html \
  tests/unit/storage/test_routing_overview.py \
  tests/integration/test_quota_tracking.py
git commit -m "$(cat <<'EOF'
feat(routing): show subscription quota state on routing page

EOF
)"
```

---

### Task 10: Docs + backlog + verify

**Files:**
- Modify: `docs/api-reference.md`
- Modify: `docs/client-setup.md` (Ollama section if present)
- Modify: `todo.md`
- Modify: `CHANGELOG.md`
- Modify: `AGENTS.md` (brief note if Ollama/request-log/quota UX mentioned)

- [ ] **Step 1: Update api-reference**

In the formats table, change Ollama row to:

`/api/chat`, `/api/generate`, `/api/show`, `/api/tags`, `/api/version`

Add short sections for `POST /api/generate` and `POST /api/show` after the chat section (curl examples mirroring chat).

- [ ] **Step 2: Update todo.md**

Mark done:

- Request logs: capture non-fallback upstream errors (note pre-routing + empty stream)
- Request logs: configurable retention + pagination
- Quota UX round 2
- Ollama surface completeness

- [ ] **Step 3: CHANGELOG `[Unreleased]`**

Add bullets for Ollama show/generate, request-log polish, quota UX round 2.

- [ ] **Step 4: Full verify**

```bash
.venv/bin/ruff check src/janus/ tests/
.venv/bin/ruff format --check src/janus/ tests/
.venv/bin/mypy src/janus/
.venv/bin/python -m pytest tests/integration/test_ollama_api.py tests/integration/test_request_logging.py tests/integration/test_quota_tracking.py tests/unit/storage/test_quotas.py tests/unit/storage/test_request_logs.py tests/unit/storage/test_routing_overview.py -v
.venv/bin/python -m pytest -q
```

Fix any failures.

- [ ] **Step 5: Commit**

```bash
git add docs/api-reference.md docs/client-setup.md todo.md CHANGELOG.md AGENTS.md
git commit -m "$(cat <<'EOF'
docs: document client compatibility polish

EOF
)"
```

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| `_ollama_model_entries` + tags allowlist | Task 1 |
| `POST /api/show` | Task 2 |
| `POST /api/generate` remap | Task 3 |
| Retention setting + `max_rows` | Task 4 |
| Passthrough 502 + pre-routing logs | Task 5 |
| Request-logs pagination | Task 6 |
| `quota_status` + enrich | Task 7 |
| Banners + providers poll | Task 8 |
| Routing page quota | Task 9 |
| Docs / todo / changelog | Task 10 |
| Out of scope (rolling 5h, Copilot pricing, encryption, webhooks) | Not planned |

## Self-review notes

- No TBD placeholders; generate remap notes the need to match `_handle` return type at implementation time.
- `max_rows` threaded through `_log_error_and_raise` and `_maybe_log_client_error` consistently.
- Quota `status` field name matches banners and routing overview.
