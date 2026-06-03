import logging
from typing import Any

import httpx

from .config import Settings
from .models import UploadJob

LOGGER = logging.getLogger(__name__)

EVENT_LABELS = {
    "completed": "Upload completed",
    "failed": "Upload failed",
    "cleanup_failed": "Upload verified, cleanup failed",
    "skipped": "Upload skipped",
}


def build_job_notification_text(job: UploadJob, event: str) -> str:
    label = EVENT_LABELS.get(event, event.replace("_", " ").title())
    lines = [
        f"{label}: {job.title or f'Job #{job.id}'}",
        f"Job: #{job.id}",
        f"Status: {job.status.value}",
    ]
    if job.ganymede_vod_id:
        lines.append(f"Ganymede VOD: {job.ganymede_vod_id}")
    if job.youtube_video_id:
        lines.append(f"YouTube video: {job.youtube_video_id}")
    if job.last_error:
        lines.append(f"Error: {job.last_error}")
    return "\n".join(lines)


def build_job_notification_payload(job: UploadJob, event: str) -> dict[str, Any]:
    return {"text": build_job_notification_text(job, event)}


async def send_job_notification(settings: Settings, job: UploadJob, event: str) -> None:
    if not settings.webhook_notifications_enabled or not settings.webhook_notification_url:
        return
    payload = build_job_notification_payload(job, event)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.post(settings.webhook_notification_url, json=payload)
            response.raise_for_status()
    except Exception as exc:
        LOGGER.warning("Webhook notification failed for job %s: %s", job.id, exc)
