import logging
import secrets
from datetime import timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError

from app import crud
from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.core import security
from app.core.cognito import (
    cognito_configured,
    exchange_code_for_tokens,
    validate_id_token,
)
from app.core.config import settings
from app.core.security import get_password_hash
from app.models import CognitoLogin, Message, NewPassword, Token, UserCreate, UserPublic
from app.utils import (
    generate_password_reset_token,
    generate_reset_password_email,
    send_email,
    verify_password_reset_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["login"])


@router.post("/login/access-token")
def login_access_token(
    session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    user = crud.authenticate(
        session=session, email=form_data.username, password=form_data.password
    )
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )


@router.post("/login/cognito", response_model=Token)
def login_cognito(session: SessionDep, body: CognitoLogin) -> Token:
    """
    Exchange a Cognito Hosted UI authorization code for an application JWT (flow B).
    """
    if not cognito_configured():
        raise HTTPException(status_code=501, detail="Cognito login is not configured")

    try:
        tokens = exchange_code_for_tokens(
            code=body.code,
            redirect_uri=body.redirect_uri,
            code_verifier=body.code_verifier,
        )
    except httpx.HTTPError as exc:
        logger.warning("Cognito token exchange error: %s", exc)
        raise HTTPException(
            status_code=400, detail="Failed to exchange Cognito authorization code"
        ) from exc

    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="Cognito response missing id_token")

    try:
        claims = validate_id_token(id_token)
    except (InvalidTokenError, ValueError, KeyError) as exc:
        logger.warning("Invalid Cognito id_token: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid Cognito ID token") from exc

    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Cognito token has no email claim")
    if claims.get("email_verified") is False:
        raise HTTPException(status_code=400, detail="Cognito email is not verified")

    full_name = claims.get("name") or claims.get("cognito:username")
    user = crud.get_user_by_email(session=session, email=email)
    if not user:
        user = crud.create_user(
            session=session,
            user_create=UserCreate(
                email=email,
                password=secrets.token_urlsafe(24),
                full_name=full_name,
                is_active=True,
                is_superuser=False,
            ),
        )
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    elif full_name and not user.full_name:
        user.full_name = full_name
        session.add(user)
        session.commit()
        session.refresh(user)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )

# @router.post("/login/access-token")
# async def login_access_token(
#     session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
# ) -> Token:
#     """
#     OAuth2 compatible token login, get an access token for future requests
#     """
#     user = await run_in_threadpool(
#         crud.authenticate,
#         session=session,
#         email=form_data.username,
#         password=form_data.password
#     )
#     if not user:
#         raise HTTPException(status_code=400, detail="Incorrect email or password")
#     elif not user.is_active:
#         raise HTTPException(status_code=400, detail="Inactive user")

#     access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
#     token = await run_in_threadpool(
#         security.create_access_token,
#         user.id,
#         expires_delta=access_token_expires
#     )

#     return Token(access_token=token)


@router.post("/login/test-token", response_model=UserPublic)
def test_token(current_user: CurrentUser) -> Any:
    """
    Test access token
    """
    return current_user


@router.post("/password-recovery/{email}")
def recover_password(email: str, session: SessionDep) -> Message:
    """
    Password Recovery
    """
    user = crud.get_user_by_email(session=session, email=email)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this email does not exist in the system.",
        )
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )
    send_email(
        email_to=user.email,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Password recovery email sent")


@router.post("/reset-password/")
def reset_password(session: SessionDep, body: NewPassword) -> Message:
    """
    Reset password
    """
    email = verify_password_reset_token(token=body.token)
    if not email:
        raise HTTPException(status_code=400, detail="Invalid token")
    user = crud.get_user_by_email(session=session, email=email)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this email does not exist in the system.",
        )
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    hashed_password = get_password_hash(password=body.new_password)
    user.hashed_password = hashed_password
    session.add(user)
    session.commit()
    return Message(message="Password updated successfully")


@router.post(
    "/password-recovery-html-content/{email}",
    dependencies=[Depends(get_current_active_superuser)],
    response_class=HTMLResponse,
)
def recover_password_html_content(email: str, session: SessionDep) -> Any:
    """
    HTML Content for Password Recovery
    """
    user = crud.get_user_by_email(session=session, email=email)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system.",
        )
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )

    return HTMLResponse(
        content=email_data.html_content, headers={"subject:": email_data.subject}
    )
