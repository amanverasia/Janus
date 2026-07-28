# Codex Inventory Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users paste Codex / ChatGPT OAuth credentials (Janus blobs, 9router connection objects, or bare tokens) into Key Inventory, select Codex, validate via OAuth refresh, and route multi-account `codex/<model>` traffic through the existing `CodexProvider` without breaking Providers-page paste.

**Architecture:** Add a small normalize/extract module for Codex credential shapes; extend inventory ingest (length, whitespace, JSON paste parsing) and `validate_key` with a Codex refresh branch; add a `codex` inventory catalog block so provisioning + `expand_gateway_provider` reuse the existing multi-key path. Do not change Codex request transforms or the Providers UI paste path beyond optional shared normalization.

**Tech Stack:** Python 3.11, aiosqlite inventory, httpx (mocked via respx), FastAPI/HTMX inventory dashboard, pytest

**Spec:** [`docs/superpowers/specs/2026-07-28-codex-inventory-design.md`](../specs/2026-07-28-codex-inventory-design.md)

## Global Constraints

- Do **not** break existing Providers-page Codex paste / `CodexProvider` / in-memory `refresh_codex` behavior
- No dashboard PKCE Connect in this pass
- No full 9router backup importer (only connection objects / arrays / `providerConnections`)
- No persist-of-refresh from the live request path back to SQLite (inventory validate may store post-refresh blob on ingest/recheck only)
- Prefer refresh-token validation over Responses probes (avoid ChatGPT “model not supported” false negatives)
- Use `.venv/bin/python -m pytest` for all test commands
- Preserve formats ↔ providers boundary (inventory/catalog only; no new format/provider cross-imports beyond existing `oauth_tokens` / `CodexProvider`)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/janus/inventory/codex_credentials.py` | Normalize 9router/Janus/bare pastes → compact canonical JSON; expand batch pastes into entries |
| `src/janus/catalog.py` | Add `inventory` block under existing `codex` gateway entry |
| `src/janus/inventory/ingestion.py` | Credential-aware length + whitespace; call normalize when provider is `codex` |
| `src/janus/dashboard/inventory_routes.py` | Teach `_parse_bulk_keys` to accept multiline JSON / arrays / `providerConnections` |
| `src/janus/inventory/key_checker.py` | `validate_key` branch for `codex` via `refresh_codex` |
| `src/janus/inventory/url_guard.py` | Safer `mask_key` when value is JSON credential blob |
| `docs/inventory.md` / `docs/providers.md` | Document Codex inventory paste |
| `tests/unit/inventory/test_codex_credentials.py` | Normalize + expand unit tests |
| `tests/unit/inventory/test_codex_ingest_validate.py` | Ingest length/whitespace + mocked validate |
| `tests/integration/test_codex_inventory_routing.py` | Provision + expand multi-account |

---

### Task 1: Codex credential normalize + batch extract

**Files:**
- Create: `src/janus/inventory/codex_credentials.py`
- Create: `tests/unit/inventory/test_codex_credentials.py`

**Interfaces:**
- Produces:
  - `def normalize_codex_credential(raw: str) -> str` — returns compact JSON string or raises `ValueError`
  - `def expand_codex_paste(raw: str) -> list[dict[str, str]]` — each item `{"key": <compact blob or token>, "label": <str>}` (label may be `""`)
  - Canonical blob keys: `access_token`, `refresh_token` (optional), `expires_at` (unix float/int, optional), `extra.workspaceId` (optional), optional `id_token` / `extra.id_token`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/inventory/test_codex_credentials.py
import json

import pytest

from janus.inventory.codex_credentials import expand_codex_paste, normalize_codex_credential


def test_normalize_9router_connection_maps_workspace_and_expiry():
    raw = json.dumps(
        {
            "accessToken": "at-value",
            "refreshToken": "rt-value",
            "expiresAt": "2026-08-04T01:46:50.826Z",
            "idToken": "idt",
            "provider": "codex",
            "name": "user@example.com",
            "email": "user@example.com",
            "providerSpecificData": {
                "chatgptAccountId": "acct-uuid",
                "chatgptPlanType": "plus",
            },
        }
    )
    out = json.loads(normalize_codex_credential(raw))
    assert out["access_token"] == "at-value"
    assert out["refresh_token"] == "rt-value"
    assert out["extra"]["workspaceId"] == "acct-uuid"
    assert isinstance(out["expires_at"], (int, float))
    assert out["expires_at"] > 1_700_000_000


def test_normalize_janus_blob_passthrough_compact():
    raw = '{\n  "access_token": "a",\n  "refresh_token": "r",\n  "extra": {"workspaceId": "w"}\n}'
    out = normalize_codex_credential(raw)
    assert "\n" not in out
    assert json.loads(out)["extra"]["workspaceId"] == "w"


def test_normalize_bare_token():
    assert json.loads(normalize_codex_credential("eyJhbGciOi.bare.token")) == {
        "access_token": "eyJhbGciOi.bare.token"
    }


def test_expand_provider_connections_filters_non_codex():
    raw = json.dumps(
        {
            "providerConnections": [
                {"provider": "nvidia", "accessToken": "nv"},
                {
                    "provider": "codex",
                    "accessToken": "at",
                    "refreshToken": "rt",
                    "name": "c1@x.com",
                    "providerSpecificData": {"chatgptAccountId": "w1"},
                },
            ]
        }
    )
    entries = expand_codex_paste(raw)
    assert len(entries) == 1
    assert entries[0]["label"] == "c1@x.com"
    assert json.loads(entries[0]["key"])["access_token"] == "at"


def test_expand_array_of_connections():
    raw = json.dumps(
        [
            {
                "provider": "codex",
                "accessToken": "a1",
                "refreshToken": "r1",
                "email": "a@b.c",
            },
            {
                "provider": "codex",
                "accessToken": "a2",
                "refreshToken": "r2",
                "name": "n2",
            },
        ]
    )
    entries = expand_codex_paste(raw)
    assert len(entries) == 2
    assert entries[0]["label"] == "a@b.c"
    assert entries[1]["label"] == "n2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_credentials.py -v`

