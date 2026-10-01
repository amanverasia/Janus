from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from janus.inventory.antigravity_credentials import normalize_antigravity_credential
from janus.inventory.catalog import get_inventory_provider
from janus.inventory.claude_credentials import normalize_claude_credential
from janus.inventory.cline_credentials import normalize_cline_credential
from janus.inventory.codex_credentials import normalize_codex_credential
from janus.inventory.provider_detection import resolve_provider_for_key
from janus.inventory.url_guard import detect_provider_from_key, is_http_url, mask_key
from janus.inventory.xiaomi_tokenplan import TOKENPLAN_PROVIDER_ID
from janus.storage.upstream_keys import (
    create_upstream_key,
    find_upstream_key_by_value,
    find_upstream_key_by_value_and_provider,
    update_upstream_key,
)

MIN_KEY_LENGTH = int(os.environ.get("INVENTORY_MIN_KEY_LENGTH", "16"))
MAX_KEY_LENGTH = int(os.environ.get("INVENTORY_MAX_KEY_LENGTH", "512"))
CREDENTIAL_MAX_KEY_LENGTH = int(os.environ.get("INVENTORY_CREDENTIAL_MAX_KEY_LENGTH", "16384"))
MAX_SUBMIT_BATCH = int(os.environ.get("INVENTORY_MAX_SUBMIT_BATCH", "200"))
_NON_KEY_PATTERN = re.compile(r"^(https?://|/|\.|\d+$)")


@dataclass
class KeyIngestEntry:
    key: str
    label: str | None = None
    provider: str | None = None
    base_url: str | None = None
    source_node: str | None = None


IngestStatus = Literal["registered", "exists", "rejected", "updated", "skipped", "unidentified"]


def _looks_like_credential_json(key_value: str) -> bool:
    s = key_value.strip()
    return s.startswith("{") or s.startswith("[")


_TOKEN_CREDENTIAL_PROVIDERS = frozenset({"codex", "antigravity", "cline", "claude_oauth"})


def validate_key_value(key_value: str, *, provider_id: str | None = None) -> str | None:
    cleaned = key_value.strip().replace("\r", "")
    if not cleaned:
        return "Key is missing"
    is_credential = (
        provider_id in _TOKEN_CREDENTIAL_PROVIDERS
        or cleaned.startswith("workos:")
        or _looks_like_credential_json(cleaned)
    )
    check_value = cleaned if is_credential else cleaned.replace("\n", "").replace("\t", "")
    max_len = CREDENTIAL_MAX_KEY_LENGTH if is_credential else MAX_KEY_LENGTH
    if len(check_value) < MIN_KEY_LENGTH:
        return f"Key too short (min {MIN_KEY_LENGTH} chars)"
    if len(check_value) > max_len:
        return f"Key too long (max {max_len} chars)"
    if not is_credential and _NON_KEY_PATTERN.match(check_value):
        return "Does not look like an API key"
    return None


def enforce_batch_size(count: int) -> str | None:
    if count > MAX_SUBMIT_BATCH:
        return f"Too many keys ({count}); max {MAX_SUBMIT_BATCH} per request"
    return None


def _chosen_provider_hint(entry: KeyIngestEntry, chosen_provider: str) -> str | None:
    if entry.provider and entry.provider != "auto":
        return entry.provider
    if chosen_provider and chosen_provider != "auto":
        return chosen_provider
    return None


CredentialFormat = Literal[
    "api_key", "codex_auth_json", "antigravity_json", "oauth_json", "unknown"
]
PreviewStatus = Literal["new", "exists", "rejected"]
UNSUPPORTED_FORMAT_ERROR = "Unsupported credential format"

_CODEX_PROVIDER_NAMES = frozenset({"codex", "openai-codex", "chatgpt"})
_OAUTH_FORMAT_BY_PROVIDER: dict[str, CredentialFormat] = {
    "codex": "codex_auth_json",
    "antigravity": "antigravity_json",
    "kiro": "oauth_json",
    "cline": "oauth_json",
    "claude_oauth": "oauth_json",
}


def _json_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [data]
    for key in ("extra", "providerSpecificData"):
        value = data.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _has_any(sources: list[dict[str, Any]], *keys: str) -> bool:
    return any(isinstance(src.get(key), str) and src.get(key) for src in sources for key in keys)


