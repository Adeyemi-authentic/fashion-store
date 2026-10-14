"""Request id + one access log line per request (method, path, status, duration)."""

import logging
import time
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.platform.logging import new_request_id, request_id_var

log = logging.getLogger("app.access")

RequestObserver = Callable[[str, str, int, float], None]  # route, method, status, seconds


class RequestIdMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware), so it adds no overhead to streaming responses."""

    def __init__(self, app: ASGIApp, observers: list[RequestObserver] | None = None) -> None:
        self.app = app
        self.observers = observers or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        request_id = new_request_id(headers.get(b"x-request-id", b"").decode() or None)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            seconds = time.perf_counter() - started
            route = scope.get("route")
            # The route template (/products/{slug}), not the raw path: keeps metrics bounded.
            template = getattr(route, "path", None) or "unmatched"
            log.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                status,
                extra={
                    "status": status,
                    "duration_ms": round(seconds * 1000, 1),
                    "route": template,
                },
            )
            for observe in self.observers:
                observe(template, scope["method"], status, seconds)
            request_id_var.reset(token)


__all__ = ["RequestIdMiddleware"]