Expected: FAIL with `ModuleNotFoundError` or import error for `janus.inventory.codex_credentials`

- [ ] **Step 3: Implement `codex_credentials.py`**

```python
# src/janus/inventory/codex_credentials.py
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _parse_expires_at(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            pass
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError as exc:
            raise ValueError(f"Invalid expiresAt: {value!r}") from exc
    raise ValueError(f"Invalid expiresAt type: {type(value).__name__}")


def _workspace_id(data: dict[str, Any]) -> str | None:
    extra = data.get("extra")
    if isinstance(extra, dict):
        wid = extra.get("workspaceId") or extra.get("chatgptAccountId")
        if isinstance(wid, str) and wid:
            return wid
    wid = data.get("workspaceId")
    if isinstance(wid, str) and wid:
        return wid
    psd = data.get("providerSpecificData")
    if isinstance(psd, dict):
        wid = psd.get("chatgptAccountId")
        if isinstance(wid, str) and wid:
            return wid
    return None


def _access(data: dict[str, Any]) -> str:
    for key in ("access_token", "accessToken", "token", "api_key"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _refresh(data: dict[str, Any]) -> str:
    for key in ("refresh_token", "refreshToken"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def normalize_codex_credential(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Empty Codex credential")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid Codex credential JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Codex credential JSON must be an object")
        at = _access(data)
        if not at:
            raise ValueError("Codex credential missing access token")
        out: dict[str, Any] = {"access_token": at}
        rt = _refresh(data)
        if rt:
            out["refresh_token"] = rt
        exp = data.get("expires_at", data.get("expiresAt"))
        parsed_exp = _parse_expires_at(exp)
        if parsed_exp is not None:
            out["expires_at"] = parsed_exp
        idt = data.get("id_token") or data.get("idToken")
        if isinstance(idt, str) and idt:
            out["id_token"] = idt
        wid = _workspace_id(data)
        if wid:
            out["extra"] = {"workspaceId": wid}
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    if text.startswith("["):
        raise ValueError("Use expand_codex_paste for arrays")
    return json.dumps({"access_token": text}, separators=(",", ":"), ensure_ascii=False)


def _label_for(conn: dict[str, Any]) -> str:
    for key in ("name", "email"):
        val = conn.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _is_codex_connection(conn: dict[str, Any]) -> bool:
    prov = conn.get("provider")
    if prov is None:
        return True
    return str(prov).lower() in {"codex", "openai-codex", "chatgpt"}


def expand_codex_paste(raw: str) -> list[dict[str, str]]:
    text = raw.strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON paste") from exc
        connections: list[Any]
        if isinstance(data, dict) and isinstance(data.get("providerConnections"), list):
            connections = data["providerConnections"]
        elif isinstance(data, list):
            connections = data
        elif isinstance(data, dict):
            return [
                {
                    "key": normalize_codex_credential(json.dumps(data)),
                    "label": _label_for(data),
                }
            ]
        else:
            raise ValueError("Unsupported Codex paste shape")
        entries: list[dict[str, str]] = []
        for item in connections:
            if not isinstance(item, dict):
                continue
            if not _is_codex_connection(item):
                continue
            if not _access(item) and not _refresh(item):
                continue
            entries.append(
                {
                    "key": normalize_codex_credential(json.dumps(item)),
                    "label": _label_for(item),
                }
            )
        return entries
    return [{"key": normalize_codex_credential(text), "label": ""}]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_credentials.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/inventory/codex_credentials.py tests/unit/inventory/test_codex_credentials.py
git commit -m "$(cat <<'EOF'
feat: normalize Codex/9router credential pastes for inventory

EOF
)"
```