def _codex_cli_tokens(data: dict[str, Any]) -> dict[str, Any] | None:
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str):
        return tokens
    return None


def detect_credential_json_provider(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if _codex_cli_tokens(data) is not None:
        return "codex"
    if isinstance(data.get("claudeAiOauth"), dict):
        return "claude_oauth"
    provider_name = data.get("provider")
    if isinstance(provider_name, str):
        lowered = provider_name.strip().lower()
        if lowered in _CODEX_PROVIDER_NAMES:
            return "codex"
        if lowered in {"antigravity", "kiro", "cline"}:
            return lowered
    sources = _json_sources(data)
    if _has_any(sources, "workspaceId", "chatgptAccountId"):
        return "codex"
    if _has_any(sources, "projectId", "project_id"):
        return "antigravity"
    if _has_any(sources, "profileArn", "authMethod"):
        return "kiro"
    return None


_OPENAI_DISTINCT_PREFIXES = ("sk-proj-", "sk-svcacct-", "sk-admin-")


def prefix_provider_from_key(key_value: str) -> str | None:
    if key_value.startswith("workos:"):
        return "cline"
    guess = detect_provider_from_key(key_value)
    if guess == "openai" and not key_value.startswith(_OPENAI_DISTINCT_PREFIXES):
        return None
    return guess


def detect_credential_format(
    key_value: str, *, provider_hint: str | None = None
) -> tuple[CredentialFormat, str | None]:
    text = key_value.strip()
    if not _looks_like_credential_json(text):
        if provider_hint:
            return "api_key", provider_hint
        return "api_key", prefix_provider_from_key(text)
    if provider_hint:
        return _OAUTH_FORMAT_BY_PROVIDER.get(provider_hint, "unknown"), provider_hint
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "unknown", None
    detected = detect_credential_json_provider(data)
    if detected is None:
        return "unknown", None
    return _OAUTH_FORMAT_BY_PROVIDER[detected], detected


def _flatten_codex_cli_auth(raw_key: str) -> str:
    try:
        data = json.loads(raw_key)
    except json.JSONDecodeError:
        return raw_key
    if not isinstance(data, dict):
        return raw_key
    tokens = _codex_cli_tokens(data)
    if tokens is None:
        return raw_key
    flat: dict[str, Any] = {"access_token": tokens["access_token"]}
    for key in ("refresh_token", "id_token"):
        if isinstance(tokens.get(key), str) and tokens[key]:
            flat[key] = tokens[key]
    account_id = tokens.get("account_id")
    if isinstance(account_id, str) and account_id:
        flat["extra"] = {"workspaceId": account_id}
    return json.dumps(flat)


def _normalize_for_provider(
    raw_key: str, provider_id: str | None
) -> tuple[str, dict[str, Any] | None]:
    if provider_id == "cline":
        return normalize_cline_credential(raw_key)
    return _normalize_key_value(raw_key, provider_id), None


def _normalize_key_value(raw_key: str, provider_id: str | None) -> str:
    if provider_id == "antigravity":
        return normalize_antigravity_credential(raw_key)
    if provider_id == "codex":
        return normalize_codex_credential(_flatten_codex_cli_auth(raw_key))
    if provider_id == "claude_oauth":
        return normalize_claude_credential(raw_key)
    if _looks_like_credential_json(raw_key):
        return raw_key
    return raw_key.replace("\r", "").replace("\n", "").replace("\t", "")


@dataclass
class EntryClassification:
    status: PreviewStatus
    format: CredentialFormat
    key_masked: str
    label: str | None
    provider_id: str | None = None
    provider_display_name: str | None = None
    error: str | None = None
    key_value: str = field(default="", repr=False)
    raw_key: str = field(default="", repr=False)
    base_url: str | None = None
    provider_choice: str | None = None
    detected_provider: str | None = None
    normalized_for: str | None = None
    existing: dict[str, Any] | None = field(default=None, repr=False)
    metadata: dict[str, Any] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "key_masked": self.key_masked,
            "label": self.label,
            "provider_id": self.provider_id,
            "provider_display_name": self.provider_display_name,
            "format": self.format,
            "status": self.status,
            "error": self.error,
        }


