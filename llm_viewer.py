"""
LLM Request Viewer — mitmproxy addon + built-in web server on http://localhost:8082

Captures LLM API request bodies (Claude/OpenAI-style JSON) and renders them
with full markdown + syntax highlighting in a standalone browser tab.

Load with: mitmproxy -s llm_viewer.py
"""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mitmproxy import http

PORT = 8082
MAX_FLOWS = 200

_flows: dict[str, dict] = {}
_flow_order: list[str] = []
_sse_queues: list[queue.Queue] = []
_req_buffers: dict[str, bytearray] = {}
_lock = threading.Lock()


def _is_llm_request(data: dict) -> bool:
    return isinstance(data.get("model"), str) and (
        isinstance(data.get("messages"), list) or
        isinstance(data.get("input"), list)
    )


def _normalize_tools(tools: object) -> list | None:
    if not isinstance(tools, list):
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        # OpenAI Chat Completions: {"type":"function","function":{"name","description","parameters"}}
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            })
        # OpenAI Responses API: {"type":"function","name","description","parameters"} (flat)
        elif t.get("type") == "function" and "name" in t and "function" not in t:
            out.append({
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {}),
            })
        else:
            # Anthropic format: {"name","description","input_schema"} — pass through
            out.append(t)
    return out or None


def _parse_flow(flow: http.HTTPFlow) -> dict | None:
    try:
        body = flow.request.content
        if not body:
            return None
        parsed = json.loads(body)
        if not _is_llm_request(parsed):
            return None
        # Responses API uses "input" instead of "messages"
        messages = parsed.get("messages") or parsed.get("input") or []
        # Responses API uses "max_output_tokens"; also check "reasoning" for thinking config
        max_tokens = parsed.get("max_tokens") or parsed.get("max_output_tokens")
        thinking = parsed.get("thinking") or parsed.get("reasoning")
        return {
            "id": flow.id,
            "timestamp": flow.request.timestamp_start,
            "url": flow.request.pretty_url,
            "model": parsed.get("model", ""),
            "max_tokens": max_tokens,
            "temperature": parsed.get("temperature"),
            "system": parsed.get("system") or parsed.get("instructions"),
            "messages": messages,
            "tools": _normalize_tools(parsed.get("tools")),
            "tool_choice": parsed.get("tool_choice"),
            "thinking": thinking,
            "response": None,
        }
    except Exception:
        return None


def _push_sse(event: dict) -> None:
    with _lock:
        queues = list(_sse_queues)
    for q in queues:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/flows":
            self._serve_flows_list()
        elif self.path.startswith("/flows/"):
            self._serve_flow(self.path[len("/flows/"):])
        elif self.path == "/sse":
            self._serve_sse()
        else:
            self.send_error(404)

    def _send_json(self, data: object) -> None:
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        body = HTML_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_flows_list(self) -> None:
        with _lock:
            items = [
                {
                    "id": fid,
                    "timestamp": _flows[fid]["timestamp"],
                    "url": _flows[fid]["url"],
                    "model": _flows[fid]["model"],
                }
                for fid in _flow_order
                if fid in _flows
            ]
        self._send_json(items)

    def _serve_flow(self, flow_id: str) -> None:
        with _lock:
            data = _flows.get(flow_id)
        if data is None:
            self.send_error(404)
            return
        self._send_json(data)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=50)
        with _lock:
            _sse_queues.append(q)
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _lock:
                if q in _sse_queues:
                    _sse_queues.remove(q)


# ── mitmproxy hooks ────────────────────────────────────────────────────────────

def requestheaders(flow: http.HTTPFlow) -> None:
    buf: bytearray = bytearray()
    _req_buffers[flow.id] = buf
    def capture(chunk: bytes) -> bytes:
        if chunk:
            buf.extend(chunk)
        return chunk
    flow.request.stream = capture


def request(flow: http.HTTPFlow) -> None:
    buf = _req_buffers.pop(flow.id, None)
    if buf:
        flow.request.content = bytes(buf)
    if "json" not in flow.request.headers.get("content-type", ""):
        return
    data = _parse_flow(flow)
    if data is None:
        return
    with _lock:
        _flows[flow.id] = data
        if flow.id not in _flow_order:
            _flow_order.append(flow.id)
            if len(_flow_order) > MAX_FLOWS:
                oldest = _flow_order.pop(0)
                _flows.pop(oldest, None)
    _push_sse({"id": flow.id, "model": data["model"], "url": data["url"], "timestamp": data["timestamp"]})


