"""Transport helpers: the plain-HTTP listener that only redirects (Phase 4 §4.6)."""

from __future__ import annotations

from typing import Any

Scope = dict[str, Any]


def redirect_app(https_port: int) -> Any:
    """ASGI app answering every plain-HTTP request with `308 https://<host>:<https_port><path>`."""

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"localhost").decode().split(":")[0]
        port = "" if https_port == 443 else f":{https_port}"
        query = f"?{scope['query_string'].decode()}" if scope.get("query_string") else ""
        location = f"https://{host}{port}{scope['path']}{query}".encode()
        await send(
            {
                "type": "http.response.start",
                "status": 308,
                "headers": [(b"location", location), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    return app
