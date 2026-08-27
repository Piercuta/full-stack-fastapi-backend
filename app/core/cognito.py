"""Cognito Hosted UI helpers for authorization-code → app user (flow B)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import settings

logger = logging.getLogger(__name__)


def cognito_configured() -> bool:
    return bool(
        settings.COGNITO_CLIENT_ID
        and settings.COGNITO_DOMAIN
        and settings.COGNITO_ISSUER
    )


def _hosted_ui_base() -> str:
    domain = (settings.COGNITO_DOMAIN or "").rstrip("/")
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    region = settings.AWS_REGION
    return f"https://{domain}.auth.{region}.amazoncognito.com"


def exchange_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for Cognito tokens."""
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": settings.COGNITO_CLIENT_ID or "",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth: tuple[str, str] | None = None
    if settings.COGNITO_CLIENT_SECRET:
        auth = (settings.COGNITO_CLIENT_ID or "", settings.COGNITO_CLIENT_SECRET)

    token_url = urljoin(_hosted_ui_base() + "/", "oauth2/token")
    with httpx.Client(timeout=15.0) as client:
        response = client.post(token_url, data=data, headers=headers, auth=auth)
        if response.status_code >= 400:
            logger.warning(
                "Cognito token exchange failed: %s %s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()
        return response.json()


def validate_id_token(id_token: str) -> dict[str, Any]:
    """Validate Cognito ID token and return claims."""
    issuer = (settings.COGNITO_ISSUER or "").rstrip("/")
    jwks_url = f"{issuer}/.well-known/jwks.json"
    jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.COGNITO_CLIENT_ID,
        issuer=issuer,
    )


def fetch_user_info(access_token: str) -> dict[str, Any]:
    """Fetch OIDC userInfo (often has name/given_name when id_token does not)."""
    userinfo_url = urljoin(_hosted_ui_base() + "/", "oauth2/userInfo")
    with httpx.Client(timeout=15.0) as client:
        response = client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code >= 400:
            logger.warning(
                "Cognito userInfo failed: %s %s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()
        return response.json()


def resolve_display_name(claims: dict[str, Any], email: str) -> str:
    """Best-effort display name from Cognito/OIDC claims."""
    full_name = claims.get("name") or " ".join(
        part
        for part in (claims.get("given_name"), claims.get("family_name"))
        if part
    )
    if isinstance(full_name, str):
        full_name = full_name.strip()
    if not full_name or full_name.startswith(
        ("google_", "Facebook_", "LoginWithAmazon_", "SignInWithApple_")
    ):
        full_name = email.split("@", 1)[0]
    return full_name