def _parse_sse_events(body: bytes) -> list[tuple[str | None, dict]]:
    """Parse an SSE body into (event_name, data_dict) pairs."""
    events: list[tuple[str | None, dict]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                current_data.append(chunk)
        elif not line and current_data:
            try:
                events.append((current_event, json.loads("\n".join(current_data))))
            except Exception:
                pass
            current_event = None
            current_data = []
    return events


def _parse_sse_response(body: bytes) -> dict | None:
    events = _parse_sse_events(body)
    if not events:
        return None

    # ── OpenAI Responses API streaming ────────────────────────────────────────
    for event_name, data in events:
        if event_name == "response.completed" and isinstance(data.get("response"), dict):
            return _parse_response(data["response"])

    # ── Anthropic streaming ───────────────────────────────────────────────────
    message_start: dict | None = None
    content_blocks: dict[int, dict] = {}
    stop_reason: str | None = None
    usage: dict = {}

    for _, data in events:
        t = data.get("type")
        if t == "message_start":
            message_start = data.get("message", {})
            usage = dict(message_start.get("usage", {}))
        elif t == "content_block_start":
            idx = data.get("index", 0)
            content_blocks[idx] = dict(data.get("content_block", {}))
        elif t == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta", {})
            block = content_blocks.setdefault(idx, {})
            dt = delta.get("type")
            if dt == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
                block.setdefault("type", "text")
            elif dt == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                block.setdefault("type", "thinking")
            elif dt == "input_json_delta":
                block["_json"] = block.get("_json", "") + delta.get("partial_json", "")
        elif t == "message_delta":
            stop_reason = data.get("delta", {}).get("stop_reason", stop_reason)
            usage.update(data.get("usage", {}))

    if message_start is not None:
        blocks = []
        for idx in sorted(content_blocks):
            b = content_blocks[idx]
            if "_json" in b:
                try:
                    b["input"] = json.loads(b.pop("_json"))
                except Exception:
                    b["input"] = {}
            blocks.append(b)
        return _parse_response({
            "id": message_start.get("id"),
            "type": "message",
            "role": "assistant",
            "content": blocks,
            "stop_reason": stop_reason,
            "usage": usage,
        })

    # ── OpenAI Chat Completions streaming ─────────────────────────────────────
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    chat_id: str | None = None
    raw_usage: dict = {}

    for _, data in events:
        if data.get("object") != "chat.completion.chunk":
            continue
        chat_id = chat_id or data.get("id")
        choices = data.get("choices") or []
        if choices:
            delta = choices[0].get("delta", {})
            if delta.get("content"):
                text_parts.append(delta["content"])
            r = delta.get("reasoning") or delta.get("reasoning_content") or ""
            if r:
                reasoning_parts.append(r)
            if choices[0].get("finish_reason"):
                finish_reason = choices[0]["finish_reason"]
        if data.get("usage"):
            raw_usage = data["usage"]

    if text_parts or reasoning_parts:
        blocks = []
        if reasoning_parts:
            blocks.append({"type": "thinking", "thinking": "".join(reasoning_parts)})
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})
        return {
            "id": chat_id,
            "content": blocks,
            "stop_reason": finish_reason,
            "usage": {
                "input_tokens": raw_usage.get("prompt_tokens"),
                "output_tokens": raw_usage.get("completion_tokens"),
            },
        }

    return None


