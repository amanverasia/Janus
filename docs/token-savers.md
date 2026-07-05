# Token Savers

Token savers run on the canonical request after parsing and before routing. Each
saver is a pure `transform(req) -> CanonicalRequest` — it modifies the request
in place to reduce token usage or steer model behavior. Savers are fail-safe:
exceptions are caught and logged, never breaking a request.

The `SaverPipeline` runs enabled savers in sequence:

```
Headroom (external) → RTK → Caveman → Ponytail
```

## RTK

**Default: ON**

RTK compresses `tool_result` content parts — the verbose output that coding
tools send back from shell commands, file reads, and similar operations. It
auto-detects the content type and applies the appropriate compression:

| Content type | Detection | Compression |
|---|---|---|
| Git diff | `diff --git` or `diff ` prefix | Strips diff-mode lines (`index`, `old mode`, `new file mode`, etc.) |
| File listing | Lines matching `drwxr-xr-x` permissions | Strips permission/user/group prefixes |
| Log output | >50 lines + timestamp/log-level patterns | Deduplicates lines |

After format-specific compression, RTK applies:

1. **ANSI stripping** — removes terminal escape sequences.
2. **Smart truncation** — caps at 8000 characters, cutting at the last word
   boundary after 80% of the limit, and appends `[…truncated…]`.

Content under 50 characters is skipped entirely (not worth processing).

Enable via YAML (first startup), dashboard **Token Savers** page, or DB settings:

```yaml
token_savers:
  rtk:
    enabled: true
```

## Caveman

**Default: OFF**

Caveman prepends a brevity-maximizing system prompt that instructs the model to
cut pleasantries and respond with maximum terseness:

> Respond with maximum brevity. Preserve technical substance. No pleasantries,
> no explanations of approach, no commentary. Just the answer. Why use many
> token when few token do trick.

```yaml
token_savers:
  caveman:
    enabled: true
```

## Ponytail

**Default: OFF**

Ponytail prepends a "lazy developer" system prompt that steers the model toward
minimal, dependency-light code. Three levels are available:

| Level | Prompt |
|---|---|
| `lite` | Build what's asked. Prefer stdlib over new dependencies. Name the lazier alternative. Minimal diff. |
| `full` *(default)* | Be a lazy senior developer. Deletion over addition. stdlib over new deps. One-liner over abstraction. Minimal code, minimal diff. Never add code that isn't requested. |
| `ultra` | YAGNI extremist. Deletion first. Ship the one-liner. Challenge unnecessary requirements in your response. The best code is no code. The second best is a one-liner. stdlib > native > existing deps > one-liner > minimal code. |

```yaml
token_savers:
  ponytail:
    enabled: true
    level: full   # lite | full | ultra
```

## Headroom

**Default: OFF** — requires a separately running [Headroom](https://github.com/chopratejas/headroom) proxy.

Headroom is an external context-compression service. When enabled, Janus sends
the conversation to Headroom's `POST /v1/compress` endpoint before any other
saver runs, then continues normal routing with the compressed messages:

```
Client → Janus → Headroom /v1/compress → Janus → provider
```

Local setup:

```bash
pip install "headroom-ai[proxy]"
headroom proxy --port 8787
```

Enable in Dashboard → Token Savers → Headroom. The URL is configurable
(default `http://localhost:8787`) — for Docker use `http://headroom:8787`
(same network) or `http://host.docker.internal:8787` (host machine).

**Fail-open:** if Headroom is down, times out, or returns an error or malformed
response, Janus sends the original uncompressed request. Headroom can never
break a request.

## All savers together

Savers stack — all enabled savers run in pipeline order. A full configuration:

```yaml
token_savers:
  rtk:
    enabled: true
  caveman:
    enabled: true
  ponytail:
    enabled: true
    level: ultra
```

With this config, a request's `tool_result` content is compressed (RTK), then a
brevity prompt is prepended (Caveman), then a lazy-dev prompt is prepended
(Ponytail). Each step is independent and fail-safe.

## Pipeline behavior

The `SaverPipeline` wraps each saver in a `try/except`. If any saver raises an
exception:

- The error is **logged** at `WARNING` level.
- The request continues with whatever state it was in.
- **No request is ever rejected** due to a saver failure.

This means you can safely enable savers without worrying about edge cases in
tool output crashing your request pipeline.

## Dashboard management

Toggle savers at runtime from `/dashboard/savers`:

- Headroom on/off with proxy URL field
- RTK on/off
- Caveman on/off
- Ponytail on/off with level selector (lite / full / ultra)

Settings are stored in the `settings` table and hot-reload immediately. After
first startup, dashboard settings override the YAML `token_savers` section.