---

### Task 2: Catalog inventory entry for Codex

**Files:**
- Modify: `src/janus/catalog.py` (the `"codex"` entry ~1282–1299)
- Test: add assertion in `tests/unit/inventory/test_codex_credentials.py`

**Interfaces:**
- Produces: `INVENTORY_PROVIDERS["codex"]` via `inventory_entries()` after `seed_inventory_providers`

- [ ] **Step 1: Write failing catalog test**

```python
from janus.inventory.catalog import get_inventory_provider
from janus.catalog import PROVIDERS

def test_codex_has_inventory_and_gateway():
    assert "inventory" in PROVIDERS["codex"]
    inv = get_inventory_provider("codex")
    assert inv is not None
    assert inv["id"] == "codex"
    assert inv["display_name"] == "Codex (ChatGPT)"
    assert inv["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert inv["models_endpoint"] is None
    assert inv["billing_model"] == "subscription"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_credentials.py::test_codex_has_inventory_and_gateway -v`

Expected: FAIL (`inventory` missing)

- [ ] **Step 3: Add inventory block to catalog**

In `src/janus/catalog.py`, under `"codex"`, add (keep existing `gateway` / `capabilities` unchanged):

```python
"codex": {
    "inventory": {
        "id": "codex",
        "name": "codex",
        "display_name": "Codex (ChatGPT)",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "auth_type": "oauth",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
        "key_env_var": None,
        "models_endpoint": None,
        "health_check_endpoint": None,
        "credit_check_endpoint": None,
        "billing_model": "subscription",
        "is_direct": True,
        "routing_note": "Paste a Janus OAuth JSON blob, a 9router Codex connection "
        "object (or providerConnections array), or a bare access token. "
        "Select Codex explicitly. Providers-page paste still works.",
    },
    "gateway": { ... existing ... },
    "capabilities": { ... existing ... },
},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_credentials.py::test_codex_has_inventory_and_gateway -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/catalog.py tests/unit/inventory/test_codex_credentials.py
git commit -m "$(cat <<'EOF'
feat: add Codex inventory catalog entry

EOF
)"
```

---

### Task 3: Ingestion length, whitespace, and Codex normalize

**Files:**
- Modify: `src/janus/inventory/ingestion.py`
- Modify: `src/janus/inventory/url_guard.py` (`mask_key`)
- Create: `tests/unit/inventory/test_codex_ingest_validate.py`

**Interfaces:**
- Consumes: `normalize_codex_credential`
- Produces: `validate_key_value(key, *, provider_id: str | None = None)` allowing up to `INVENTORY_CREDENTIAL_MAX_KEY_LENGTH` (default `16384`) when value looks like JSON credential or `provider_id == "codex"`; `ingest_upstream_key` stores compact normalized blob for Codex

- [ ] **Step 1: Write failing ingest tests**

```python
# tests/unit/inventory/test_codex_ingest_validate.py
import json

import pytest

from janus.inventory.ingestion import KeyIngestEntry, ingest_upstream_key, validate_key_value
from janus.storage.database import init_db, seed_inventory_providers


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_inventory_providers(db_path)
    return db_path


def test_validate_allows_long_codex_json():
    blob = json.dumps({"access_token": "x" * 2000, "refresh_token": "y" * 200})
    assert len(blob) > 512
    assert validate_key_value(blob, provider_id="codex") is None


def test_validate_still_rejects_short_garbage_for_normal_keys():
    assert validate_key_value("short") is not None


@pytest.mark.asyncio
async def test_ingest_preserves_multiline_json_via_normalize(db):
    raw = (
        '{\n  "accessToken": "at-long-enough-value",\n'
        '  "refreshToken": "rt-long-enough-value",\n'
        '  "providerSpecificData": {"chatgptAccountId": "w"}\n}'
    )
    result = await ingest_upstream_key(
        db,
        KeyIngestEntry(key=raw, label="acct"),
        chosen_provider="codex",
    )
    assert result["status"] == "registered"
    from janus.storage.upstream_keys import get_upstream_key  # use real getter name

    row = await get_upstream_key(db, result["id"])
    stored = row["key_value"]
    assert "\n" not in stored
    assert json.loads(stored)["extra"]["workspaceId"] == "w"
```

