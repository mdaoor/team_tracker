"""Serve the prebuilt web console (apps/web/dist) and forward API paths to the API server.

Used by "ttctl start web" when Node is not installed (air-gapped bundle installs, servers): the same URL layout as the
Vite dev server and the nginx config in deploy/nginx/, standard library only.

    python scripts/lib/webserve.py --dist apps/web/dist --port 5173 --api http://127.0.0.1:8000
"""

import argparse
import http.client
import http.server
import os
import socketserver
import sys
import urllib.parse

PROXIED = ("/api/", "/auth/", "/p/")
PROXIED_EXACT = ("/healthz", "/readyz", "/metrics")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}


def make_handler(dist: str, api: str) -> type[http.server.SimpleHTTPRequestHandler]:
    target = urllib.parse.urlsplit(api)

    class Handler(http.server.SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
            super().__init__(*a, directory=dist, **kw)

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _proxied(self) -> bool:
            path = self.path.split("?", 1)[0]
            return path.startswith(PROXIED) or path in PROXIED_EXACT

        def _proxy(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=3600)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != "host"}
            headers["Host"] = self.headers.get("Host", "")
            headers["X-Forwarded-For"] = self.client_address[0]
            headers["X-Forwarded-Proto"] = "http"
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
            except OSError as exc:
                self.send_error(502, f"API not reachable at {api}: {exc}")
                return
            self.send_response(resp.status, resp.reason)
            streaming = resp.getheader("Content-Type", "").startswith("text/event-stream")
            for k, v in resp.getheaders():
                if k.lower() not in HOP and k.lower() != "content-length":
                    self.send_header(k, v)
            if streaming:
                self.send_header("Connection", "close")
                self.end_headers()
                while chunk := resp.read1(65536) if hasattr(resp, "read1") else resp.read(1024):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                self.close_connection = True
            else:
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
            conn.close()

        def _spa(self) -> None:
            path = urllib.parse.unquote(self.path.split("?", 1)[0])
            if not os.path.isfile(os.path.join(dist, path.lstrip("/"))):
                self.path = "/index.html"

        def do_GET(self) -> None:  # noqa: N802
            if self._proxied():
                self._proxy()
            else:
                self._spa()
                super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            if self._proxied():
                self._proxy()
            else:
                self._spa()
                super().do_HEAD()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy()

        do_PUT = do_PATCH = do_DELETE = do_POST  # noqa: N815

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dist", required=True)
    p.add_argument("--port", type=int, default=5173)
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 - same as the Vite dev server
    p.add_argument("--api", default="http://127.0.0.1:8000")
    a = p.parse_args()
    if not os.path.isfile(os.path.join(a.dist, "index.html")):
        sys.exit(f"{a.dist}/index.html not found - build the web console first")

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = Server((a.host, a.port), make_handler(os.path.abspath(a.dist), a.api))
    print(f"web console on http://{a.host}:{a.port} (static {a.dist}, API {a.api})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
