from __future__ import annotations

import asyncio
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .jobs import JobProcessor
from .models import JobStatus, UploadJob
from .ui_settings import build_effective_settings

_RUNNING_JOB_IDS: set[int] = set()
_RUNNING_LOCK = threading.Lock()

RESUMABLE_STATUSES = {
    JobStatus.RECEIVED,
    JobStatus.FETCHING_GANYMEDE_METADATA,
    JobStatus.WAITING_FOR_GANYMEDE_PROCESSING,
    JobStatus.LOCKED,
    JobStatus.VALIDATING_FILE,
    JobStatus.UPLOADED,
    JobStatus.VERIFYING_YOUTUBE,
    JobStatus.VERIFIED,
    JobStatus.CLEANING_GANYMEDE,
}


def current_running_job_ids() -> list[int]:
    with _RUNNING_LOCK:
        return sorted(_RUNNING_JOB_IDS)


def run_job_sync(job_id: int) -> None:
    with _RUNNING_LOCK:
        if job_id in _RUNNING_JOB_IDS:
            return
        _RUNNING_JOB_IDS.add(job_id)
    try:
        asyncio.run(_run_job(job_id))
    finally:
        with _RUNNING_LOCK:
            _RUNNING_JOB_IDS.discard(job_id)


async def _run_job(job_id: int) -> None:
    session = SessionLocal()
    try:
        job = session.get(UploadJob, job_id)
        if job:
            await JobProcessor(build_effective_settings(session)).process(session, job)
    finally:
        session.close()


def start_job_thread(job_id: int) -> None:
    thread = threading.Thread(target=run_job_sync, args=(job_id,), daemon=True)
    thread.start()


def recover_interrupted_jobs(session: Session) -> list[int]:
    unsafe_uploads = list(
        session.scalars(
            select(UploadJob).where(
                UploadJob.status == JobStatus.UPLOADING,
                UploadJob.youtube_video_id.is_(None),
            )
        )
    )
    for job in unsafe_uploads:
        job.status = JobStatus.NEEDS_MANUAL_REVIEW
        job.last_error = (
            "Uploader restarted while upload was in progress and no YouTube video ID "
            "was saved. This job was not auto-resumed to avoid uploading the same VOD twice."
        )
    resumable = list(
        session.scalars(
            select(UploadJob).where(
                UploadJob.status.in_(RESUMABLE_STATUSES)
                | (
                    (UploadJob.status == JobStatus.UPLOADING)
                    & UploadJob.youtube_video_id.is_not(None)
                )
            )
        )
    )
    session.commit()
    return [job.id for job in resumable]


def resume_jobs_after_startup() -> list[int]:
    session = SessionLocal()
    try:
        job_ids = recover_interrupted_jobs(session)
    finally:
        session.close()
    for job_id in job_ids:
        start_job_thread(job_id)
    return job_ids