If `get_upstream_key` does not exist, use the existing getter from `upstream_keys.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_ingest_validate.py -v`

Expected: FAIL on length and/or newline stripping

- [ ] **Step 3: Update `validate_key_value` and `ingest_upstream_key`**

```python
# ingestion.py additions (illustrative)
CREDENTIAL_MAX_KEY_LENGTH = int(os.environ.get("INVENTORY_CREDENTIAL_MAX_KEY_LENGTH", "16384"))

def _looks_like_credential_json(key_value: str) -> bool:
    s = key_value.strip()
    return s.startswith("{") or s.startswith("[")

def validate_key_value(key_value: str, *, provider_id: str | None = None) -> str | None:
    cleaned = key_value.strip()
    if not cleaned:
        return "Key is missing"
    max_len = (
        CREDENTIAL_MAX_KEY_LENGTH
        if (provider_id == "codex" or _looks_like_credential_json(cleaned))
        else MAX_KEY_LENGTH
    )
    if len(cleaned) < MIN_KEY_LENGTH:
        return f"Key too short (min {MIN_KEY_LENGTH} chars)"
    if len(cleaned) > max_len:
        return f"Key too long (max {max_len} chars)"
    if not _looks_like_credential_json(cleaned) and _NON_KEY_PATTERN.match(cleaned):
        return "Does not look like an API key"
    return None
```

In `ingest_upstream_key`:
1. Call `validate_key_value(entry.key, provider_id=...)` with chosen/entry provider when known
2. When provider is `codex`, set `key_value = normalize_codex_credential(entry.key)` (catch `ValueError` → rejected)
3. For non-credential keys, keep existing strip of `\r\n\t`
4. For credential JSON / codex, only `strip()` outer whitespace before normalize

Update `mask_key` in `url_guard.py` to mask inner access/refresh token when value is JSON (add `import json` if missing).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_ingest_validate.py tests/unit/inventory/test_codex_credentials.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/inventory/ingestion.py src/janus/inventory/url_guard.py tests/unit/inventory/test_codex_ingest_validate.py
git commit -m "$(cat <<'EOF'
feat: accept long Codex JSON blobs in inventory ingest

EOF
)"
```

---

### Task 4: Bulk paste parser for multiline JSON

**Files:**
- Modify: `src/janus/dashboard/inventory_routes.py` (`_parse_bulk_keys`)
- Modify: `tests/unit/inventory/test_codex_ingest_validate.py`

**Interfaces:**
- Consumes: `expand_codex_paste`
- Produces: `_parse_bulk_keys(raw) -> list[dict[str, str]]` with `key` / `label` for JSON credential pastes when the whole textarea is JSON

- [ ] **Step 1: Write failing parser test**

```python
from janus.dashboard.inventory_routes import _parse_bulk_keys

