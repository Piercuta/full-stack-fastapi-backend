"""Reject cross-origin mutating requests when auth uses cookies (CSRF mitigation)."""

from __future__ import annotations

from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CookieCsrfMiddleware(BaseHTTPMiddleware):
    """Allow cookie-auth API calls only from configured CORS origins.

    Requests with ``Authorization: Bearer`` skip this check (API clients/tests).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method in _UNSAFE_METHODS:
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                origin = request.headers.get("origin")
                if origin:
                    allowed = {o.rstrip("/") for o in settings.all_cors_origins}
                    if origin.rstrip("/") not in allowed:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Cross-origin request blocked"},
                        )
        return await call_next(request)
