"""Media upload + async variant jobs (SQS → media-worker)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.models import (
    MediaJob,
    MediaJobPublic,
    MediaJobsPublic,
    MediaJobStatus,
)
from app.services.media_queue import publish_media_uploaded

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/media", tags=["media"])


def _to_public(job: MediaJob) -> MediaJobPublic:
    urls: list[str] = []
    if job.result_urls:
        try:
            parsed = json.loads(job.result_urls)
            if isinstance(parsed, list):
                urls = [str(u) for u in parsed]
        except json.JSONDecodeError:
            urls = []
    return MediaJobPublic(
        id=job.id,
        status=job.status,
        original_s3_key=job.original_s3_key,
        original_url=job.original_url,
        content_type=job.content_type,
        result_urls=urls,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _variant_url(original_url: str, original_s3_key: str, variant_key: str) -> str:
    """Rebuild a public URL for a variant S3 key.

    file-service builds URLs as https://{CLOUDFRONT_DOMAIN}/{s3_key} where
    CLOUDFRONT_DOMAIN may already include a path prefix (e.g. host/media).
    """
    key = original_s3_key.lstrip("/")
    if key and original_url.endswith(key):
        return original_url[: -len(key)] + variant_key.lstrip("/")
    parts = urlsplit(original_url)
    # Fallback: keep path directory of the original object.
    path = parts.path.rsplit("/", 1)[0] if "/" in parts.path else ""
    variant_name = variant_key.lstrip("/").rsplit("/", 1)[-1]
    return urlunsplit(
        (parts.scheme, parts.netloc, f"{path}/{variant_name}", "", "")
    )


def _create_job_and_enqueue(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    file: UploadFile,
    folder: str,
) -> MediaJob:
    if file.content_type not in settings.ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed types: {', '.join(settings.ALLOWED_AVATAR_TYPES)}",
        )

    file_content = file.file.read()
    if len(file_content) > settings.MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.MAX_AVATAR_SIZE // (1024 * 1024)}MB",
        )
    file.file.seek(0)

    try:
        with httpx.Client() as client:
            files = {"file": (file.filename, file.file, file.content_type)}
            response = client.post(
                f"{settings.FILE_SERVICE_URL}/upload",
                params={"folder": folder},
                files=files,
                timeout=30.0,
            )
            response.raise_for_status()
            upload_result = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"File service error: {exc.response.text}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to connect to file service: {exc}",
        ) from exc

    cloudfront_url = upload_result.get("cloudfront_url")
    s3_key = upload_result.get("s3_key")
    if not cloudfront_url or not s3_key:
        raise HTTPException(status_code=500, detail="Invalid response from file service")

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
    return job


@router.post("/upload", response_model=MediaJobPublic)
def upload_media(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> Any:
    """Upload an image and enqueue async variant generation."""
    job = _create_job_and_enqueue(
        session=session,
        current_user=current_user,
        file=file,
        folder="media",
    )
    return _to_public(job)


@router.get("/jobs", response_model=MediaJobsPublic)
def list_media_jobs(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
) -> Any:
    count = session.exec(
        select(func.count())
        .select_from(MediaJob)
        .where(MediaJob.owner_id == current_user.id)
    ).one()
    jobs = session.exec(
        select(MediaJob)
        .where(MediaJob.owner_id == current_user.id)
        .order_by(col(MediaJob.created_at).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return MediaJobsPublic(data=[_to_public(j) for j in jobs], count=int(count))


@router.get("/jobs/{job_id}", response_model=MediaJobPublic)
def get_media_job(
    job_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    job = session.get(MediaJob, job_id)
    if not job or job.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_public(job)


@router.api_route(
    "/jobs/{job_id}/status",
    methods=["POST", "PATCH"],
    response_model=MediaJobPublic,
)
def update_media_job_status(
    job_id: uuid.UUID,
    session: SessionDep,
    status: MediaJobStatus = Query(...),
    error: str | None = Query(None),
    variant_keys: list[str] = Query(default=[]),
    x_media_worker_secret: str | None = Header(default=None),
) -> Any:
    """Internal callback used by media-worker (optional shared secret).

    Accepts query params (preferred) so HTTP clients / OTEL agents that drop
    request bodies still work: ?status=done&variant_keys=a&variant_keys=b
    """
    expected = settings.MEDIA_WORKER_SECRET
    if expected and x_media_worker_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid worker secret")

    job = session.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = status
    job.error = error
    job.updated_at = datetime.now(timezone.utc)
    keys = variant_keys or []
    if keys:
        job.result_urls = json.dumps(
            [
                _variant_url(job.original_url, job.original_s3_key, key)
                for key in keys
            ]
        )
    session.add(job)
    session.commit()
    session.refresh(job)
    return _to_public(job)
