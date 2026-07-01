# mitmproxy LLM inspector

Intercepts and renders LLM API traffic (Anthropic, OpenAI chat completions, OpenAI Responses API) with readable formatting in the mitmweb UI.

## Setup

**1. Download mitmproxy binaries** (one-time):

```bash
./init.sh
```

This downloads mitmproxy 12.2.2 for Linux x86_64 into the current directory. The binaries are git-ignored.

**2. Configure your application** to use the proxy:

```json
"debugProxyUrl": "http://localhost:8080/"
```

**3. Start the proxy**:

```bash
./start.sh
```

Then open **http://localhost:8081** (mitmweb UI). LLM requests are automatically rendered with the "LLM Request" content view.

## Scripts

| File | Purpose |
|------|---------|
| `start.sh` | Launch mitmweb with the LLM addons |
| `init.sh` | Download mitmproxy binaries |

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


