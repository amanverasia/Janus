# CLAUDE.md

Project guidance for Claude Code and other coding agents working in this repo.

**Read and follow [`AGENTS.md`](AGENTS.md)** — it is the canonical contributor/agent handbook (dev commands, architecture constraints, storage, routing, testing).

## Quick pointers

- Package: `janus-ai` (PyPI) / import `janus` / CLI `janus`
- Python 3.11 + `.venv` — always `.venv/bin/python -m pytest`, never bare `pytest`
- Do not break the formats ↔ providers boundary (both talk only to `canonical/`)
- DB is source of truth after first seed from `~/.janus/config.yaml`
- Design specs: `docs/superpowers/specs/`; living backlog: `todo.md`
- API key scopes: `can_login` + `allowed_models` on DB keys — see AGENTS.md “SQLite storage” and `docs/superpowers/specs/2026-07-09-api-key-scopes-design.md`
