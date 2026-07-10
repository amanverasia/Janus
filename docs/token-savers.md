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
tools send back from shell commands, file reads, and similar operations. Every
result is ANSI-stripped first, then auto-detected against a priority-ordered
filter set and compressed with the matching filter:

| Priority | Content type | Detection | Compression |
|---|---|---|---|
| 1 | Git log | `commit <sha>` header | Keeps headers/metadata for all commits; collapses commit bodies past the 20th to one line, capped at 200 output lines |
| 2 | Git diff | `diff --git` or `diff ` prefix | Strips diff-mode lines (`index`, `old mode`, `new file mode`, etc.); caps each hunk at 100 shown lines |
| 3 | Git status | Porcelain-shaped lines (`?? path`, `M path`, ...), 3+ of them | Caps modified/untracked file lists at 10 entries per category |
| 4 | Build output | `error[`, `warning:`, `FAILED`, `BUILD` markers | Keeps error/warning/failure lines with 3 lines of context, plus the last 30 lines |
| 5 | Grep output | 5+ `path:NN:` match lines, majority grep-shaped | Caps matches at 10 per file; non-matching lines (context, tracebacks, prose) pass through untouched |
| 6 | Find output | 10+ bare path-like lines, no `:` | Groups by parent directory, caps at 10 entries shown per directory |
| 7 | Tree output | `├──` / `└──` glyphs | Keeps the first 200 lines plus a "+N more lines" summary |
| 8 | File listing | Lines matching `drwxr-xr-x` permissions | Strips permission/user/group prefixes |
| 9 | Log output | >50 lines + timestamp/log-level patterns | Deduplicates lines |

After format-specific compression, RTK applies **smart truncation**:

- Above **250 lines**, truncation is **line-based**: keeps the first **120**
  lines and the last **60**, replacing the middle with a
  `[… N lines truncated …]` marker.
- Otherwise it falls back to **character-based** truncation, capping at 8000
  characters and cutting at the last word boundary after 80% of the limit,
  appended with `[…truncated…]`.

Every filter is a **no-op guard**: if a compression pass would produce output
that's the same size or larger than the input, the original text is kept
instead — RTK never grows content. It also never empties content outright.

Guardrails around the whole pipeline:

- Content under **500 bytes** is skipped — not worth processing.
- Content over **10 MiB** is passed through untouched — too large to be worth
  the CPU cost of scanning.
- `tool_result` parts marked as **errors** are left completely untouched, so
  error messages and tracebacks stay intact for the model to reason about.

Enable via YAML (first startup), dashboard **Token Savers** page, or DB settings:

```yaml
token_savers:
  rtk:
    enabled: true
```

## Caveman

**Default: OFF**

Caveman prepends a brevity-maximizing system prompt that instructs the model to
cut pleasantries and respond with maximum terseness. Three levels are
available (`saver_caveman_level`, dashboard select), each ported from
9router's safety-conscious Caveman prompts:

| Level | Prompt |
|---|---|
| `lite` | Be brief. Skip pleasantries and skip explaining your approach. Keep code, paths, commands, error messages, and URLs exact — never abbreviate them. |
| `full` *(default)* | Respond with maximum brevity. Preserve technical substance. No pleasantries, no explanations of approach, no commentary. Just the answer. Why use many token when few token do trick. *(plus the safety boundaries below)* |
| `ultra` | Max brevity. Drop article, filler, pleasantry. Fragment fine, full sentence not required. No preamble, no commentary. Just answer. Why use many token when few token do trick. *(plus the safety boundaries below)* |

!!! note "Safety boundaries"
    `full` and `ultra` always append the same safety boundary clause: security
    warnings, irreversible-action confirmations, and multi-step instructions
    are always written out normally, and code, paths, commands, error
    messages, and URLs are never abbreviated — brevity never gets to compromise
    correctness or safety.

```yaml
token_savers:
  caveman:
    enabled: true
    level: full   # lite | full | ultra
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

## Savings metrics

The `SaverPipeline` measures each request's size (JSON-serialized `messages` +
`system`) before and after every saver runs, and accumulates the results
per-saver. The **Token Savers** dashboard page (`/dashboard/savers`) shows, under
each saver:

> saved *X* KB across *N* requests (*Y*% avg) — since restart

- Measured **per request** — every request that passes through an enabled
  saver updates that saver's running totals.
- **In-memory, since restart** — counters live on the running `SaverPipeline`
  instance and reset when the process restarts. They are *not* persisted to
  the database.
- Displayed savings are clamped to zero — prompt-injecting savers like
  Caveman and Ponytail can occasionally increase request size (they add a
  system prompt), and those requests aren't shown as negative savings.
- Counters survive a dashboard-triggered saver reload (e.g. toggling a
  different saver on/off rebuilds the pipeline) — the new pipeline adopts the
  old one's cumulative stats rather than resetting to zero.

## Dashboard management

Toggle savers at runtime from `/dashboard/savers`:

- Headroom on/off with proxy URL field
- RTK on/off
- Caveman on/off with level selector (lite / full / ultra)
- Ponytail on/off with level selector (lite / full / ultra)

Settings are stored in the `settings` table and hot-reload immediately. After
first startup, dashboard settings override the YAML `token_savers` section.
