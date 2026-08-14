"""Kiro (AWS CodeWhisperer) executor.

Ported from 9router ``open-sse/executors/kiro.js`` (auth headers, multi-host,
AWS/social token refresh, native request translation, and AWS EventStream
translation to OpenAI responses).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from .base import RawResult, parse_error_body, parse_retry_after
from .oauth_tokens import (
    access_token,
    apply_token_response,
    needs_refresh,
    parse_credential,
    refresh_kiro_aws,
    refresh_kiro_social,
    refresh_token,
    serialize_credential,
)

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)

DEFAULT_KIRO_BASES = (
    "https://runtime.us-east-1.kiro.dev",
    "https://codewhisperer.us-east-1.amazonaws.com",
    "https://q.us-east-1.amazonaws.com",
)


def _decode_eventstream_headers(raw: bytes) -> dict[str, Any]:
    """Decode AWS EventStream headers (notably ``:event-type``)."""
    headers: dict[str, Any] = {}
    offset = 0
    while offset < len(raw):
        name_len = raw[offset]
        offset += 1
        if offset + name_len + 1 > len(raw):
            raise ValueError("truncated EventStream header")
        name = raw[offset : offset + name_len].decode()
        offset += name_len
        value_type = raw[offset]
        offset += 1
        if value_type in (0, 1):
            value: Any = value_type == 0
        elif value_type == 2:
            value = int.from_bytes(raw[offset : offset + 1], "big", signed=True)
            offset += 1
        elif value_type == 3:
            value = int.from_bytes(raw[offset : offset + 2], "big", signed=True)
            offset += 2
        elif value_type == 4:
            value = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
            offset += 4
        elif value_type in (5, 8):
            value = int.from_bytes(raw[offset : offset + 8], "big", signed=value_type == 5)
            offset += 8
        elif value_type in (6, 7):
            value_len = int.from_bytes(raw[offset : offset + 2], "big")
            offset += 2
            value_raw = raw[offset : offset + value_len]
            offset += value_len
            value = value_raw.decode() if value_type == 7 else value_raw
        elif value_type == 9:
            value = raw[offset : offset + 16]
            offset += 16
        else:
            raise ValueError(f"unsupported EventStream header type {value_type}")
        headers[name] = value
    return headers


def _eventstream_frames(raw: bytes) -> list[tuple[str, Any]]:
    frames: list[tuple[str, Any]] = []
    offset = 0
    while offset + 16 <= len(raw):
        total = int.from_bytes(raw[offset : offset + 4], "big")
        headers_len = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        if total < 16 or headers_len > total - 16 or offset + total > len(raw):
            break
        headers_start = offset + 12
        payload_start = headers_start + headers_len
        try:
            headers = _decode_eventstream_headers(raw[headers_start:payload_start])
            payload_raw = raw[payload_start : offset + total - 4]
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            offset += total
            continue
        if isinstance(payload, (dict, list)):
            event_type = str(headers.get(":event-type") or "")
            # Headerless fixtures/bridges historically exposed content directly.
            if (
                not event_type
                and isinstance(payload, dict)
                and isinstance(payload.get("content"), str)
            ):
                event_type = "assistantResponseEvent"
            frames.append((event_type, payload))
        offset += total
    return frames


def _strip_thinking_tags(content: str, in_thinking: bool) -> tuple[str, bool]:
    if in_thinking:
        end = content.find("</thinking>")
        if end < 0:
            return "", True
        content = content[end + len("</thinking>") :].removeprefix("\n")
        in_thinking = False
    while True:
        start = content.find("<thinking>")
        if start < 0:
            return content, in_thinking
        end = content.find("</thinking>", start + len("<thinking>"))
        if end < 0:
            return content[:start], True
        content = content[:start] + content[end + len("</thinking>") :].removeprefix("\n")


def _reasoning_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text") or value.get("content")
    return None


def _stop_reason(value: Any, *, has_tools: bool = False) -> str:
    normalized = str(value or "").lower().replace("-", "_")
    if normalized in {"tool_use", "tool_calls", "tooluse"} or has_tools:
        return "tool_calls"
    if normalized in {"max_tokens", "length", "token_limit"}:
        return "length"
    if normalized in {"content_filter", "safety"}:
        return "content_filter"
    return "stop"


def _tool_input(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class KiroProvider:
    name = "kiro"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_KIRO_BASES[0],
        *,
        auth_method: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._cred = parse_credential(api_key)
        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        self.auth_method = (
            auth_method
            or (extra.get("authMethod") if isinstance(extra.get("authMethod"), str) else None)
            or self._cred.get("authMethod")
            or "social"
        )
        self.region = (
            region
            if region != "us-east-1" or not isinstance(extra.get("region"), str)
            else str(extra.get("region") or region)
        )
        self.base_url = (base_url or DEFAULT_KIRO_BASES[0]).rstrip("/")
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)

    def credential_blob(self) -> str:
        return serialize_credential(self._cred)

    def _profile_arn(self) -> str:
        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        value = extra.get("profileArn") or self._cred.get("profileArn")
        return str(value) if value else ""

    def _catalog_region(self) -> str:
        profile_arn = self._profile_arn()
        parts = profile_arn.split(":")
        if len(parts) >= 4 and parts[3]:
            return parts[3]
        return self.region

    def _catalog_headers(self) -> dict[str, str]:
        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        seed = (
            extra.get("clientId")
            or self._cred.get("clientId")
            or refresh_token(self._cred)
            or self._profile_arn()
            or access_token(self._cred)
            or "kiro-anonymous"
        )
        machine_id = hashlib.sha256(str(seed).encode()).hexdigest()
        kiro_id = f"KiroIDE-0.10.32-{machine_id}"
        return {
            "Authorization": f"Bearer {access_token(self._cred)}",
            "User-Agent": (
                "aws-sdk-js/1.0.0 ua/2.1 os/windows#10.0.26200 lang/js "
                "md/nodejs#22.21.1 api/codewhispererruntime#1.0.0 m/N,E "
                f"{kiro_id}"
            ),
            "x-amz-user-agent": f"aws-sdk-js/1.0.0 {kiro_id}",
            "x-amzn-kiro-agent-mode": "vibe",
            "x-amzn-codewhisperer-optout": "true",
            "amz-sdk-request": "attempt=1; max=1",
            "amz-sdk-invocation-id": str(uuid4()),
            "Accept": "application/json",
        }

    async def list_models(self) -> tuple[list[str], str | None]:
        """Fetch this account's live Kiro catalog via ListAvailableModels."""
        err = await self._ensure_token()
        if err is not None:
            detail = err.json_data.get("error") if isinstance(err.json_data, dict) else None
            return [], str(detail or "Kiro token refresh failed")
        profile_arn = self._profile_arn()
        params = {"origin": "AI_EDITOR"}
        if profile_arn:
            params["profileArn"] = profile_arn
        url = f"https://q.{self._catalog_region()}.amazonaws.com/ListAvailableModels"
        try:
            response = await self._client.get(url, params=params, headers=self._catalog_headers())
        except httpx.HTTPError as exc:
            return [], f"Kiro catalog request failed: {exc}"
        if response.status_code >= 400:
            detail = parse_error_body(response.content)
            message = detail.get("message") if isinstance(detail, dict) else None
            return (
                [],
                f"Kiro ListAvailableModels returned {response.status_code}: {message or detail}",
            )
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            return [], "Kiro ListAvailableModels returned invalid JSON"
        raw_models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(raw_models, list):
            return [], "Kiro ListAvailableModels response did not contain a models array"
        models: set[str] = set()
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("modelId") or item.get("id")
            if isinstance(model_id, str) and model_id:
                models.add(model_id)
        if not models:
            return [], "Kiro ListAvailableModels returned an empty catalog"
        return sorted(models), None

    def _ordered_bases(self) -> list[str]:
        # A custom OpenAI-compatible bridge is not one of Kiro's alternate
        # service surfaces; never replace it with a native endpoint.
        if self.base_url.endswith("/v1") or "/chat" in self.base_url:
            return [self.base_url]
        bases = [self.base_url, *DEFAULT_KIRO_BASES]
        seen: set[str] = set()
        ordered: list[str] = []
        for base in bases:
            base = base.rstrip("/")
            if base not in seen:
                seen.add(base)
                ordered.append(base)

        # Mirror 9router's auth-aware surface selection. Social/Builder-ID
        # credentials use runtime.kiro.dev. IDC/external-IdP use the AWS
        # surfaces, while API keys specifically need q.* before CodeWhisperer.
        if self.auth_method not in ("api_key", "external_idp", "idc"):
            runtime = [url for url in ordered if "kiro.dev" in url]
            remaining = [url for url in ordered if "kiro.dev" not in url]
            return runtime + remaining

        def regionalize(url: str) -> str:
            if self.region == "us-east-1" or "amazonaws.com" not in url:
                return url
            for service in ("codewhisperer", "q"):
                marker = f"{service}.us-east-1.amazonaws.com"
                if marker in url:
                    return url.replace(marker, f"{service}.{self.region}.amazonaws.com")
            return url

        amazon = [regionalize(url) for url in ordered if "amazonaws.com" in url]
        others = [url for url in ordered if "amazonaws.com" not in url]
        if self.auth_method == "api_key":
            q_urls = [url for url in amazon if "://q." in url]
            amazon = q_urls + [url for url in amazon if "://q." not in url]
        return amazon + others if amazon else ordered

    async def _ensure_token(self) -> RawResult | None:
        if self.auth_method == "api_key":
            return None
        # Imported Kiro exports commonly omit expiresAt. In that case the
        # access token may already be stale; refresh once whenever a refresh
        # token is available, matching 9router's pre-request refresh behavior.
        expires = self._cred.get("expires_at") or self._cred.get("expiresAt")
        should_refresh = needs_refresh(self._cred) or (
            expires is None and bool(refresh_token(self._cred))
        )
        if not should_refresh:
            return None
        rt = refresh_token(self._cred)
        if not rt:
            return None
        async with self._refresh_lock:
            expires = self._cred.get("expires_at") or self._cred.get("expiresAt")
            should_refresh = needs_refresh(self._cred) or (
                expires is None and bool(refresh_token(self._cred))
            )
            if not should_refresh:
                return None
            raw_extra = self._cred.get("extra")
            extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
            client_id = extra.get("clientId") or self._cred.get("clientId")
            client_secret = extra.get("clientSecret") or self._cred.get("clientSecret")
            region = str(extra.get("region") or self._cred.get("region") or self.region)
            if client_id and client_secret:
                tokens = await refresh_kiro_aws(
                    rt,
                    self._client,
                    client_id=str(client_id),
                    client_secret=str(client_secret),
                    region=region,
                )
            else:
                tokens = await refresh_kiro_social(rt, self._client)
            if tokens is None:
                return RawResult(
                    status_code=401,
                    json_data={"error": "Kiro token refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
            if tokens.get("profileArn"):
                extra = (
                    dict(self._cred.get("extra") or {})
                    if isinstance(self._cred.get("extra"), dict)
                    else {}
                )
                extra["profileArn"] = tokens["profileArn"]
                self._cred["extra"] = extra
        return None

    def _headers(self, url: str = "") -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.amazon.eventstream",
            "User-Agent": "AWS-SDK-JS/3.0.0 kiro-ide/1.0.0",
            "X-Amz-User-Agent": "aws-sdk-js/3.0.0 kiro-ide/1.0.0",
            "Amz-Sdk-Request": "attempt=1; max=3",
            "Amz-Sdk-Invocation-Id": str(uuid4()),
        }
        if "://codewhisperer." in url:
            headers["X-Amz-Target"] = (
                "AmazonCodeWhispererStreamingService.GenerateAssistantResponse"
            )
        token = access_token(self._cred)
        if self.auth_method == "api_key":
            headers["Authorization"] = f"Bearer {token}"
            # Match Kiro IDE / 9router exactly. Header names are generally
            # case-insensitive, but upstream examples use this spelling.
            headers["tokentype"] = "API_KEY"
        elif self.auth_method == "external_idp":
            headers["Authorization"] = f"Bearer {token}"
            headers["TokenType"] = "EXTERNAL_IDP"
        else:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _url_for(self, base: str) -> str:
        if base.endswith("/generateAssistantResponse"):
            return base
        if base.endswith("/v1") or "/chat" in base:
            return f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
        return f"{base}/generateAssistantResponse"

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        err = await self._ensure_token()
        if err is not None:
            return err
        bridge_mode = self.base_url.endswith("/v1") or "/chat" in self.base_url
        body = dict(payload) if bridge_mode else self._to_kiro_payload(payload)
        if bridge_mode and stream and "stream" not in body:
            body["stream"] = True
        last: RawResult | None = None
        for base in self._ordered_bases():
            url = self._url_for(base)
            if stream:
                result = await self._call_stream(url, body)
            else:
                r = await self._client.post(url, json=body, headers=self._headers(url))
                if r.status_code >= 400:
                    result = RawResult(
                        status_code=r.status_code,
                        json_data=parse_error_body(r.content),
                        retry_after=parse_retry_after(r.headers),
                    )
                else:
                    data = self._parse_eventstream_response(r.content, body)
                    result = RawResult(status_code=r.status_code, json_data=data)
            last = result
            if result.status_code < 400:
                return result
            # try next host on 401/403/5xx
            if result.status_code not in (401, 403) and result.status_code < 500:
                return result
        return last or RawResult(status_code=502, json_data={"error": "Kiro unavailable"})

    def _parse_eventstream_response(self, raw: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Decode Kiro's AWS EventStream response for OpenAI JSON clients."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tools: dict[str, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        finish_value: Any = None
        in_thinking = False
        frames = _eventstream_frames(raw)
        for event_type, event in frames:
            if event_type == "assistantResponseEvent" and isinstance(event, dict):
                content = event.get("content")
                if isinstance(content, str):
                    content, in_thinking = _strip_thinking_tags(content, in_thinking)
                    if content:
                        text_parts.append(content)
            elif event_type == "codeEvent" and isinstance(event, dict):
                content = event.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
            elif event_type == "reasoningContentEvent" and isinstance(event, dict):
                value = event.get("reasoningContentEvent") or event
                content = _reasoning_text(value)
                if content:
                    reasoning_parts.append(content)
            elif event_type == "toolUseEvent":
                values = event if isinstance(event, list) else [event]
                for index, value in enumerate(values):
                    if not isinstance(value, dict) or not value.get("name"):
                        continue
                    tool_id = str(value.get("toolUseId") or f"call_{len(tools) + index + 1}")
                    tool = tools.setdefault(tool_id, {"name": str(value["name"]), "input": []})
                    fragment = _tool_input(value.get("input"))
                    if fragment:
                        tool["input"].append(fragment)
            elif event_type in {
                "messageStopEvent",
                "metadataEvent",
                "MetadataEvent",
            } and isinstance(event, dict):
                metadata = event.get("metadataEvent") or event.get("metadata") or event
                finish_value = metadata.get("stopReason") or metadata.get("stop_reason")
            elif event_type == "metricsEvent" and isinstance(event, dict):
                metrics = event.get("metricsEvent") or event
                prompt = int(metrics.get("inputTokens") or 0)
                completion = int(metrics.get("outputTokens") or 0)
                usage = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                }
                cache_read = int(
                    metrics.get("cacheReadInputTokens")
                    or metrics.get("cache_read_input_tokens")
                    or 0
                )
                if cache_read:
                    usage["prompt_tokens_details"] = {"cached_tokens": cache_read}

        if not frames:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, json.JSONDecodeError):
                pass
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tools:
            message["tool_calls"] = [
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "arguments": "".join(tool["input"]) or "{}",
                    },
                }
                for tool_id, tool in tools.items()
            ]
        model = self._payload_model(request)
        result: dict[str, Any] = {
            "id": f"chatcmpl-{uuid4().hex[:12]}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": _stop_reason(finish_value, has_tools=bool(tools)),
                }
            ],
        }
        if usage is not None:
            result["usage"] = usage
        return result

    @staticmethod
    def _payload_model(payload: dict[str, Any]) -> str:
        model = payload.get("model")
        if isinstance(model, str) and model:
            return model
        state = payload.get("conversationState")
        if not isinstance(state, dict):
            return ""
        current = state.get("currentMessage")
        if not isinstance(current, dict):
            return ""
        user = current.get("userInputMessage")
        return str(user.get("modelId") or "") if isinstance(user, dict) else ""

    @staticmethod
    def _message_parts(content: Any) -> tuple[str, list[dict[str, Any]]]:
        if isinstance(content, str):
            return content, []
        if not isinstance(content, list):
            return str(content or ""), []
        text_parts: list[str] = []
        images: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
            image_url = item.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if isinstance(url, str) and url.startswith("data:") and "," in url:
                metadata, encoded = url.split(",", 1)
                media_type = metadata[5:].split(";", 1)[0]
                images.append(
                    {
                        "format": (media_type.split("/", 1)[-1] or "png").replace("jpeg", "jpg"),
                        "source": {"bytes": encoded},
                    }
                )
            source = item.get("source")
            if isinstance(source, dict) and source.get("type") == "base64" and source.get("data"):
                media_type = str(source.get("media_type") or "image/png")
                images.append(
                    {
                        "format": (media_type.split("/", 1)[-1] or "png").replace("jpeg", "jpg"),
                        "source": {"bytes": str(source["data"])},
                    }
                )
        return "\n".join(text_parts), images

    @classmethod
    def _message_text(cls, content: Any) -> str:
        return cls._message_parts(content)[0]

    @staticmethod
    def _kiro_tools(tools: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools if isinstance(tools, list) else []:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function") if tool.get("type") == "function" else tool
            if not isinstance(function, dict) or not function.get("name"):
                continue
            converted.append(
                {
                    "toolSpecification": {
                        "name": function["name"],
                        "description": function.get("description") or f"Tool: {function['name']}",
                        "inputSchema": {
                            "json": function.get("parameters")
                            or {"type": "object", "properties": {}}
                        },
                    }
                }
            )
        return converted

    @staticmethod
    def _tool_uses(message: dict[str, Any]) -> list[dict[str, Any]]:
        uses: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments") or "{}"
            try:
                tool_input = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                tool_input = {"raw": arguments}
            uses.append(
                {
                    "toolUseId": call.get("id") or f"call_{uuid4().hex[:12]}",
                    "name": function.get("name") or "",
                    "input": tool_input,
                }
            )
        return uses

    def _to_kiro_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Translate Janus's canonical OpenAI body to Kiro's native envelope."""
        if "conversationState" in payload:
            return dict(payload)
        model = str(payload.get("model") or "claude-sonnet-4")
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for msg in payload.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content, images = self._message_parts(msg.get("content", ""))
            if role in {"system", "developer"}:
                if content:
                    system_parts.append(content)
                continue
            if role == "assistant":
                assistant: dict[str, Any] = {"content": content}
                tool_uses = self._tool_uses(msg)
                if tool_uses:
                    assistant["toolUses"] = tool_uses
                converted.append({"assistantResponseMessage": assistant})
            elif role == "tool":
                tool_result = {
                    "toolUseId": msg.get("tool_call_id") or "",
                    "content": [{"text": content}],
                    "status": "success",
                }
                converted.append(
                    {
                        "userInputMessage": {
                            "content": "",
                            "modelId": model,
                            "userInputMessageContext": {"toolResults": [tool_result]},
                        }
                    }
                )
            else:
                user_message: dict[str, Any] = {
                    "content": content or "continue",
                    "modelId": model,
                }
                if images:
                    user_message["images"] = images
                converted.append({"userInputMessage": user_message})

        current_index = next(
            (i for i in range(len(converted) - 1, -1, -1) if "userInputMessage" in converted[i]),
            None,
        )
        if current_index is None:
            current = {"userInputMessage": {"content": "continue", "modelId": model}}
            history = converted
        else:
            current = converted[current_index]
            history = converted[:current_index] + converted[current_index + 1 :]
        current_user: dict[str, Any] = current["userInputMessage"]
        if system_parts:
            current_user["content"] = "\n\n".join(
                [
                    *system_parts,
                    str(current_user.get("content") or "continue"),
                ]
            )
        raw_context = current_user.get("userInputMessageContext")
        context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
        kiro_tools = self._kiro_tools(payload.get("tools"))
        if kiro_tools:
            context["tools"] = kiro_tools
        if context:
            current_user["userInputMessageContext"] = context
        else:
            current_user.pop("userInputMessageContext", None)

        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        profile = extra.get("profileArn") or self._cred.get("profileArn")
        envelope: dict[str, Any] = {
            "conversationState": {
                "chatTriggerType": "MANUAL",
                "conversationId": str(uuid4()),
                "currentMessage": {
                    "userInputMessage": {
                        **current_user,
                        "modelId": model,
                        "origin": "AI_EDITOR",
                    }
                },
                "history": history,
            },
            "inferenceConfig": {
                "maxTokens": int(
                    payload.get("max_tokens") or payload.get("max_completion_tokens") or 32000
                ),
                **(
                    {"temperature": payload["temperature"]}
                    if payload.get("temperature") is not None
                    else {}
                ),
                **({"topP": payload["top_p"]} if payload.get("top_p") is not None else {}),
            },
        }
        if profile:
            envelope["profileArn"] = profile
        return envelope

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        cm = self._client.stream("POST", url, json=payload, headers=self._headers(url))
        r = await cm.__aenter__()
        if r.status_code >= 400:
            body = await r.aread()
            await cm.__aexit__(None, None, None)
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(body),
                retry_after=parse_retry_after(r.headers),
            )

        content_type = r.headers.get("content-type", "").lower()
        if "event-stream" in content_type or url.endswith("/chat/completions"):

            async def sse_line_iter() -> AsyncIterator[str]:
                try:
                    async for line in r.aiter_lines():
                        yield line
                finally:
                    await cm.__aexit__(None, None, None)

            return RawResult(status_code=r.status_code, lines=sse_line_iter())

        model = self._payload_model(payload)

        async def line_iter() -> AsyncIterator[str]:
            buffer = bytearray()
            response_id = f"chatcmpl-{uuid4().hex[:12]}"
            in_thinking = False
            saw_tools = False
            try:
                async for chunk in r.aiter_bytes():
                    buffer.extend(chunk)
                    while len(buffer) >= 16:
                        total = int.from_bytes(buffer[:4], "big")
                        if total < 16 or len(buffer) < total:
                            break
                        frame = bytes(buffer[:total])
                        del buffer[:total]
                        frames = _eventstream_frames(frame)
                        if not frames:
                            continue
                        event_type, event = frames[0]
                        delta: dict[str, Any] = {}
                        finish_reason: str | None = None
                        usage: dict[str, Any] | None = None
                        if event_type == "assistantResponseEvent" and isinstance(event, dict):
                            content = event.get("content")
                            if isinstance(content, str):
                                content, in_thinking = _strip_thinking_tags(content, in_thinking)
                                if content:
                                    delta["content"] = content
                        elif event_type == "codeEvent" and isinstance(event, dict):
                            content = event.get("content")
                            if isinstance(content, str) and content:
                                delta["content"] = content
                        elif event_type == "reasoningContentEvent" and isinstance(event, dict):
                            value = event.get("reasoningContentEvent") or event
                            content = _reasoning_text(value)
                            if content:
                                delta["reasoning_content"] = content
                        elif event_type == "toolUseEvent":
                            values = event if isinstance(event, list) else [event]
                            tool_calls: list[dict[str, Any]] = []
                            for index, value in enumerate(values):
                                if not isinstance(value, dict) or not value.get("name"):
                                    continue
                                saw_tools = True
                                call_id = str(value.get("toolUseId") or f"call_{uuid4().hex[:12]}")
                                tool_calls.append(
                                    {
                                        "index": index,
                                        "id": call_id,
                                        "type": "function",
                                        "function": {
                                            "name": str(value["name"]),
                                            "arguments": _tool_input(value.get("input")),
                                        },
                                    }
                                )
                            if tool_calls:
                                delta["tool_calls"] = tool_calls
                        elif event_type in {
                            "messageStopEvent",
                            "metadataEvent",
                            "MetadataEvent",
                        } and isinstance(event, dict):
                            metadata = event.get("metadataEvent") or event.get("metadata") or event
                            finish_reason = _stop_reason(
                                metadata.get("stopReason") or metadata.get("stop_reason"),
                                has_tools=saw_tools,
                            )
                        elif event_type == "metricsEvent" and isinstance(event, dict):
                            metrics = event.get("metricsEvent") or event
                            prompt = int(metrics.get("inputTokens") or 0)
                            completion = int(metrics.get("outputTokens") or 0)
                            usage = {
                                "prompt_tokens": prompt,
                                "completion_tokens": completion,
                                "total_tokens": prompt + completion,
                            }
                        if not delta and finish_reason is None and usage is None:
                            continue
                        data: dict[str, Any] = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": delta,
                                    "finish_reason": finish_reason,
                                }
                            ],
                        }
                        if usage is not None:
                            data["usage"] = usage
                        yield "data: " + json.dumps(data, separators=(",", ":"))
                        # Empty line is the SSE event terminator in aiter_lines form.
                        yield ""
            finally:
                await cm.__aexit__(None, None, None)

        return RawResult(status_code=r.status_code, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()
