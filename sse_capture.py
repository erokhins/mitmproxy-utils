"""
Buffers streaming (SSE / chunked) responses so mitmproxy retains the full
body even when the client disconnects before the server closes the stream.

Without this, flows with Content-Type: text/event-stream show empty response
bodies because mitmproxy hits the error path ("Client disconnected.") and
discards the in-flight buffer.
"""
from mitmproxy import http

_resp_buffers: dict[str, bytearray] = {}


def responseheaders(flow: http.HTTPFlow):
    if "text/event-stream" not in flow.response.headers.get("content-type", ""):
        return

    buf = bytearray()
    _resp_buffers[flow.id] = buf

    def collect(chunk: bytes) -> bytes:
        if chunk:
            buf.extend(chunk)
        return chunk

    flow.response.stream = collect


def _save(flow: http.HTTPFlow, suffix: str = "") -> None:
    buf = _resp_buffers.pop(flow.id, None)
    if buf is None:
        return
    flow.response.content = bytes(buf)
    if suffix:
        flow.response.headers["x-mitm-captured"] = suffix


def response(flow: http.HTTPFlow):
    _save(flow)


def error(flow: http.HTTPFlow):
    if flow.response:
        _save(flow, "partial")