def _parse_response(parsed: dict) -> dict | None:
    """Normalize Anthropic or OpenAI-compatible response to a common shape."""
    # Anthropic format: {"type": "message", "content": [...], "stop_reason": ...}
    if parsed.get("type") == "message" or (
        parsed.get("role") == "assistant" and "content" in parsed
    ):
        return {
            "id": parsed.get("id"),
            "content": parsed.get("content", []),
            "stop_reason": parsed.get("stop_reason"),
            "usage": parsed.get("usage"),
        }

    # OpenAI Responses API: {"object":"response","output":[...]}
    if parsed.get("object") == "response" or (
        isinstance(parsed.get("output"), list) and parsed.get("model")
    ):
        blocks = []
        for item in parsed.get("output", []):
            t = item.get("type")
            if t == "reasoning":
                inner = item.get("content", [])
                if inner:
                    for c in inner:
                        text = c.get("text") or c.get("thinking", "")
                        if text:
                            blocks.append({"type": "thinking", "thinking": text})
                elif item.get("encrypted_content"):
                    blocks.append({"type": "thinking", "thinking": "[reasoning encrypted]"})
            elif t == "function_call":
                try:
                    input_data = json.loads(item.get("arguments", "{}"))
                except Exception:
                    input_data = {"arguments": item.get("arguments", "")}
                blocks.append({
                    "type": "tool_use",
                    "id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "input": input_data,
                })
            elif t == "message":
                for c in item.get("content", []):
                    ct = c.get("type", "")
                    if ct == "output_text":
                        blocks.append({"type": "text", "text": c.get("text", "")})
                    elif ct == "refusal":
                        blocks.append({"type": "text", "text": c.get("refusal", "")})
        raw_usage = parsed.get("usage", {})
        return {
            "id": parsed.get("id"),
            "content": blocks,
            "stop_reason": parsed.get("status"),
            "usage": {
                "input_tokens": raw_usage.get("input_tokens"),
                "output_tokens": raw_usage.get("output_tokens"),
            },
        }

    # OpenAI Chat Completions: {"object":"chat.completion","choices":[...]}
    choices = parsed.get("choices")
    if choices and isinstance(choices, list):
        choice = choices[0]
        message = choice.get("message", {})
        text = message.get("content") or ""
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""

        blocks = []
        if reasoning:
            blocks.append({"type": "thinking", "thinking": reasoning})
        if text:
            blocks.append({"type": "text", "text": text})

        raw_usage = parsed.get("usage", {})
        usage = {
            "input_tokens": raw_usage.get("prompt_tokens"),
            "output_tokens": raw_usage.get("completion_tokens"),
        }
        if raw_usage.get("cost") is not None:
            usage["cost"] = raw_usage["cost"]

        return {
            "id": parsed.get("id"),
            "content": blocks,
            "stop_reason": choice.get("finish_reason"),
            "usage": usage,
        }

    return None


def _handle_response_body(flow: http.HTTPFlow) -> None:
    with _lock:
        if flow.id not in _flows:
            return
    try:
        body = flow.response.content
        if not body:
            return
        ct = flow.response.headers.get("content-type", "")
        if "text/event-stream" in ct:
            resp = _parse_sse_response(body)
        elif "json" in ct:
            resp = _parse_response(json.loads(body))
        else:
            return
        if resp is None:
            return
        with _lock:
            _flows[flow.id]["response"] = resp
        _push_sse({"type": "response", "id": flow.id})
    except Exception:
        pass


def response(flow: http.HTTPFlow) -> None:
    _handle_response_body(flow)


def error(flow: http.HTTPFlow) -> None:
    # sse_capture.py saves the buffered SSE body to flow.response.content
    # in its error hook (which runs before ours). Parse it here.
    if flow.response:
        _handle_response_body(flow)


def running() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"\n  LLM Viewer → http://localhost:{PORT}\n")


# ── embedded HTML page ─────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LLM Viewer</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@4.3.0/marked.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#1a1a1a;height:100vh;display:flex;flex-direction:column;overflow:hidden}

#toolbar{background:#fff;border-bottom:1px solid #e0e0e0;padding:8px 16px;display:flex;align-items:center;gap:10px;flex-shrink:0;box-shadow:0 1px 3px rgba(0,0,0,.06)}
#toolbar h1{font-size:13px;font-weight:600;color:#555;letter-spacing:.04em}
#dot{width:7px;height:7px;border-radius:50%;background:#ccc;transition:background .3s;margin-left:auto}
#dot.live{background:#4caf50}
#dot.err{background:#e53}

#main{display:flex;flex:1;overflow:hidden}

