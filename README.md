# mitmproxy LLM inspector

Intercepts and renders LLM API traffic (Anthropic, OpenAI chat completions, OpenAI Responses API) with readable formatting in the mitmweb UI.

## Setup

**1. Download mitmproxy binaries** (one-time):

```bash
./init.sh
```

This downloads mitmproxy 12.2.2 for Linux x86_64 into the current directory. The binaries are git-ignored.

**2. Trust the mitmproxy CA certificate** (one-time, so HTTPS interception works):

```bash
./mitmweb &   # run once to generate the cert, then kill it
# cert is now at ~/.mitmproxy/mitmproxy-ca-cert.pem
```

**3. Configure your client** — see [Configuring clients](#configuring-clients) below.

**4. Start the proxy**:

```bash
./start.sh
```

Then open **http://localhost:8081** (mitmweb UI). LLM requests are automatically rendered with the "LLM Request" content view.

## Configuring clients

Ready-to-use config files live in `tmp/experiment/`. Run experiments from that directory so each tool picks up its config automatically.

### Claude Code

File: `tmp/experiment/.claude/settings.json` — picked up automatically when you run `claude` from that directory.

```json
{
  "env": {
    "HTTP_PROXY": "http://localhost:8080",
    "HTTPS_PROXY": "http://localhost:8080",
    "NODE_EXTRA_CA_CERTS": "/home/erokhins/.mitmproxy/mitmproxy-ca-cert.pem"
  }
}
```

### Junie

File: `tmp/experiment/.junie/models/proxy.json` — add your model's `baseUrl`, `id`, and `apiKey`, then select this model in Junie.

```json
{
  "baseUrl": "FILL_IN",
  "id": "FILL_IN",
  "apiType": "OpenAICompletion",
  "apiKey": "FILL_IN",
  "debugProxyUrl": "http://localhost:8080/"
}
```

### Codex

File: `tmp/experiment/codex.json` — picked up automatically by the Codex CLI.

```json
{
  "proxy": "http://localhost:8080"
}
```

### Pi

Pi ([pi.dev](https://pi.dev)) is a Node.js CLI agent. It has no project-level proxy config, so route it through the proxy via environment variables:

```bash
source tmp/experiment/proxy.env
pi ...
```

`proxy.env` sets `HTTP_PROXY`, `HTTPS_PROXY`, and `NODE_EXTRA_CA_CERTS` (for the mitmproxy CA cert so TLS interception works). Pi's `models.json` `baseUrl` field redirects to a different API server — that's not the same as a forward proxy and won't help here.

## Addons

### `llm_request_view.py`

A mitmproxy content-view addon. Automatically activates on requests that look like LLM API calls; can also be applied manually to any JSON request via the mitmweb UI.

**Detects:**
- Anthropic / OpenAI chat-completions requests — `{model, messages}`
- OpenAI Responses API requests — `{model, input}`
- OpenAI Responses API responses — `{object: "response", output}`

**Rendering:**
- Pretty-printed JSON
- `\n` escape sequences inside strings are expanded into real newlines, indented to align with the opening `"` of the string value — making long system prompts and message content readable without scrolling through escaped text

### `llm_viewer.py`

A standalone web UI on **http://localhost:8082** that renders captured LLM requests with full markdown and syntax highlighting. Useful as a richer alternative to the mitmweb flow inspector.

## Scripts

| File | Purpose |
|------|---------|
| `start.sh` | Launch mitmweb with the LLM addons |
| `init.sh` | Download mitmproxy binaries |
