from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .models import JobStatus


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ganymede_vod_id: str | None
    twitch_external_vod_id: str | None
    title: str | None
    local_mapped_path: str | None
    file_size: int | None
    local_duration: int | None
    ganymede_duration: int | None
    youtube_video_id: str | None
    status: JobStatus
    attempt_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class WebhookAccepted(BaseModel):
    job_id: int
    status: JobStatus


class HealthRead(BaseModel):
    status: str


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    vod_id: str | None = None
    id: str | None = None
    external_id: str | None = None
    twitch_vod_id: str | None = None
    title: str | None = None
    data: dict[str, Any] | None = None