#sidebar{width:280px;flex-shrink:0;background:#fff;border-right:1px solid #e8e8e8;display:flex;flex-direction:column;overflow:hidden}
#sidebar-hd{padding:9px 12px;font-size:10px;font-weight:700;color:#999;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #f0f0f0;flex-shrink:0}
#flow-list{overflow-y:auto;flex:1}
.fi{padding:9px 12px;cursor:pointer;border-bottom:1px solid #f5f5f5;transition:background .1s}
.fi:hover{background:#f8f8f8}
.fi.active{background:#e8f0fd;border-left:3px solid #1a73e8;padding-left:9px}
.fi-model{font-size:12px;font-weight:600;color:#1a1a1a}
.fi-url{font-size:11px;color:#999;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fi-time{font-size:10px;color:#bbb;margin-top:2px}

#content{flex:1;overflow-y:auto;padding:20px 28px;background:#f5f5f5}
#placeholder{display:flex;align-items:center;justify-content:center;height:100%;color:#bbb;font-size:14px}
#flow-view{display:none}

.fhdr{background:#fff;border:1px solid #e8e8e8;border-radius:8px;padding:10px 14px;margin-bottom:18px;font-size:12px;line-height:1.9;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.fhdr .lbl{color:#999}
.fhdr .val{color:#1a1a1a;font-weight:600}
.fhdr .url{color:#aaa;font-size:11px}

.msg{margin-bottom:12px;border-radius:8px;overflow:hidden;border:1px solid #e8e8e8;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.msg-hd{padding:6px 14px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;display:flex;align-items:center}
.msg-hd.user{background:#f0faf0;color:#2e7d32;border-bottom:1px solid #e0f0e0}
.msg-hd.assistant{background:#f0f4ff;color:#1a56c4;border-bottom:1px solid #dde8ff}
.msg-hd.system{background:#fff8f0;color:#c25a00;border-bottom:1px solid #fde8d0}
.msg-body{padding:14px}
.msg-tabs{margin-left:auto;display:flex;gap:2px}
.mtab{padding:1px 8px;font-size:9px;font-weight:700;border-radius:3px;cursor:pointer;opacity:.5;text-transform:uppercase;letter-spacing:.05em;user-select:none;border:1px solid currentColor}
.mtab:hover{opacity:.8}
.mtab.active{opacity:1;background:rgba(0,0,0,.1)}

.md{font-size:14px;line-height:1.65;color:#222}
.md h1,.md h2,.md h3,.md h4{color:#111;margin:1em 0 .4em;font-weight:600}
.md h1{font-size:1.3em;border-bottom:1px solid #eee;padding-bottom:.3em}
.md h2{font-size:1.1em;border-bottom:1px solid #f0f0f0;padding-bottom:.2em}
.md h3{font-size:1em}
.md p{margin:.5em 0}
.md ul,.md ol{margin:.4em 0;padding-left:1.4em}
.md li{margin:.15em 0}
.md code{background:#f0f4ff;border:1px solid #dde8ff;border-radius:3px;padding:1px 5px;font-family:'Fira Code',monospace;font-size:.88em;color:#1a56c4}
.md pre{margin:.7em 0;border-radius:6px;overflow-x:auto;border:1px solid #e8e8e8}
.md pre code{background:none;border:none;padding:0;color:inherit}
.md blockquote{border-left:3px solid #ddd;padding-left:1em;color:#888;margin:.5em 0;font-style:italic}
.md strong{color:#000}
.md a{color:#1a73e8}
.md table{border-collapse:collapse;width:100%;margin:.5em 0;font-size:13px}
.md th,.md td{border:1px solid #e0e0e0;padding:5px 10px}
.md th{background:#f8f8f8;color:#555}
.md hr{border:none;border-top:1px solid #eee;margin:1em 0}

.tool{border:1px solid #e8e8e8;border-radius:6px;margin:8px 0;overflow:hidden;background:#fff}
.tool-hd{padding:7px 12px;background:#fafafa;border-bottom:1px solid #f0f0f0;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:8px;user-select:none}
.tool-hd:hover{background:#f5f5f5}
.tool-nm{font-weight:600;color:#c25a00}
.tool-id{color:#bbb;font-size:11px}
.tool-arr{color:#bbb;margin-left:auto;font-size:11px}
.tool-body{padding:12px;display:none}
.tool-body.open{display:block}
.tool-body pre{margin:0;font-size:12px;border-radius:4px;overflow-x:auto}
.hljs{background:#f6f8fa!important}
pre code.hljs{padding:12px!important;font-size:12px!important}

.tools-section{margin-bottom:16px}
.tools-toggle{width:100%;text-align:left;background:#fafafa;border:1px solid #e8e8e8;border-radius:8px;padding:9px 14px;cursor:pointer;font-size:12px;font-weight:600;color:#555;display:flex;align-items:center;gap:6px}
.tools-toggle:hover{background:#f5f5f5}
.tools-toggle .tc{margin-left:auto;font-size:11px;color:#bbb}
.tools-list{border:1px solid #e8e8e8;border-top:none;border-radius:0 0 8px 8px;overflow:hidden;display:none}
.tools-list.open{display:block}
.tool-def{border-bottom:1px solid #f0f0f0}
.tool-def:last-child{border-bottom:none}
.tool-def-hd{padding:8px 14px;background:#fff;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:8px}
.tool-def-hd:hover{background:#fafafa}
.tool-def-nm{font-weight:600;color:#1a1a1a}
.tool-def-desc{color:#999;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.tool-def-arr{color:#ccc;font-size:10px;flex-shrink:0}
.tool-def-body{padding:12px 14px;background:#f8f8f8;border-top:1px solid #f0f0f0;display:none;font-size:13px;overflow-x:auto}
.tool-def-body.open{display:block}
.tool-def-body .md{font-size:13px}
.resp-section{margin-top:8px}
.resp-hd{padding:7px 14px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;background:#f5f0ff;color:#6b21a8;border-bottom:1px solid #ede0ff}
.resp-meta{padding:7px 14px;font-size:11px;color:#999;border-bottom:1px solid #f5f5f5;background:#fafafa}
.resp-meta span{margin-right:12px}
.resp-meta .val{color:#555;font-weight:600}

.mjpanel{display:none;padding:12px;background:#f8f8f8;border-top:1px solid #f0f0f0}
.mjpanel pre{margin:0}
</style>
</head>
<body>
<div id="toolbar">
  <h1>LLM Request Viewer</h1>
  <div id="dot" title="SSE connection"></div>
</div>
<div id="main">
  <div id="sidebar">
    <div id="sidebar-hd">Captured Requests</div>
    <div id="flow-list"></div>
  </div>
  <div id="content">
    <div id="placeholder">Waiting for requests…</div>
    <div id="flow-view"></div>
  </div>
</div>
<script>
// marked v4 API — only highlight explicitly-tagged code blocks; never auto-detect
marked.setOptions({
  breaks: true,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, {language: lang, ignoreIllegals: true}).value; } catch(e) {}
    }
    return esc(code);  // plain text for unknown/unspecified languages
  }
});

function md(text) {
  try {
    // Pre-escape < and > so HTML tags in prose (e.g. <code>, <issue_description>)
    // are rendered as literal text instead of being parsed as HTML by marked.
    // This prevents unclosed elements from leaking across content sections.
    const safe = (text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return '<div class="md">' + marked.parse(safe) + '</div>';
  }
  catch(e) { return '<div class="md"><pre>'+esc(text)+'</pre></div>'; }
}
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmtTime(ts){ return new Date(ts*1000).toLocaleTimeString(); }
function fmtUrl(url){ try{return new URL(url).pathname}catch(e){return url} }
function jsonHl(obj){
  try { return hljs.highlight(JSON.stringify(obj,null,2),{language:'json'}).value; }
  catch(e) { return esc(JSON.stringify(obj,null,2)); }
}

let _uid = 0;
function uid(){ return 'tb-'+(++_uid); }

function tog(id){
  const body=document.getElementById(id), arr=document.getElementById(id+'a');
  const open=body.classList.toggle('open');
  if(arr) arr.textContent=open?'▼':'▶';
}

function showMsgTab(bodyId, jsonId, panel, btn) {
  document.getElementById(bodyId).style.display = panel === 'r' ? 'block' : 'none';
  document.getElementById(jsonId).style.display = panel === 'j' ? 'block' : 'none';
  btn.closest('.msg-tabs').querySelectorAll('.mtab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function renderBlock(b) {
  if (typeof b === 'string') return md(b);
  const t = b.type || 'unknown';
  if (t === 'text' || t === 'input_text' || t === 'output_text') return md(b.text || '');
  if (t === 'thinking') {
    const id = uid();
    return `<div class="tool">
      <div class="tool-hd" onclick="tog('${id}')">
        <span class="tool-nm" style="color:#6b21a8">💭 thinking</span>
        <span class="tool-arr" id="${id}a">▶</span>
      </div>
      <div class="tool-body" id="${id}">${md(b.thinking||'')}</div></div>`;
  }
  if (t === 'tool_use' || t === 'function_call') {
    const id = uid();
    let input = b.input || {};
    if (t === 'function_call' && typeof b.arguments === 'string') {
      try { input = JSON.parse(b.arguments); } catch(e) { input = {arguments: b.arguments}; }
    }
    const callId = b.call_id || b.id || '';
    return `<div class="tool">
      <div class="tool-hd" onclick="tog('${id}')">
        <span class="tool-nm">⚙ ${esc(b.name||'?')}</span>
        <span class="tool-id">${esc(callId)}</span>
        <span class="tool-arr" id="${id}a">▶</span>
      </div>
      <div class="tool-body" id="${id}">
        <pre><code class="hljs language-json">${jsonHl(input)}</code></pre>
      </div></div>`;
  }
  if (t === 'tool_result') {
    const id = uid();
    const c = b.content;
    let inner;
    if (typeof c === 'string') inner = md(c);
    else if (Array.isArray(c)) inner = c.map(renderBlock).join('');
    else inner = `<pre><code class="hljs language-json">${jsonHl(c)}</code></pre>`;
    return `<div class="tool">
      <div class="tool-hd" onclick="tog('${id}')">
        <span class="tool-nm">↩ result</span>
        <span class="tool-id">${esc(b.tool_use_id||'')}</span>
        <span class="tool-arr" id="${id}a">▶</span>
      </div>
      <div class="tool-body" id="${id}">${inner}</div></div>`;
  }
  return `<pre><code class="hljs language-json">${jsonHl(b)}</code></pre>`;
}

function msgTabs(obj, bodyId, jsonId, cls) {
  return `<div class="msg-tabs">
    <span class="mtab active" onclick="showMsgTab('${bodyId}','${jsonId}','r',this)">Rendered</span>
    <span class="mtab" onclick="showMsgTab('${bodyId}','${jsonId}','j',this)">JSON</span>
  </div>`;
}

function renderMsg(obj, labelHtml, cls, contentHtml) {
  const bodyId = uid(), jsonId = uid();
  return `<div class="msg">
    <div class="msg-hd ${cls}">${labelHtml}${msgTabs(obj, bodyId, jsonId, cls)}</div>
    <div class="msg-body" id="${bodyId}">${contentHtml}</div>
    <div class="mjpanel" id="${jsonId}"><pre><code class="hljs language-json">${jsonHl(obj)}</code></pre></div>
  </div>`;
}

function renderContent(c) {
  if (typeof c === 'string') return md(c);
  if (Array.isArray(c)) return c.map(renderBlock).join('');
  return '<em style="color:#bbb">empty</em>';
}

function renderTools(tools) {
  if (!tools || !tools.length) return '';
  const id = uid();
  const defs = tools.map(t => {
    const tid = uid();
    const desc = (t.description || '').split('\\n')[0].slice(0, 120);
    const schema = t.input_schema || {};
    return `<div class="tool-def">
      <div class="tool-def-hd" onclick="tog('${tid}')">
        <span class="tool-def-nm">${esc(t.name||'?')}</span>
        <span class="tool-def-desc">${esc(desc)}</span>
        <span class="tool-def-arr" id="${tid}a">▶</span>
      </div>
      <div class="tool-def-body" id="${tid}">
        ${t.description ? md(t.description) : ''}
        <div style="margin-top:8px"><pre><code class="hljs language-json">${jsonHl(schema)}</code></pre></div>
      </div>
    </div>`;
  }).join('');
  return `<div class="tools-section">
    <button class="tools-toggle" onclick="tog('${id}')">
      ⚒ Tools <span style="color:#bbb;font-weight:400">(${tools.length})</span>
      <span class="tc" id="${id}a">▶</span>
    </button>
    <div class="tools-list" id="${id}">${defs}</div>
  </div>`;
}

function renderResponse(resp) {
  if (!resp) return '';
  const usage = resp.usage || {};
  const cost = usage.cost != null ? `$${Number(usage.cost).toFixed(5)}` : null;
  const metaParts = [
    resp.stop_reason ? `<span class="lbl">stop </span><span class="val">${esc(resp.stop_reason)}</span>` : '',
    usage.input_tokens != null ? `<span class="lbl">in </span><span class="val">${usage.input_tokens}</span>` : '',
    usage.output_tokens != null ? `<span class="lbl">out </span><span class="val">${usage.output_tokens}</span>` : '',
    usage.cache_read_input_tokens ? `<span class="lbl">cache_read </span><span class="val">${usage.cache_read_input_tokens}</span>` : '',
    cost ? `<span class="lbl">cost </span><span class="val">${cost}</span>` : '',
  ].filter(Boolean).join(' &nbsp;');
  const bodyId = uid(), jsonId = uid();
  return `<div class="msg resp-section">
    <div class="msg-hd resp-hd">Response${msgTabs(resp, bodyId, jsonId, 'resp-hd')}</div>
    ${metaParts ? `<div class="resp-meta">${metaParts}</div>` : ''}
    <div class="msg-body" id="${bodyId}">${renderContent(resp.content)}</div>
    <div class="mjpanel" id="${jsonId}"><pre><code class="hljs language-json">${jsonHl(resp)}</code></pre></div>
  </div>`;
}

function renderFlow(d) {
  let html = `<div class="fhdr">
    <span class="lbl">model </span><span class="val">${esc(d.model)}</span>`;
  if (d.max_tokens) html += ` <span class="lbl">· max_tokens </span><span class="val">${d.max_tokens}</span>`;
  if (d.temperature != null) html += ` <span class="lbl">· temperature </span><span class="val">${d.temperature}</span>`;
  if (d.thinking) html += ` <span class="lbl">· thinking </span><span class="val">${esc(d.thinking.budget_tokens||'')}</span>`;
  html += `<br><span class="url">${esc(d.url)}</span></div>`;

  html += renderTools(d.tools);

  if (d.system) {
    const sc = Array.isArray(d.system) ? d.system : [{type:'text', text: String(d.system)}];
    html += renderMsg(d.system, 'System', 'system', renderContent(sc));
  }
  for (const msg of (d.messages || [])) {
    const role = msg.role || 'unknown';
    const cls = role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : 'system';
    html += renderMsg(msg, esc(role), cls, renderContent(msg.content));
  }
  html += renderResponse(d.response);
  return html;
}

let selectedId = null;

function selectFlow(id) {
  selectedId = id;
  document.querySelectorAll('.fi').forEach(el => el.classList.toggle('active', el.dataset.id === id));
  fetch('/flows/' + id)
    .then(r => { if (!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(d => {
      document.getElementById('placeholder').style.display = 'none';
      const v = document.getElementById('flow-view');
      v.style.display = 'block';
      v.innerHTML = renderFlow(d);
    })
    .catch(err => console.error('selectFlow failed:', err));
}

function addItem(f, prepend) {
  const list = document.getElementById('flow-list');
  const el = document.createElement('div');
  el.className = 'fi';
  el.dataset.id = f.id;
  el.innerHTML = `<div class="fi-model">${esc(f.model)}</div><div class="fi-url">${esc(fmtUrl(f.url))}</div><div class="fi-time">${fmtTime(f.timestamp)}</div>`;
  el.onclick = () => selectFlow(f.id);
  if (prepend) list.prepend(el); else list.appendChild(el);
}

// Load existing flows (newest first)
fetch('/flows')
  .then(r => r.json())
  .then(data => {
    data.forEach(f => addItem(f, true));
    if (data.length) selectFlow(data[data.length - 1].id);
  })
  .catch(err => console.error('Failed to load flows:', err));

// Live updates via SSE
const dot = document.getElementById('dot');
const sse = new EventSource('/sse');
sse.onopen = () => { dot.className = 'live'; };
sse.onerror = () => { dot.className = 'err'; };
sse.onmessage = e => {
  const ev = JSON.parse(e.data);
  if (ev.type === 'response') {
    // re-render the current view if this response belongs to it
    if (ev.id === selectedId) selectFlow(ev.id);
    return;
  }
  // new request flow
  if (!document.querySelector(`.fi[data-id="${ev.id}"]`)) {
    addItem(ev, true);
  }
  if (!selectedId) selectFlow(ev.id);
};
</script>
</body>
</html>"""
