"""Fire-and-forget SQS notification after a media upload."""

import json
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)


def publish_media_uploaded(
    *,
    s3_key: str,
    content_type: str | None,
    user_id: str | None = None,
    job_id: str | None = None,
) -> None:
    if not settings.MEDIA_QUEUE_URL:
        return

    body = {
        "s3Key": s3_key,
        "contentType": content_type,
        "userId": user_id,
        "jobId": job_id,
    }
    try:
        client = boto3.client("sqs", region_name=settings.AWS_REGION)
        client.send_message(
            QueueUrl=settings.MEDIA_QUEUE_URL,
            MessageBody=json.dumps(body),
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Could not enqueue media variant job for %s: %s", s3_key, exc)
