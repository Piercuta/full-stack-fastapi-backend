"""HttpOnly cookie helpers for JWT auth (browser sessions)."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from app.core.config import settings
from app.models import Token


def set_auth_cookie(response: JSONResponse, token: str) -> None:
    """Attach the JWT as an HttpOnly cookie on a JSON response."""
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        domain=settings.cookie_domain,
        path=settings.AUTH_COOKIE_PATH,
    )


def clear_auth_cookie(response: JSONResponse) -> None:
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        domain=settings.cookie_domain,
        path=settings.AUTH_COOKIE_PATH,
    )


def login_json_response(token: str) -> JSONResponse:
    """Return OAuth2-compatible JSON and set the HttpOnly auth cookie."""
    response = JSONResponse(content=Token(access_token=token).model_dump())
    set_auth_cookie(response, token)
    return response