def _display_name(provider_id: str | None) -> str | None:
    if provider_id is None:
        return None
    provider = get_inventory_provider(provider_id)
    return str(provider["display_name"]) if provider else provider_id


async def classify_upstream_entry(
    db_path: str | Path,
    entry: KeyIngestEntry,
    chosen_provider: str = "auto",
    *,
    custom_base_url: str | None = None,
) -> EntryClassification:
    provider_hint = _chosen_provider_hint(entry, chosen_provider)
    credential_format, detected = detect_credential_format(entry.key, provider_hint=provider_hint)

    def rejected(error: str, key_masked: str) -> EntryClassification:
        return EntryClassification(
            status="rejected",
            format=credential_format,
            key_masked=key_masked,
            label=entry.label,
            error=error,
        )

    validation_error = validate_key_value(entry.key, provider_id=provider_hint)
    if validation_error:
        return rejected(validation_error, mask_key(entry.key) if entry.key else "?")

    raw_key = entry.key.strip()
    normalized_for = provider_hint or (
        detected if detected in _TOKEN_CREDENTIAL_PROVIDERS else None
    )
    try:
        key_value, metadata = _normalize_for_provider(raw_key, normalized_for)
    except ValueError as exc:
        return rejected(str(exc), mask_key(raw_key))

    key_masked = mask_key(key_value)
    base_url = (entry.base_url or custom_base_url or "").strip() or None
    if base_url and not is_http_url(base_url):
        return rejected("base_url must be a valid http(s) URL", key_masked)

    existing = await find_upstream_key_by_value(db_path, key_value)
    if existing and existing["provider_id"] != "unidentified":
        return EntryClassification(
            status="exists",
            format=credential_format,
            key_masked=existing["key_masked"],
            label=entry.label or existing.get("key_label"),
            provider_id=existing["provider_id"],
            provider_display_name=_display_name(existing["provider_id"]),
            key_value=key_value,
            raw_key=raw_key,
            existing=existing,
        )

    requested = entry.provider or chosen_provider
    provider_choice = requested if requested and requested != "auto" else None
    if provider_choice:
        if get_inventory_provider(provider_choice) is None:
            return rejected(f"Unknown provider: {provider_choice}", key_masked)
    else:
        if _looks_like_credential_json(raw_key) and detected is None:
            return rejected(UNSUPPORTED_FORMAT_ERROR, key_masked)

    provider_id = provider_choice or detected
    return EntryClassification(
        status="new",
        format=credential_format,
        key_masked=key_masked,
        label=entry.label,
        provider_id=provider_id,
        provider_display_name=_display_name(provider_id),
        key_value=key_value,
        raw_key=raw_key,
        base_url=base_url,
        provider_choice=provider_choice,
        detected_provider=detected,
        normalized_for=normalized_for,
        existing=existing,
        metadata=metadata,
    )


