"""
Local reverse-proxy router — mitmproxy addon that path-routes requests hitting
one or more local listen ports to different backend servers, per reverse_proxy.conf.

Routed requests are captured like any other flow, so they show up in mitmweb
(http://localhost:8081) alongside everything else.

Load with: mitmweb -s reverse_proxy.py   (see start.sh, which also derives the
required --mode reverse:... flags and connection_strategy=lazy setting from
this same file — run `python3 reverse_proxy.py` standalone to see them).
"""

from __future__ import annotations

from collections import namedtuple

try:
    from mitmproxy import http  # only available when run as a mitmproxy addon
except ImportError:
    http = None  # running standalone as the `--print-modes` CLI helper

CONFIG_PATH = "reverse_proxy.conf"
ARROW = "-->"

# A plain namedtuple (not @dataclass) on purpose: mitmproxy's script loader
# doesn't register addon scripts in sys.modules the way a normal module
# import does, which breaks dataclass's forward-reference field resolution
# under `from __future__ import annotations` (needed below for 3.9 compat,
# since the CLI helper path runs under the system python3, not mitmproxy's
# bundled interpreter).
Route = namedtuple(
    "Route",
    "listen_host listen_port src_prefix target_host target_port dst_prefix",
)


def _split_host_path(spec: str) -> tuple[str, int, str]:
    hostport, _, path = spec.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port), "/" + path if path else "/"


def _strip_wildcard(path: str) -> str:
    if path.endswith("$url"):
        return path[: -len("$url")]
    return path


def parse_routes(path: str) -> list[Route]:
    routes: list[Route] = []
    try:
        lines = open(path).readlines()
    except OSError:
        return routes
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ARROW not in line:
            raise ValueError(f"{path}:{lineno}: missing '{ARROW}'")
        left, right = (p.strip() for p in line.split(ARROW, 1))
        lhost, lport, lpath = _split_host_path(left)
        rhost, rport, rpath = _split_host_path(right)
        src_prefix = _strip_wildcard(lpath)
        dst_prefix = _strip_wildcard(rpath)
        if dst_prefix == "/":
            dst_prefix = ""
        routes.append(Route(lhost, lport, src_prefix, rhost, rport, dst_prefix))
    # Most-specific (longest) prefix first, so overlapping routes on the same
    # listen port resolve predictably.
    routes.sort(key=lambda r: len(r.src_prefix), reverse=True)
    return routes


def match(path: str, prefix: str) -> str | None:
    if prefix in ("", "/"):
        return path
    if path == prefix:
        return ""
    if prefix.endswith("/"):
        return path[len(prefix):] if path.startswith(prefix) else None
    return path[len(prefix):] if path.startswith(prefix + "/") else None


def join(prefix: str, remainder: str) -> str:
    if not remainder:
        return prefix or "/"
    if not prefix:
        return "/" + remainder.lstrip("/")
    return prefix.rstrip("/") + "/" + remainder.lstrip("/")


def best_match(routes: list[Route], listen_port: int, path: str) -> tuple[Route, str] | None:
    for route in routes:
        if route.listen_port != listen_port:
            continue
        remainder = match(path, route.src_prefix)
        if remainder is not None:
            return route, remainder
    return None


ROUTES: list[Route] = []


def load(loader) -> None:
    global ROUTES
    ROUTES = parse_routes(CONFIG_PATH)


def requestheaders(flow: http.HTTPFlow) -> None:
    # Rewrite as soon as headers arrive, not in request() (which only fires
    # after the full body is read): with stream_large_bodies set low, mitmproxy
    # opens the upstream connection off the Content-Length header as soon as
    # headers are in, using whatever host/port the flow has *at that point* —
    # for a reverse-mode listener, that's still the dummy placeholder target
    # unless we've already rewritten it here.
    if not ROUTES:
        return
    listen_port = flow.client_conn.sockname[1]
    found = best_match(ROUTES, listen_port, flow.request.path)
    if found is None:
        return
    route, remainder = found
    flow.request.scheme = "http"
    flow.request.host = route.target_host
    flow.request.port = route.target_port
    flow.request.host_header = f"{route.target_host}:{route.target_port}"
    flow.request.path = join(route.dst_prefix, remainder)


if __name__ == "__main__":
    listen_addrs = sorted({(r.listen_host, r.listen_port) for r in parse_routes(CONFIG_PATH)})
    for host, port in listen_addrs:
        print(f"--mode reverse:http://127.0.0.1:1@{host}:{port}")
