# Optimize

Optimize is SLM's way of spending fewer tokens on the model calls your agents already make. It does two things: it caches results so a repeat is not paid for twice, and it compresses large text before it goes into a context window. It comes in three forms. A local proxy sits between a client and its model provider and applies both to every call. Five proxy-free MCP tools let an agent compress and cache selected content itself. And two skills tell an agent when to use those tools. Everything Optimize stores lives in its own encrypted file, `llmcache.db`, and never touches your memories. The only traffic that leaves the computer is the proxy forwarding your own requests to the provider you already use.

## Three surfaces

| Surface | How you use it | Proxy needed | What it can cache or compress |
|---|---|---|---|
| Proxy | Point a client at `http://127.0.0.1:8765` (`slm wrap`, `slm proxy`, or a base-URL setting) | Yes | Whole model calls: exact-match response cache and lossless compression of the request |
| MCP tools | `slm_compress`, `slm_retrieve`, `slm_cache_set`, `slm_cache_get`, `slm_optimize_stats` | No | Only content the agent chooses to route through them |
| Skills | `slm-cache` and `slm-compress`, plus the `slm-optimize-advisor` agent, shipped in the host plugins | No | Whatever the agent decides to route, if it follows the skill |

The split exists because of one hard limit. A client's own conversation turn can only be cached by something sitting on its network path, and that is the proxy. The tools and skills cannot cache the turn itself; they cache results the agent hands them (a file read, a search, a long tool output). The code comment on the tools adds the reason people choose them anyway: they do not change `ANTHROPIC_BASE_URL`, so the full 1M-token context option for Claude stays available, which a proxy base URL removes.

## The proxy

The proxy is not a separate process. It is a set of routes the SLM daemon mounts on its own port, 8765, when the daemon starts with `proxy_enabled` true. It serves the Anthropic Messages API (`/v1/messages`), OpenAI chat completions (`/v1/chat/completions`), Gemini native and OpenAI-compatible routes (`/v1beta/...`), and a Vertex surface. For each call it checks the cache, compresses on a miss, forwards to the provider, and stores the answer. If one of its own hooks fails it forwards the request unchanged rather than failing the call. Request bodies are capped at 10 MB.

Turning it on has one non-obvious step. The proxy routes are mounted only when the daemon boots, so after you enable the proxy you must run `slm restart`. `slm proxy` sets `proxy_enabled` and prints "Proxy ready" as soon as the daemon answers its health check, which is true even before the routes exist; if `/v1/messages` returns 404, restart. The master switch is a different thing: `slm optimize on` sets `enabled`, cache and compress, but does not set `proxy_enabled`, and the master `enabled` flag gates only the in-process SDK wrapper `withSLM(...)`. The proxy ignores it.

### Who may use the proxy

The proxy is on the daemon's port, so it guards itself. Every request must carry the provider credential a real client sends (`x-api-key` or a bearer token for the Anthropic routes, `x-goog-api-key` or a bearer token for the Gemini ones, a bearer token elsewhere); without one it answers 401 "provider credential required". A request that carries a browser `Origin` other than the daemon's own dashboard origins is refused with 403 "cross-origin request rejected", so a web page cannot use your proxy. When the proxy forwards a call to the provider it sends only the headers each provider needs, never your dashboard cookies or SLM's own keys, and the request body goes through the same outbound gate as every other call that leaves the computer, so credential-shaped text in it is replaced by a bare `redacted` marker before it reaches a non-local provider ([Privacy and Data Flow](Privacy-and-Data-Flow)).

### Pointing a client at it

`slm wrap <agent>` is a registry of how each agent accepts a base URL. Run `slm wrap --list` for the table.

| Target | What `slm wrap` does |
|---|---|
| `claude` | Launches Claude Code with `ANTHROPIC_BASE_URL` set to the proxy |
| `claude-settings` | Writes that variable into `~/.claude/settings.json`, so every Claude Code session uses the proxy |
| `codex` | Launches Codex with `OPENAI_BASE_URL` set |
| `aider` | Launches Aider with both OpenAI and Anthropic base URLs set |
| `cline` | Edits VS Code user settings (`cline.openAiApiBase`) |
| `antigravity`, `opencode`, `cursor`, `copilot`, `generic` | Print instructions only; you set the URL yourself |