def test_parse_bulk_keys_provider_connections():
    raw = """{
      "providerConnections": [
        {"provider": "codex", "accessToken": "atok-long", "refreshToken": "rtok-long", "name": "n1"},
        {"provider": "nvidia", "accessToken": "nv"}
      ]
    }"""
    entries = _parse_bulk_keys(raw)
    assert len(entries) == 1
    assert entries[0]["label"] == "n1"
    assert "access_token" in entries[0]["key"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_ingest_validate.py::test_parse_bulk_keys_provider_connections -v`

Expected: FAIL (line-splitting yields wrong count)

- [ ] **Step 3: Implement JSON-aware `_parse_bulk_keys`**

Prefer `expand_codex_paste` when textarea starts with `{` or `[` and yields entries; else fall back to existing one-key-per-line behavior. Plain API-key line pastes remain unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_ingest_validate.py::test_parse_bulk_keys_provider_connections -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/dashboard/inventory_routes.py tests/unit/inventory/test_codex_ingest_validate.py
git commit -m "$(cat <<'EOF'
feat: parse 9router Codex JSON pastes in inventory add

EOF
)"
```

---

### Task 5: Codex `validate_key` via OAuth refresh

**Files:**
- Modify: `src/janus/inventory/key_checker.py`
- Modify: `tests/unit/inventory/test_codex_ingest_validate.py`

**Interfaces:**
- Consumes: `normalize_codex_credential`; `refresh_codex`, `parse_credential`, `apply_token_response`, `refresh_token`, `serialize_credential`
- Produces: `validate_key(..., provider_id="codex")` → `{is_valid, key_value?, is_usable, ...}`; checker persists rotated `key_value` when present

- [ ] **Step 1: Write failing validate tests with respx mocking `CODEX_TOKEN_URL`**

Cover refresh success (preserves `extra.workspaceId`) and refresh failure → `is_valid=False`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_ingest_validate.py::test_codex_validate_refresh_success -v`

Expected: FAIL

- [ ] **Step 3: Implement `_validate_codex_key` and early branch in `validate_key`**

Refresh when refresh token present; bare access token accepted structurally with `usability_status=unknown`. Do **not** add `codex` to `CHAT_VALIDATED_PROVIDERS`. Wire `check_upstream_key` to persist `result["key_value"]` when returned.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/inventory/test_codex_ingest_validate.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/janus/inventory/key_checker.py tests/unit/inventory/test_codex_ingest_validate.py
git commit -m "$(cat <<'EOF'
feat: validate Codex inventory keys via OAuth refresh

EOF
)"
```

---

### Task 6: Integration — provision + expand routing

**Files:**
- Create: `tests/integration/test_codex_inventory_routing.py`

**Interfaces:**
- Consumes: `ingest_upstream_key`, `ensure_routing_providers`, `expand_gateway_provider`, `list_routable_upstream_keys`

- [ ] **Step 1: Write integration test** ingesting two Codex blobs, marking them routable, ensuring gateway provision, asserting `expand_gateway_provider` returns two `api_type=codex` configs whose `api_key` contains `access_token`

- [ ] **Step 2: Run test (expect FAIL only if prior tasks incomplete)**

Run: `.venv/bin/python -m pytest tests/integration/test_codex_inventory_routing.py -v`

- [ ] **Step 3: Fix provision gaps only if needed**

- [ ] **Step 4: Confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_codex_inventory_routing.py
git commit -m "$(cat <<'EOF'
test: cover Codex inventory multi-account routing expand

EOF
)"
```

---

### Task 7: Docs + regression sweep

**Files:**
- Modify: `docs/inventory.md`
- Modify: `docs/providers.md`

- [ ] **Step 1: Document Codex inventory paste** (Janus blob / 9router connection / `providerConnections`; select Codex; full backup out of scope; Providers paste still works)

- [ ] **Step 2: Run focused + Codex regression tests**

```bash
.venv/bin/python -m pytest \
  tests/unit/inventory/test_codex_credentials.py \
  tests/unit/inventory/test_codex_ingest_validate.py \
  tests/integration/test_codex_inventory_routing.py \
  tests/unit/providers/test_specialized_providers.py \
  tests/unit/providers/test_oauth_tokens.py \
  -v
```

Expected: PASS

- [ ] **Step 3: Ruff check + format on touched files**

- [ ] **Step 4: Commit docs**

```bash
git add docs/inventory.md docs/providers.md
git commit -m "$(cat <<'EOF'
docs: document Codex credentials in Key Inventory

EOF
)"
```

---

## Spec coverage check

| Spec requirement | Task |
|------------------|------|
| Canonical Janus blob + 9router mapping (`chatgptAccountId` → `workspaceId`) | Task 1 |
| Bare token | Task 1 |
| Batch array / `providerConnections` | Task 1 + 4 |
| Catalog inventory for `codex` | Task 2 |
| Length ≥ 16 KiB + newline-safe JSON | Task 3 |
| Normalize on ingest | Task 3 |
| Safer mask for JSON | Task 3 |
| Multiline paste in dashboard | Task 4 |
| Refresh validation; no chat-validator set | Task 5 |
| Store post-refresh blob on validate | Task 5 |
| Expand multi-account routing | Task 6 |
| Preserve Providers paste / executor | Tasks 1–7 (no executor edits) |
| Docs | Task 7 |
| No Connect / no full backup / no request-path persist | Explicit non-goals; no tasks |
