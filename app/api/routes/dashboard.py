"""Dashboard aggregate stats for the home page."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import boto3
import httpx
from fastapi import APIRouter
from sqlalchemy import Date, cast, func
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep
from app.core.cache import cache_get, cache_set
from app.core.config import settings
from app.models import DashboardSeriesPoint, DashboardStats, Item, MediaJob, MediaJobStatus, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DASHBOARD_CACHE_KEY = f"dashboard:stats:{settings.ENVIRONMENT}"


def _sqs_queue_depth() -> tuple[int, int]:
    """Return (pending visible, failed approx). Failed stays 0 without a DLQ URL."""
    if not settings.MEDIA_QUEUE_URL:
        return 0, 0
    try:
        client = boto3.client("sqs", region_name=settings.AWS_REGION)
        response = client.get_queue_attributes(
            QueueUrl=settings.MEDIA_QUEUE_URL,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )
        attrs = response.get("Attributes") or {}
        visible = int(attrs.get("ApproximateNumberOfMessages", "0"))
        in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", "0"))
        return visible + in_flight, 0
    except Exception as exc:
        logger.warning("SQS queue depth unavailable: %s", exc)
        return 0, 0


def _file_service_healthy() -> bool:
    base = (settings.FILE_SERVICE_URL or "").rstrip("/")
    if not base:
        return True
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{base}/health")
            return response.status_code < 500
    except Exception:
        return False


def _compute_dashboard_stats(session: SessionDep) -> DashboardStats:
    users = session.exec(select(func.count()).select_from(User)).one()
    items = session.exec(select(func.count()).select_from(Item)).one()
    avatars = session.exec(
        select(func.count()).select_from(User).where(User.avatar_url != None)  # noqa: E711
    ).one()

    jobs_pending = session.exec(
        select(func.count())
        .select_from(MediaJob)
        .where(MediaJob.status.in_([MediaJobStatus.queued, MediaJobStatus.processing]))
    ).one()
    jobs_failed = session.exec(
        select(func.count())
        .select_from(MediaJob)
        .where(MediaJob.status == MediaJobStatus.failed)
    ).one()
    api_healthy = _file_service_healthy()

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=6)
    rows = session.exec(
        select(cast(Item.created_at, Date), func.count())
        .where(Item.created_at >= datetime.combine(start, datetime.min.time(), timezone.utc))
        .group_by(cast(Item.created_at, Date))
        .order_by(cast(Item.created_at, Date))
    ).all()
    by_day: dict[date, int] = {}
    for day, count in rows:
        if isinstance(day, datetime):
            day = day.date()
        by_day[day] = int(count)

    series = [
        DashboardSeriesPoint(
            date=(start + timedelta(days=offset)).isoformat(),
            items=by_day.get(start + timedelta(days=offset), 0),
        )
        for offset in range(7)
    ]

    return DashboardStats(
        users=int(users),
        items=int(items),
        avatars=int(avatars),
        jobs_pending=int(jobs_pending),
        jobs_failed=int(jobs_failed),
        api_healthy=api_healthy,
        series=series,
    )


@router.get("/stats", response_model=DashboardStats)
def read_dashboard_stats(
    session: SessionDep,
    current_user: CurrentUser,
) -> DashboardStats:
    """
    Aggregate stats for the home dashboard.
    Chart series = items created per day (last 7 days UTC).
    Avatars = users with avatar_url set.
    Jobs pending/failed = MediaJob rows (queued/processing vs failed).

    When REDIS_URL is set, results are cached (TTL = DASHBOARD_CACHE_TTL_SECONDS).
    """
    _ = current_user

    cached = cache_get(DASHBOARD_CACHE_KEY)
    if cached:
        try:
            return DashboardStats.model_validate_json(cached)
        except Exception as exc:
            logger.warning("Invalid dashboard cache payload: %s", exc)

    stats = _compute_dashboard_stats(session)
    cache_set(
        DASHBOARD_CACHE_KEY,
        stats.model_dump_json(),
        settings.DASHBOARD_CACHE_TTL_SECONDS,
    )
    return stats
