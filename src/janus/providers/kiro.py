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
        stop_reason = "stop"
        offset = 0
        while offset + 16 <= len(raw):
            total = int.from_bytes(raw[offset : offset + 4], "big")
            headers_len = int.from_bytes(raw[offset + 4 : offset + 8], "big")
            if total < 16 or offset + total > len(raw):
                break
            payload_start = offset + 12 + headers_len
            payload_end = offset + total - 4
            try:
                event = json.loads(raw[payload_start:payload_end])
            except (ValueError, json.JSONDecodeError):
                offset += total
                continue
            if isinstance(event, dict):
                content = event.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                if event.get("stopReason"):
                    stop_reason = "stop"
            offset += total
        if not text_parts:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, json.JSONDecodeError):
                pass
        model = self._payload_model(request)
        return {
            "id": f"chatcmpl-{uuid4().hex[:12]}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "".join(text_parts)},
                    "finish_reason": stop_reason,
                }
            ],
        }

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
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)

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
            content = self._message_text(msg.get("content", ""))
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
                converted.append(
                    {"userInputMessage": {"content": content or "continue", "modelId": model}}
                )

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
            try:
                async for chunk in r.aiter_bytes():
                    buffer.extend(chunk)
                    while len(buffer) >= 16:
                        total = int.from_bytes(buffer[:4], "big")
                        headers_len = int.from_bytes(buffer[4:8], "big")
                        if total < 16 or len(buffer) < total:
                            break
                        frame = bytes(buffer[:total])
                        del buffer[:total]
                        start = 12 + headers_len
                        try:
                            event = json.loads(frame[start : total - 4])
                        except (ValueError, json.JSONDecodeError):
                            continue
                        content = event.get("content") if isinstance(event, dict) else None
                        if isinstance(content, str) and content:
                            yield "data: " + json.dumps(
                                {
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"content": content},
                                            "finish_reason": None,
                                        }
                                    ],
                                },
                                separators=(",", ":"),
                            )
                            # RawResult.lines follows httpx.aiter_lines semantics:
                            # an empty item represents the blank line terminating
                            # an SSE event. Without it, OpenAI/Pi joins adjacent
                            # data lines and attempts to parse both JSON objects as
                            # one event.
                            yield ""
            finally:
                await cm.__aexit__(None, None, None)

        return RawResult(status_code=r.status_code, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()
