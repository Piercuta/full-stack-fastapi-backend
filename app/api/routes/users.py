import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import col, delete, func, select

from app import crud
from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models import (
    AvatarResponse,
    Item,
    Message,
    UpdatePassword,
    User,
    UserCreate,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.services.media_queue import publish_media_uploaded

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UsersPublic,
)
def read_users(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    """
    Retrieve users.
    """

    count_statement = select(func.count()).select_from(User)
    count = session.exec(count_statement).one()

    statement = select(User).offset(skip).limit(limit)
    users = session.exec(statement).all()

    return UsersPublic(data=users, count=count)


@router.post(
    "/", dependencies=[Depends(get_current_active_superuser)], response_model=UserPublic
)
def create_user(*, session: SessionDep, user_in: UserCreate) -> Any:
    """
    Create new user.
    """
    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )

    user = crud.create_user(session=session, user_create=user_in)
    if settings.emails_enabled and user_in.email:
        email_data = generate_new_account_email(
            email_to=user_in.email, username=user_in.email, password=user_in.password
        )
        send_email(
            email_to=user_in.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return user


@router.patch("/me", response_model=UserPublic)
def update_user_me(
    *, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser
) -> Any:
    """
    Update own user.
    """

    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.patch("/me/password", response_model=Message)
def update_password_me(
    *, session: SessionDep, body: UpdatePassword, current_user: CurrentUser
) -> Any:
    """
    Update own password.
    """
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect password")
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=400, detail="New password cannot be the same as the current one"
        )
    hashed_password = get_password_hash(body.new_password)
    current_user.hashed_password = hashed_password
    session.add(current_user)
    session.commit()
    return Message(message="Password updated successfully")


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
    """
    Get current user.
    """
    return current_user


@router.delete("/me", response_model=Message)
def delete_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    """
    Delete own user.
    """
    if current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    session.delete(current_user)
    session.commit()
    return Message(message="User deleted successfully")


@router.post("/signup", response_model=UserPublic)
def register_user(session: SessionDep, user_in: UserRegister) -> Any:
    """
    Create new user without the need to be logged in.
    """
    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system",
        )
    user_create = UserCreate.model_validate(user_in)
    user = crud.create_user(session=session, user_create=user_create)
    return user


@router.get("/{user_id}", response_model=UserPublic)
def read_user_by_id(
    user_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> Any:
    """
    Get a specific user by id.
    """
    user = session.get(User, user_id)
    if user == current_user:
        return user
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="The user doesn't have enough privileges",
        )
    return user


@router.patch(
    "/{user_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
)
def update_user(
    *,
    session: SessionDep,
    user_id: uuid.UUID,
    user_in: UserUpdate,
) -> Any:
    """
    Update a user.
    """

    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )

    db_user = crud.update_user(session=session, db_user=db_user, user_in=user_in)
    return db_user


@router.delete("/{user_id}", dependencies=[Depends(get_current_active_superuser)])
def delete_user(
    session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID
) -> Message:
    """
    Delete a user.
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user == current_user:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    statement = delete(Item).where(col(Item.owner_id) == user_id)
    session.exec(statement)  # type: ignore
    session.delete(user)
    session.commit()
    return Message(message="User deleted successfully")


# Avatar Management Routes

@router.post("/me/avatar", response_model=AvatarResponse)
def upload_avatar(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    file: UploadFile = File(...)
) -> Any:
    """
    Upload user avatar.
    """
    # Validate file type
    if file.content_type not in settings.ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed types: {', '.join(settings.ALLOWED_AVATAR_TYPES)}"
        )

    # Validate file size
    file_content = file.file.read()
    if len(file_content) > settings.MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.MAX_AVATAR_SIZE // (1024 * 1024)}MB"
        )

    # Reset file pointer
    file.file.seek(0)

    try:
        # Upload to file service
        with httpx.Client() as client:
            files = {"file": (file.filename, file.file, file.content_type)}
            data = {"folder": "avatars"}

            response = client.post(
                f"{settings.FILE_SERVICE_URL}/upload",
                files=files,
                data=data,
                timeout=30.0
            )
            response.raise_for_status()

            upload_result = response.json()
            cloudfront_url = upload_result.get("cloudfront_url")
            file_id = upload_result.get("file_id")
            s3_key = upload_result.get("s3_key")

            if not cloudfront_url or not file_id:
                raise HTTPException(
                    status_code=500,
                    detail="Invalid response from file service"
                )

            # Update user avatar URL in database
            current_user.avatar_url = cloudfront_url
            session.add(current_user)
            session.commit()
            session.refresh(current_user)

            if s3_key:
                from app.models import MediaJob, MediaJobStatus

                job = MediaJob(
                    owner_id=current_user.id,
                    status=MediaJobStatus.queued,
                    original_s3_key=s3_key,
                    original_url=cloudfront_url,
                    content_type=file.content_type,
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                publish_media_uploaded(
                    s3_key=s3_key,
                    content_type=file.content_type,
                    user_id=str(current_user.id),
                    job_id=str(job.id),
                )

            return AvatarResponse(
                avatar_url=cloudfront_url,
                message="Avatar uploaded successfully"
            )

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"File service error: {e.response.text}"
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to connect to file service: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar upload failed: {str(e)}"
        )


@router.get("/me/avatar", response_model=AvatarResponse)
def get_avatar(current_user: CurrentUser) -> Any:
    """
    Get current user's avatar URL.
    """
    if not current_user.avatar_url:
        raise HTTPException(
            status_code=404,
            detail="No avatar found for this user"
        )

    return AvatarResponse(
        avatar_url=current_user.avatar_url,
        message="Avatar retrieved successfully"
    )


@router.delete("/me/avatar", response_model=Message)
def delete_avatar(
    *,
    session: SessionDep,
    current_user: CurrentUser
) -> Any:
    """
    Delete user's avatar.
    """
    if not current_user.avatar_url:
        raise HTTPException(
            status_code=404,
            detail="No avatar found for this user"
        )

    try:
        # Extraire le file_id de l'URL CloudFront
        # Format: https://d1n4zytf7ed6nm.cloudfront.net/files/{file_id}
        url_parts = current_user.avatar_url.split('/')
        file_id = url_parts[-1]  # Dernière partie de l'URL

        # Appeler l'API de file-service pour supprimer le fichier
        with httpx.Client() as client:
            response = client.delete(
                f"{settings.FILE_SERVICE_URL}/files/{file_id}",
                timeout=30.0
            )
            response.raise_for_status()

        # Mettre à jour la base de données
        current_user.avatar_url = None
        session.add(current_user)
        session.commit()

        return Message(message="Avatar deleted successfully")

    except httpx.HTTPStatusError as e:
        # Si le fichier n'existe plus sur S3, on continue quand même
        if e.response.status_code == 404:
            current_user.avatar_url = None
            session.add(current_user)
            session.commit()
            return Message(message="Avatar deleted successfully (file was already removed)")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"File service error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar deletion failed: {str(e)}"
        )
