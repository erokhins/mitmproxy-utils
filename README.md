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

Claude Code is Node.js-based and respects standard proxy env vars:

```bash
source tmp/experiment/proxy.env
claude
```

### Junie

Junie is JVM-based, so it ignores `HTTP_PROXY`. Use `JAVA_TOOL_OPTIONS` instead, which the JVM reads automatically at startup.

**One-time: create a JKS truststore.** Copy the default Java cacerts first, then add the mitmproxy CA on top. This is important — setting `javax.net.ssl.trustStore` replaces the default truststore entirely, so without the standard CAs included, Junie can't verify any normal HTTPS endpoint either.

```bash
CACERTS=$(java -XshowSettings:property -version 2>&1 | grep java.home | awk '{print $NF}')/lib/security/cacerts
cp "$CACERTS" ~/.mitmproxy/mitmproxy-truststore.jks
keytool -importcert -alias mitmproxy \
  -file ~/.mitmproxy/mitmproxy-ca-cert.pem \
  -keystore ~/.mitmproxy/mitmproxy-truststore.jks \
  -storepass changeit -noprompt
```

**Then source `proxy.env`** before running Junie — it sets `JAVA_TOOL_OPTIONS` with the proxy host/port and truststore path:

```bash
source tmp/experiment/proxy.env
junie
```

Alternatively, Junie's model config supports `"debugProxyUrl"` for routing a specific model's traffic through the proxy without needing `JAVA_TOOL_OPTIONS`. Template: `tmp/experiment/.junie/models/proxy.json`.

### Codex

Codex's LLM HTTP client does not honor `HTTP_PROXY`/`HTTPS_PROXY` (open issue [openai/codex#4242](https://github.com/openai/codex/issues/4242)). Use a custom local proxy or other workaround — TBD.

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

### `sse_capture.py`

Buffers streaming (`text/event-stream`) response bodies and saves them explicitly in both the `response` and `error` hooks. Without this, flows where the client disconnects after reading a stream (the normal SSE lifecycle) show an empty response body in mitmweb because mitmproxy discards the in-flight buffer when it hits the error path.

### `llm_viewer.py`

A standalone web UI on **http://localhost:8083** that renders captured LLM requests with full markdown and syntax highlighting. Useful as a richer alternative to the mitmweb flow inspector.

## Scripts

| File | Purpose |
|------|---------|
| `start.sh` | Launch mitmweb with the LLM addons |
| `init.sh` | Download mitmproxy binaries |