async def ingest_upstream_key(
    db_path: str | Path,
    entry: KeyIngestEntry,
    *,
    chosen_provider: str = "auto",
    custom_base_url: str | None = None,
    require_provider: bool = False,
) -> dict[str, Any]:
    classification = await classify_upstream_entry(
        db_path, entry, chosen_provider, custom_base_url=custom_base_url
    )
    if classification.status == "rejected":
        return {
            "key_masked": classification.key_masked,
            "label": entry.label,
            "status": "rejected",
            "error": classification.error,
        }
    existing = classification.existing
    if classification.status == "exists" and existing is not None:
        return {
            "id": existing["id"],
            "key_masked": existing["key_masked"],
            "label": classification.label,
            "provider_id": classification.provider_id,
            "provider_display_name": classification.provider_display_name,
            "status": "exists",
        }

    raw_key = classification.raw_key
    key_value = classification.key_value
    key_masked = classification.key_masked
    base_url = classification.base_url
    provider_hint = classification.normalized_for
    provider_choice = classification.provider_choice
    custom_meta: dict[str, Any] | None
    if provider_choice:
        resolved_provider = provider_choice
        custom_meta = classification.metadata
        if provider_choice == "custom" and base_url:
            custom_meta = {"custom_base_url": base_url.rstrip("/")}
    elif (
        classification.detected_provider
        and classification.detected_provider != TOKENPLAN_PROVIDER_ID
    ):
        resolved_provider = classification.detected_provider
        custom_meta = classification.metadata
    else:
        resolved_provider, custom_meta = await resolve_provider_for_key(
            key_value,
            chosen_provider="auto",
            custom_base_url=base_url,
        )
        if require_provider and resolved_provider in {"unidentified", None}:
            return {
                "key_masked": key_masked,
                "label": entry.label,
                "status": "rejected",
                "error": "Cannot detect provider. Pass provider field.",
            }

    if resolved_provider == "antigravity" and provider_hint != "antigravity":
        try:
            key_value = normalize_antigravity_credential(raw_key)
            key_masked = mask_key(key_value)
        except ValueError as exc:
            return {
                "key_masked": mask_key(raw_key),
                "label": entry.label,
                "status": "rejected",
                "error": str(exc),
            }
    elif resolved_provider == "codex" and provider_hint != "codex":
        try:
            key_value = normalize_codex_credential(_flatten_codex_cli_auth(raw_key))
            key_masked = mask_key(key_value)
        except ValueError as exc:
            return {
                "key_masked": mask_key(raw_key),
                "label": entry.label,
                "status": "rejected",
                "error": str(exc),
            }

    effective_base_url = base_url
    if resolved_provider == "custom":
        effective_base_url = (custom_meta or {}).get("custom_base_url") or base_url
    elif custom_meta and custom_meta.get("custom_base_url"):
        # Token Plan (and similar) region endpoints discovered during detection.
        effective_base_url = str(custom_meta["custom_base_url"]).rstrip("/")
    if resolved_provider == "custom" and not effective_base_url:
        return {
            "key_masked": key_masked,
            "label": entry.label,
            "provider_id": resolved_provider,
            "status": "rejected",
            "error": "Custom provider requires a base URL.",
        }

    is_unidentified = resolved_provider == "unidentified"
    if existing and existing["provider_id"] == "unidentified":
        await update_upstream_key(
            db_path,
            existing["id"],
            {
                "provider_id": resolved_provider,
                "custom_base_url": (
                    effective_base_url
                    if (resolved_provider == "custom" or effective_base_url)
                    else None
                ),
                "metadata": custom_meta,
                "status": "unidentified" if is_unidentified else "pending_validation",
                "is_valid": 0,
                "health_status": "healthy",
                "usability_status": "unknown",
                "usability_note": None,
                "last_error": (
                    "Provider not auto-detected — needs manual review" if is_unidentified else None
                ),
            },
        )
        return {
            "id": existing["id"],
            "key_masked": existing["key_masked"],
            "label": entry.label or existing.get("key_label"),
            "provider_id": resolved_provider,
            "status": "updated" if not is_unidentified else "unidentified",
        }

    duplicate = await find_upstream_key_by_value_and_provider(db_path, key_value, resolved_provider)
    if duplicate:
        provider = get_inventory_provider(resolved_provider)
        return {
            "id": duplicate["id"],
            "key_masked": duplicate["key_masked"],
            "label": entry.label or duplicate.get("key_label"),
            "provider_id": resolved_provider,
            "provider_display_name": provider["display_name"] if provider else resolved_provider,
            "status": "exists",
        }

    # Persist regional base URLs for Token Plan etc., not only custom providers.
    persist_base = (
        effective_base_url if (resolved_provider == "custom" or effective_base_url) else None
    )
    record = await create_upstream_key(
        db_path,
        provider_id=resolved_provider,
        key_value=key_value,
        key_label=entry.label,
        custom_base_url=persist_base,
        source_node=entry.source_node,
        metadata=custom_meta,
    )
    if is_unidentified:
        await update_upstream_key(
            db_path,
            record["id"],
            {
                "status": "unidentified",
                "last_error": "Provider not auto-detected — needs manual review",
            },
        )
    return {
        "id": record["id"],
        "key_masked": record["key_masked"],
        "label": entry.label,
        "provider_id": resolved_provider,
        "status": "unidentified" if is_unidentified else "registered",
    }
