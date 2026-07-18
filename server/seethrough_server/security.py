from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .config import Settings

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CLIENT_HEADER = "x-see-through-client"


def _host_name(host_header: str) -> str:
    value = host_header.strip()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end].lower() if end > 0 else ""
    return value.rsplit(":", 1)[0].lower()


def _same_origin(origin: str, host: str) -> bool:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        host = request.headers.get("host", "")
        if self.settings.loopback_only and _host_name(host) not in {
            "127.0.0.1",
            "::1",
            "localhost",
            self.settings.bind_host.lower(),
        }:
            response: Response = JSONResponse(
                {"detail": "Host is not allowed in loopback mode"}, status_code=400
            )
        elif request.method in MUTATING_METHODS and request.url.path.startswith("/api/"):
            if request.headers.get(CLIENT_HEADER) != "web":
                response = JSONResponse(
                    {"detail": "Missing See-through client header"}, status_code=403
                )
            else:
                origin = request.headers.get("origin")
                if origin and not _same_origin(origin, host):
                    response = JSONResponse(
                        {"detail": "Cross-origin request rejected"}, status_code=403
                    )
                else:
                    response = await call_next(request)
        else:
            response = await call_next(request)

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; font-src 'self'; img-src 'self' blob: data:; "
            "frame-ancestors 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        return response
