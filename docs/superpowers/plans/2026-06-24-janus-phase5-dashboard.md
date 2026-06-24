# Janus Phase 5: Dashboard UI Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** HTMX + Jinja2 dashboard at `/dashboard` for API key management, provider/combo viewing, and usage stats.

**Architecture:** New `dashboard/` package with templates + management API routes. Served by FastAPI. Tailwind + HTMX via CDN.

**Tech Stack:** Jinja2, HTMX, Tailwind CSS (CDN), FastAPI.

---

### Task 1: Dashboard scaffolding + base template + overview page

**Files:** `src/janus/dashboard/__init__.py`, `src/janus/dashboard/routes.py`, `src/janus/dashboard/templates/base.html`, `src/janus/dashboard/templates/overview.html`, `src/janus/app.py` (modify)

- [ ] **Step 1: Add jinja2 dependency**

Add `"jinja2>=3.1"` to pyproject.toml dependencies. Run `pip install -e ".[dev]"`.

- [ ] **Step 2: Create base template**

```html
<!-- src/janus/dashboard/templates/base.html -->
<!DOCTYPE html>
<html lang="en" class="h-full bg-gray-900">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Janus — {% block title %}Dashboard{% endblock %}</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏛️</text></svg>">
</head>
<body class="h-full">
  <div class="min-h-full flex">
    <!-- Sidebar -->
    <aside class="w-64 bg-gray-800 text-gray-100 flex flex-col">
      <div class="px-6 py-4 border-b border-gray-700">
        <h1 class="text-xl font-bold">🏛️ Janus</h1>
        <p class="text-xs text-gray-400">AI Routing Gateway</p>
      </div>
      <nav class="flex-1 px-2 py-4 space-y-1">
        <a href="/dashboard" class="block px-4 py-2 rounded hover:bg-gray-700 {% block overview_active %}{% endblock %}">📊 Overview</a>
        <a href="/dashboard/providers" class="block px-4 py-2 rounded hover:bg-gray-700 {% block providers_active %}{% endblock %}">🔌 Providers</a>
        <a href="/dashboard/combos" class="block px-4 py-2 rounded hover:bg-gray-700 {% block combos_active %}{% endblock %}">🔀 Combos</a>
        <a href="/dashboard/keys" class="block px-4 py-2 rounded hover:bg-gray-700 {% block keys_active %}{% endblock %}">🔑 API Keys</a>
        <a href="/dashboard/usage" class="block px-4 py-2 rounded hover:bg-gray-700 {% block usage_active %}{% endblock %}">📈 Usage</a>
      </nav>
      <div class="px-6 py-4 border-t border-gray-700 text-xs text-gray-500">
        <p>v0.1.0</p>
        <a href="/v1/health" class="text-gray-400 hover:text-gray-200" target="_blank">Health Check</a>
      </div>
    </aside>

    <!-- Main content -->
    <main class="flex-1 overflow-y-auto bg-gray-900 p-8">
      {% block content %}{% endblock %}
    </main>
  </div>
</body>
</html>
```

- [ ] **Step 3: Create overview template**