`--dry-run` previews the action. The `--persistent` flag is accepted but the wrap code never reads it, so to make Claude Code use the proxy permanently run `slm wrap claude-settings` instead. For SDKs, set the base URL (`http://127.0.0.1:8765` for Anthropic clients, `http://127.0.0.1:8765/v1` for OpenAI-compatible ones) or wrap the client in code with `from superlocalmemory.optimize.adapters import withSLM; client = withSLM(OpenAI())`. `docs/proxy-setup.md` has per-client recipes. Windsurf is listed there with `slm wrap windsurf`, but the registry has no such target; set its base URL by hand.

One practical limit: a coding agent that signs in through a subscription's OAuth cannot be pointed at a local proxy, so the proxy is aimed at API-key and SDK traffic you control.

### What it does to a request

The cache is an exact match: the key hashes the model, system prompt, messages and parameters, so a hit is byte-for-byte the same call, with no wrong-answer risk. Responses with tool-use blocks are cached too and replayed as a proper event stream. Entries are scoped to a tenant derived from the profile, the provider and the caller's own API key, so two keys, or two profiles sharing one key, never see each other's answers. A request with no credential is not cached at all rather than falling into a shared bucket.

Compression on the live proxy is lossless only: whitespace is normalised and JSON is minified when the result round-trips to the same value, and code is never touched. The most recent four messages are protected (`compress_protect_recent`). A CacheAligner step also scans the system prompt for volatile tokens such as timestamps and logs a warning when it finds them, because those break provider-side prefix caching; it only reports and never rewrites.

The lossy mode, LLMLingua-2 prose compression, is deliberately kept off the live proxy path so real traffic is never lossily rewritten. It is available through `slm_compress` (below), needs `compress_mode` set to aggressive and `compress_prose` on, and is why every lossy result carries a recoverable original.

A semantic cache (fuzzy matching of similar questions) exists but is off by default, because a loose match can return the answer to a different question. Treat it as experimental and measure your own false-hit rate before turning it on.

## The proxy-free MCP tools

Five tools, all in the `core`, `code`, `full` and `power` MCP profiles ([MCP Tools](MCP-Tools)):

| Tool | What it does |
|---|---|
| `slm_compress(content, mode, reversible, ttl_seconds)` | Compresses up to 1,000,000 bytes. `mode` is `normalize` (always lossless), `auto` or `aggressive`. If the result is lossy and `reversible` is true it returns a `ccr_id` |
| `slm_retrieve(ccr_id)` | Returns the exact original for a `ccr_id` (a UUID4). Fails cleanly if it expired or belongs to another tenant |
| `slm_cache_set(key, value, ttl_seconds)` | Stores a string (key up to 512 characters, value up to 1,000,000 bytes, default lifetime 24 h) |
| `slm_cache_get(key)` | Returns `hit: true` with the value, or `hit: false`; never raises |
| `slm_optimize_stats()` | Compression runs, tokens saved, proxy hit and miss counts, and the key-value cache's hit and miss counts, kept across restarts |

Reversible compression is what makes lossy compression safe to use. When `slm_compress` shrinks text lossily it first stores the original, encrypted, under a `ccr_id` (compressed context retrieval), and `slm_retrieve` hands it back byte for byte. Treat a `ccr_id` as a secret: anyone who can call the tool as the same agent can read the original, so do not log or share them. Do not cache secrets with `slm_cache_set` either.

Both stores are isolated per agent. The tenant is the agent id from `/mcp/<agent_id>` or `SLM_AGENT_ID` ([Multi-Agent Memory](Multi-Agent-Memory)); a caller on another computer is keyed additionally by its remote key, so it can never read or overwrite a local agent's entries. Every tool is fail-open: on an internal error it returns the input unchanged with `ok: false` and never raises. `slm_compress` in `auto` or `aggressive` mode honours the `compress_enabled` setting (so `slm optimize off` makes it a pass-through), while `normalize` and the cache tools do not consult any setting. The master `enabled` flag is not consulted by any of them.

## Commands

```
slm optimize status | on | off | savings [--since N] [--provider P] [--json]
slm cache    status | clear | invalidate --tag T | ttl --set S --semantic S | semantic on|off
slm compress status | mode safe|aggressive | prose on|off
slm proxy    [--port P] [--provider anthropic|openai|gemini] [--no-compress] [--semantic]
slm wrap     [--list] [--persistent] [--dry-run] <agent> [-- agent args]
slm help-optimize [cache|compress|proxy|agents|safety]
```

