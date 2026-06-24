# Janus — Phase 4: SQLite Persistence (Design Spec)

**Status:** Approved
**Date:** 2026-06-24
**Builds on:** Phases 1-3

---

## 1. Goal

Add a SQLite database for runtime state: API-key management and usage history. YAML remains the primary config source for providers/combos (declarative, version-controllable). The DB stores what changes at runtime.

## 2. What Gets Stored

| Table | Purpose |
|-------|---------|
| `api_keys` | Generated API keys (hashed), managed via CLI |
| `usage` | Per-request token usage log (provider, model, tokens, status, timestamp) |

Providers and combos stay in YAML — they're declarative config, not runtime state.

## 3. Database

- Location: `~/.janus/janus.db` (respect `data_dir` from config)
- Library: `aiosqlite` (async, non-blocking)
- Auto-created on first run (schema initialization)
- Connection managed per-request (no global connection pool needed — SQLite is in-process)

## 4. API-Key Management

- Format: `sk-janus-{32 hex chars}`
- Stored as SHA256 hash (never plaintext)
- CLI: `janus keys create --name "my-key"`, `janus keys list`, `janus keys revoke --id N`
- Verification: `deps.py` checks BOTH config `api_keys` (static list) AND DB keys
- Key shown once at creation time (only the hash is stored after)

## 5. Usage Recording

After each request completes (success or error), record to `usage` table:
- `timestamp`, `provider_id`, `model`, `input_tokens`, `output_tokens`, `status`, `account_id`
- Async insert, fire-and-forget (failures logged but don't break the request)
- CLI: `janus usage stats` — aggregate totals per provider/model

## 6. Architecture

New package: `src/janus/storage/`

```
src/janus/storage/
├── __init__.py
├── database.py    # init_db, get_db_path, async connection helper
├── api_keys.py    # create_key, list_keys, revoke_key, verify_key
└── usage.py       # record_usage, get_usage_stats
```

Integration points:
- `app.py` — call `init_db()` on startup
- `deps.py` — check DB keys in addition to config keys
- `routes.py` — record usage after each request
- `cli.py` — add `keys` and `usage` command groups

## 7. Out of Scope

- Dashboard UI (Phase 5)
- Cost estimation / pricing (Phase 6)
- Cooldown state persistence (stays in-memory)
- Provider/combo CRUD via API (YAML only for now)