```html
<!-- src/janus/dashboard/templates/overview.html -->
{% extends "base.html" %}
{% block title %}Overview{% endblock %}
{% block overview_active %}bg-gray-700{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Overview</h1>

<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Total Requests</p>
    <p class="text-3xl font-bold text-white mt-2">{{ stats.total_requests }}</p>
  </div>
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Input Tokens</p>
    <p class="text-3xl font-bold text-blue-400 mt-2">{{ stats.total_input_tokens }}</p>
  </div>
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Output Tokens</p>
    <p class="text-3xl font-bold text-green-400 mt-2">{{ stats.total_output_tokens }}</p>
  </div>
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Active Providers</p>
    <p class="text-3xl font-bold text-white mt-2">{{ provider_count }}</p>
  </div>
</div>

<div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
  <h2 class="text-lg font-semibold text-white mb-4">Active Combos</h2>
  {% if combos %}
  <ul class="space-y-2">
    {% for name, models in combos.items() %}
    <li class="text-gray-300">
      <span class="font-mono text-sm text-blue-400">{{ name }}</span>
      <span class="text-gray-500">→</span>
      <span class="text-sm">{{ models | join(" → ") }}</span>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="text-gray-500 text-sm">No combos configured.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: Create dashboard routes**

```python
# src/janus/dashboard/routes.py
from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@router.get("", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    app = request.app
    registry = app.state.registry
    db_path = app.state.db_path

    # Get usage stats
    from janus.storage.usage import get_usage_stats
    stats = await get_usage_stats(db_path)

    provider_count = len(registry.providers)

    return templates.TemplateResponse(request, "overview.html", {
        "stats": stats,
        "provider_count": provider_count,
        "combos": dict(registry.combos),
    })


@router.get("/providers", response_class=HTMLResponse)
async def providers(request: Request) -> HTMLResponse:
    registry = request.app.state.registry
    providers_data: list[dict[str, Any]] = []
    for prefix, configs in registry.providers.items():
        models = set()
        for cfg in configs:
            models.update(cfg.models)
        providers_data.append({
            "prefix": prefix,
            "api_type": configs[0].api_type,
            "base_url": configs[0].base_url,
            "accounts": len(configs),
            "models": sorted(models),
        })
    return templates.TemplateResponse(request, "providers.html", {
        "providers": providers_data,
    })


@router.get("/combos", response_class=HTMLResponse)
async def combos_page(request: Request) -> HTMLResponse:
    registry = request.app.state.registry
    return templates.TemplateResponse(request, "combos.html", {
        "combos": dict(registry.combos),
    })
```

- [ ] **Step 5: Register in app.py**

Add to `create_app()` after the `/v1` router:

```python
from janus.dashboard.routes import router as dashboard_router
app.include_router(dashboard_router, prefix="/dashboard")
```

- [ ] **Step 6: Run tests to verify nothing broke, commit**

```bash
.venv/bin/python -m pytest tests/ -v
git add -A && git commit -m "feat: dashboard scaffolding with overview page"
```

---

### Task 2: Providers + Combos pages

**Files:** `templates/providers.html`, `templates/combos.html`

- [ ] **Step 1: Create providers template**

```html
<!-- src/janus/dashboard/templates/providers.html -->
{% extends "base.html" %}
{% block title %}Providers{% endblock %}
{% block providers_active %}bg-gray-700{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Providers</h1>

{% if providers %}
<div class="space-y-4">
  {% for p in providers %}
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <div class="flex items-center justify-between mb-3">
      <div>
        <span class="text-lg font-mono text-blue-400">{{ p.prefix }}/</span>
        <span class="text-sm text-gray-500 ml-2">{{ p.api_type }}</span>
      </div>
      <span class="px-3 py-1 text-xs rounded-full {{ 'bg-green-900 text-green-300' if p.accounts > 0 else 'bg-red-900 text-red-300' }}">
        {{ p.accounts }} account(s)
      </span>
    </div>
    <p class="text-xs text-gray-500 mb-3">{{ p.base_url }}</p>
    <div class="flex flex-wrap gap-2">
      {% for model in p.models %}
      <span class="px-2 py-1 text-xs bg-gray-700 text-gray-300 rounded font-mono">{{ p.prefix }}/{{ model }}</span>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<p class="text-gray-500">No providers configured. Edit <code class="text-gray-400">~/.janus/config.yaml</code> to add providers.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Create combos template**

```html
<!-- src/janus/dashboard/templates/combos.html -->
{% extends "base.html" %}
{% block title %}Combos{% endblock %}
{% block combos_active %}bg-gray-700{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Combos</h1>

{% if combos %}
<div class="space-y-4">
  {% for name, models in combos.items() %}
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <h2 class="text-lg font-mono text-blue-400 mb-3">{{ name }}</h2>
    <div class="flex items-center flex-wrap gap-2">
      {% for model in models %}
      <span class="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded font-mono">{{ model }}</span>
      {% if not loop.last %}
      <span class="text-gray-500">→</span>
      {% endif %}
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<p class="text-gray-500">No combos configured. Edit <code class="text-gray-400">~/.janus/config.yaml</code> to add combos.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Run tests, commit**

---

### Task 3: API Keys management page (HTMX)

**Files:** `templates/keys.html`, add management API routes to `dashboard/routes.py`

- [ ] **Step 1: Add management API routes to routes.py**

```python
from fastapi import Form
from fastapi.responses import JSONResponse


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request) -> HTMLResponse:
    db_path = request.app.state.db_path
    from janus.storage.api_keys import list_keys
    keys = await list_keys(db_path)
    return templates.TemplateResponse(request, "keys.html", {
        "keys": keys,
        "new_key": None,
    })


@router.post("/api/keys")
async def create_key_api(request: Request, name: str = Form(...)) -> HTMLResponse:
    db_path = request.app.state.db_path
    from janus.storage.api_keys import create_key, list_keys
    key, _ = await create_key(db_path, name=name)
    keys = await list_keys(db_path)
    return templates.TemplateResponse(request, "keys.html", {
        "keys": keys,
        "new_key": key,
    })


@router.delete("/api/keys/{key_id}")
async def revoke_key_api(request: Request, key_id: int) -> HTMLResponse:
    db_path = request.app.state.db_path
    from janus.storage.api_keys import revoke_key, list_keys
    await revoke_key(db_path, key_id)
    keys = await list_keys(db_path)
    return templates.TemplateResponse(request, "keys.html", {
        "keys": keys,
        "new_key": None,
    })
```

- [ ] **Step 2: Create keys template**

```html
<!-- src/janus/dashboard/templates/keys.html -->
{% extends "base.html" %}
{% block title %}API Keys{% endblock %}
{% block keys_active %}bg-gray-700{% endblock %}

{% block content %}
<div class="flex items-center justify-between mb-6">
  <h1 class="text-2xl font-bold text-white">API Keys</h1>
</div>

{% if new_key %}
<div class="bg-green-900 border border-green-700 rounded-lg p-4 mb-6">
  <p class="text-green-300 font-semibold mb-1">✅ Key created! Save it now — it won't be shown again.</p>
  <code class="text-green-200 text-sm break-all">{{ new_key }}</code>
</div>
{% endif %}

<div class="bg-gray-800 rounded-lg p-6 border border-gray-700 mb-6">
  <h2 class="text-lg font-semibold text-white mb-3">Create New Key</h2>
  <form hx-post="/dashboard/api/keys" hx-target="#keys-list" hx-swap="innerHTML" class="flex gap-3">
    <input type="text" name="name" placeholder="Key name (e.g. claude-code)"
           class="flex-1 px-4 py-2 bg-gray-700 text-white rounded border border-gray-600 focus:border-blue-500 focus:outline-none">
    <button type="submit" class="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-500 font-medium">
      Generate Key
    </button>
  </form>
</div>

<div id="keys-list">
  <div class="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
    <table class="w-full">
      <thead>
        <tr class="border-b border-gray-700">
          <th class="px-6 py-3 text-left text-xs text-gray-400 uppercase">ID</th>
          <th class="px-6 py-3 text-left text-xs text-gray-400 uppercase">Name</th>
          <th class="px-6 py-3 text-left text-xs text-gray-400 uppercase">Prefix</th>
          <th class="px-6 py-3 text-left text-xs text-gray-400 uppercase">Status</th>
          <th class="px-6 py-3 text-left text-xs text-gray-400 uppercase">Created</th>
          <th class="px-6 py-3"></th>
        </tr>
      </thead>
      <tbody>
        {% for key in keys %}
        <tr class="border-b border-gray-700 hover:bg-gray-750">
          <td class="px-6 py-3 text-gray-400 text-sm">{{ key.id }}</td>
          <td class="px-6 py-3 text-white text-sm">{{ key.name }}</td>
          <td class="px-6 py-3 text-gray-400 text-sm font-mono">{{ key.prefix }}...</td>
          <td class="px-6 py-3">
            {% if key.is_active %}
            <span class="px-2 py-1 text-xs bg-green-900 text-green-300 rounded">Active</span>
            {% else %}
            <span class="px-2 py-1 text-xs bg-red-900 text-red-300 rounded">Revoked</span>
            {% endif %}
          </td>
          <td class="px-6 py-3 text-gray-500 text-sm">{{ key.created_at }}</td>
          <td class="px-6 py-3 text-right">
            {% if key.is_active %}
            <button hx-delete="/dashboard/api/keys/{{ key.id }}" hx-target="#keys-list" hx-swap="innerHTML"
                    hx-confirm="Revoke this key?"
                    class="text-red-400 hover:text-red-300 text-sm">Revoke</button>
            {% endif %}
          </td>
        </tr>
        {% else %}
        <tr>
          <td colspan="6" class="px-6 py-8 text-center text-gray-500">No API keys yet.</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests, commit**

---

### Task 4: Usage stats page

**Files:** `templates/usage.html`, add usage route to routes.py

- [ ] **Step 1: Add usage page route**

```python
@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request) -> HTMLResponse:
    db_path = request.app.state.db_path
    from janus.storage.usage import get_usage_stats
    stats = await get_usage_stats(db_path)
    return templates.TemplateResponse(request, "usage.html", {
        "stats": stats,
    })
```

- [ ] **Step 2: Create usage template**

```html
<!-- src/janus/dashboard/templates/usage.html -->
{% extends "base.html" %}
{% block title %}Usage{% endblock %}
{% block usage_active %}bg-gray-700{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-white mb-6">Usage Statistics</h1>

<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Total Requests</p>
    <p class="text-3xl font-bold text-white mt-2">{{ stats.total_requests }}</p>
  </div>
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Input Tokens</p>
    <p class="text-3xl font-bold text-blue-400 mt-2">{{ stats.total_input_tokens }}</p>
  </div>
  <div class="bg-gray-800 rounded-lg p-6 border border-gray-700">
    <p class="text-sm text-gray-400">Output Tokens</p>
    <p class="text-3xl font-bold text-green-400 mt-2">{{ stats.total_output_tokens }}</p>
  </div>
</div>

<div class="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
  <h2 class="px-6 py-4 text-lg font-semibold text-white border-b border-gray-700">By Model</h2>
  {% if stats.by_model %}
  <table class="w-full">
    <thead>
      <tr class="border-b border-gray-700">
        <th class="px-6 py-3 text-left text-xs text-gray-400 uppercase">Model</th>
        <th class="px-6 py-3 text-right text-xs text-gray-400 uppercase">Requests</th>
        <th class="px-6 py-3 text-right text-xs text-gray-400 uppercase">Input Tokens</th>
        <th class="px-6 py-3 text-right text-xs text-gray-400 uppercase">Output Tokens</th>
      </tr>
    </thead>
    <tbody>
      {% for m in stats.by_model %}
      <tr class="border-b border-gray-700">
        <td class="px-6 py-3 text-white text-sm font-mono">{{ m.model or "unknown" }}</td>
        <td class="px-6 py-3 text-right text-gray-300 text-sm">{{ m.requests }}</td>
        <td class="px-6 py-3 text-right text-blue-400 text-sm">{{ m.input_tokens }}</td>
        <td class="px-6 py-3 text-right text-green-400 text-sm">{{ m.output_tokens }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="px-6 py-8 text-center text-gray-500">No usage data yet.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests, commit**

---

### Task 5: Integration tests + full verification + push

- [ ] **Step 1: Write dashboard integration tests**

```python
# tests/integration/test_dashboard.py
import pytest
from httpx import ASGITransport, AsyncClient
from janus.app import create_app
from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings, ComboConfig
from janus.providers.registry import ProviderRegistry


@pytest.fixture
def app(tmp_path):
    reg = ProviderRegistry()
    reg.register(ProviderConfig(id="t", prefix="t", api_type="openai_compat",
                                base_url="https://test.local/v1", api_key="k", models=["m1"]))
    reg.register_combo(ComboConfig(name="stk", models=["t/m1"]))
    cfg = JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path))
    return create_app(reg, cfg)


@pytest.mark.asyncio
async def test_dashboard_overview(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "Janus" in r.text
        assert "Overview" in r.text


@pytest.mark.asyncio
async def test_dashboard_providers(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/providers")
        assert r.status_code == 200
        assert "t/" in r.text


@pytest.mark.asyncio
async def test_dashboard_combos(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/combos")
        assert r.status_code == 200
        assert "stk" in r.text


@pytest.mark.asyncio
async def test_dashboard_keys(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/keys")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_keys_create(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/dashboard/api/keys", data={"name": "test-key"})
        assert r.status_code == 200
        assert "sk-janus-" in r.text


@pytest.mark.asyncio
async def test_dashboard_usage(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/dashboard/usage")
        assert r.status_code == 200
        assert "Usage" in r.text or "usage" in r.text.lower()
```

- [ ] **Step 2: Run all tests + lint**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/ruff check src/janus/ tests/
.venv/bin/mypy src/janus/
```

- [ ] **Step 3: Push and create PR**