`slm optimize off` turns off the master switch, cache, semantic cache and compression together; it does not stop the proxy, which then passes calls through. `slm compress code`, `ccr` and `align` are retired and only print a notice; the lossy extractive compressor they controlled was removed. `slm compress prose on` also flips the mode to aggressive, so the state is never "prose enabled but inert". `slm cache invalidate --tag mcp-kv` clears everything written by `slm_cache_set`.

Treat `slm optimize savings` figures as rough. The report is cumulative counters multiplied by a built-in price table (Anthropic $3.00 input per million tokens, OpenAI $2.50, Gemini $1.25, dated 2026-06-07 and flagged stale after 90 days; override with `pricing_overrides`). In this build `--since` changes only the heading, and the JSON field named `semantic_hits` carries the miss count. The dashboard shows the same counters.

## Dashboard pane

The Optimize pane has headline cards (hits, misses, tokens saved, compression ratio, estimated saving in USD and INR), a column of switches, a savings-by-provider table, and a 24-hour hit and miss chart. The switches are Master enable, Proxy, KV Cache, Compression, Semantic cache, Compress prose, and a safe or aggressive mode selector, plus "Clear cache (llmcache.db)". Cache, compression, mode and prose apply within about two seconds. Master and Proxy show "Restart required: run `slm restart`". The pane does not show a per-provider token breakdown; it shows the same total against each provider's price. See [Dashboard Tour](Dashboard-Tour) for the rest of the dashboard.

## Settings

Settings live in `optimize.json` in the data root and are re-read every two seconds, whether you change them from the dashboard, the CLI, `PUT /api/optimize/config`, or by hand. `proxy_enabled` is the exception: it is read once at daemon start. Defaults in the schema:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | false | Master flag; gates only the SDK wrapper |
| `proxy_enabled` | false | Mount the proxy routes at daemon start |
| `cache_enabled` | true | Exact-match cache |
| `semantic_enabled` | false | Fuzzy cache; return threshold 0.98 |
| `compress_enabled` | true | Lossless compression |
| `compress_mode` | `safe` | `safe` or `aggressive` |
| `compress_prose` | false | LLMLingua-2 prose compression (aggressive only) |
| `compress_protect_recent` | 4 | Latest messages never compressed |
| `ttl.exact_seconds` | 86400 | Exact cache lifetime (semantic 3600, CCR originals 604800, sweep 3600) |
| `prometheus_port` | 9091 | Optional metrics exporter port |

These defaults mean a fresh install shows Optimize as OFF in `slm optimize status` even though safe compression and the cache are individually enabled; they only act once something routes traffic through the proxy, the tools or the SDK wrapper. Older docs that say Optimize is on by default are wrong. Environment variables: `SLM_CACHE_BACKEND` and `SLM_CACHE_DB` select the cache store, and `SLM_OPTIMIZE_CAPTURE=1` records raw proxied traffic to `optimize_capture.jsonl` (owner-only, bodies cut at 1 MB per side); while capture is on the proxy runs as pure passthrough with cache and compression disabled, so use it only to build a benchmark corpus. The aggressive mode needs `llmlingua`, which is a base dependency; its model of roughly 560 MB is fetched at setup or warm-up per the dependency comment in `pyproject.toml`.

## What leaves the machine

On the proxy path your request bodies go to the provider the client already talks to: `api.anthropic.com`, `api.openai.com` or `generativelanguage.googleapis.com` (the Vertex host was not traced), with your own authentication headers forwarded. Nothing else is sent, and nothing from your SLM memory is added to the request. The proxy uses its own HTTP client rather than the credential-screening client SLM uses for model providers, because this is your own traffic to your own provider; keep that in mind if you inspect the capture file, which contains full bodies. The MCP tools do not use the network. Cached values and `ccr_id` originals are encrypted with AES-256-GCM using a machine-wide key in `opt-key.bin` (mode 0600), the database file is mode 0600, and a corrupt database is moved to a `.corrupt` sidecar and rebuilt. For the full data-flow picture see [Privacy and Data Flow](Privacy-and-Data-Flow).

Code: `src/superlocalmemory/optimize/` (`proxy/`, `cache/`, `compress/`, `config/`, `adapters/`, `storage/db.py`), `mcp/tools_optimize.py`, `cli/optimize_cmd.py`, `cli/cache_cmd.py`, `cli/compress_cmd.py`, `cli/proxy_cmd.py`, `server/routes/optimize.py`, `ui/js/od-optimize.js`.
