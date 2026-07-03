"""Debug multi-turn tool loop against live Janus (localhost). No secrets printed."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
from httpx import AsyncClient

AUTH_PATH = Path.home() / ".local/share/opencode/auth.json"
CONFIG_PATH = Path.home() / ".config/opencode/opencode.jsonc"


def _auth_header() -> dict[str, str]:
    key: str | None = None
    if AUTH_PATH.exists():
        data = json.loads(AUTH_PATH.read_text())
        cred = data.get("janus", {})
        key = cred.get("key") or cred.get("apiKey")
    if not key and CONFIG_PATH.exists():
        raw = CONFIG_PATH.read_text()
        if raw.lstrip().startswith("{"):
            cfg = json.loads(raw)
            key = (cfg.get("provider", {}).get("janus") or {}).get("apiKey")
    if not key:
        raise SystemExit("janus api key not found in opencode auth/config")
    return {"Authorization": f"Bearer {key}"}


async def main() -> None:
    headers = {**_auth_header(), "Content-Type": "application/json"}
    base = "http://127.0.0.1:20128/v1"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run bash",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    ]

    async with AsyncClient(base_url=base, timeout=120) as client:
        r1 = await client.post(
            "/chat/completions",
            headers=headers,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "messages": [{"role": "user", "content": "Use bash to run: echo hello"}],
                "tools": tools,
                "tool_choice": "required",
                "stream": False,
                "max_tokens": 1024,
            },
            timeout=120,
        )
        d1 = r1.json()
        if r1.status_code != 200:
            print("TURN1 FAILED", r1.status_code, d1)
            sys.exit(1)
        msg1 = d1["choices"][0]["message"]
        if not msg1.get("tool_calls"):
            print("TURN1 no tool_calls", json.dumps(msg1)[:500])
            sys.exit(1)
        call_id = msg1["tool_calls"][0]["id"]
        messages = [
            {"role": "user", "content": "Use bash to run: echo hello"},
            {"role": "assistant", "content": msg1.get("content"), "tool_calls": msg1["tool_calls"]},
            {"role": "tool", "tool_call_id": call_id, "content": "hello\n"},
        ]

        r2 = await client.post(
            "/chat/completions",
            headers=headers,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "messages": messages,
                "tools": tools,
                "stream": False,
                "max_tokens": 1024,
            },
            timeout=120,
        )
        d2 = r2.json()
        if r2.status_code != 200:
            print("TURN2 FAILED", r2.status_code, d2)
            sys.exit(1)
        msg2 = d2["choices"][0]["message"]
        print("TURN1", d1["choices"][0].get("finish_reason"), "tool_calls", len(msg1["tool_calls"]))
        print("TURN2", r2.status_code, d2["choices"][0].get("finish_reason"))
        print("TURN2 content", repr((msg2.get("content") or "")[:400]))
        print("TURN2 tool_calls", msg2.get("tool_calls"))
        print("TURN2 usage", d2.get("usage"))

        async with client.stream(
            "POST",
            "/chat/completions",
            headers=headers,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "messages": messages,
                "tools": tools,
                "stream": True,
                "max_tokens": 1024,
            },
            timeout=120,
        ) as r3:
            text = ""
            finish = None
            tool_deltas = 0
            raw_lines = 0
            async for line in r3.aiter_lines():
                raw_lines += 1
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                ch = json.loads(payload)
                c0 = ch.get("choices", [{}])[0]
                delta = c0.get("delta") or {}
                if delta.get("content"):
                    text += delta["content"]
                if c0.get("finish_reason"):
                    finish = c0["finish_reason"]
                tool_deltas += len(delta.get("tool_calls") or [])
        print("STREAM", r3.status_code, finish, repr(text[:400]), "tool_deltas", tool_deltas, "lines", raw_lines)


if __name__ == "__main__":
    asyncio.run(main())
