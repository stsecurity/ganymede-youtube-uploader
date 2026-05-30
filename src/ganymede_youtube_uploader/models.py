from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class JobStatus(StrEnum):
    RECEIVED = "received"
    FETCHING_GANYMEDE_METADATA = "fetching_ganymede_metadata"
    WAITING_FOR_GANYMEDE_PROCESSING = "waiting_for_ganymede_processing"
    LOCKED = "locked"
    VALIDATING_FILE = "validating_file"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VERIFYING_YOUTUBE = "verifying_youtube"
    VERIFIED = "verified"
    CLEANING_GANYMEDE = "cleaning_ganymede"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_MANUAL_CLEANUP = "needs_manual_cleanup"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


class UploadJob(Base):
    __tablename__ = "upload_jobs"
    __table_args__ = (
        UniqueConstraint("ganymede_vod_id", name="uq_upload_jobs_ganymede_vod_id"),
        UniqueConstraint("twitch_external_vod_id", name="uq_upload_jobs_twitch_external_vod_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ganymede_vod_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    twitch_external_vod_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    local_mapped_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ganymede_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.RECEIVED, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
