from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db, init_db
from .jobs import JobProcessor, create_or_update_job, extract_webhook_ids
from .logging_config import configure_logging
from .models import JobStatus, UploadJob
from .schemas import HealthRead, JobRead, WebhookAccepted
from .security import verify_webhook_secret, webhook_secret_header
from .ui import router as ui_router
from .ui_settings import build_effective_settings

configure_logging()
app = FastAPI(title="ganymede-youtube-uploader")
DBSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
WebhookSecret = Annotated[str | None, Depends(webhook_secret_header)]


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_job_or_404(session: Session, job_id: int) -> UploadJob:
    job = session.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def process_job_background(job_id: int) -> None:
    from .db import SessionLocal

    session = SessionLocal()
    try:
        job = get_job_or_404(session, job_id)
        await JobProcessor(build_effective_settings(session)).process(session, job)
    finally:
        session.close()


@app.get("/health", response_model=HealthRead)
def health() -> HealthRead:
    return HealthRead(status="ok")


@app.post("/webhooks/ganymede", response_model=WebhookAccepted)
async def ganymede_webhook(
    payload: dict,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
    provided_secret: WebhookSecret,
) -> WebhookAccepted:
    verify_webhook_secret(settings, provided_secret)
    ganymede_vod_id, external_id, title = extract_webhook_ids(payload)
    try:
        job = create_or_update_job(
            session,
            ganymede_vod_id=ganymede_vod_id,
            external_id=external_id,
            title=title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if job.status not in {JobStatus.COMPLETED, JobStatus.UPLOADING}:
        background_tasks.add_task(process_job_background, job.id)
    return WebhookAccepted(job_id=job.id, status=job.status)


@app.get("/jobs", response_model=list[JobRead])
def list_jobs(session: DBSession) -> list[UploadJob]:
    return list(session.scalars(select(UploadJob).order_by(UploadJob.created_at.desc()).limit(100)))


@app.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: int, session: DBSession) -> UploadJob:
    return get_job_or_404(session, job_id)


@app.post("/jobs/{job_id}/retry", response_model=JobRead)
async def retry_job(
    job_id: int,
    session: DBSession,
    settings: AppSettings,
) -> UploadJob:
    job = get_job_or_404(session, job_id)
    if job.status == JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Completed jobs cannot be retried")
    job.status = JobStatus.RECEIVED
    job.last_error = None
    session.commit()
    return await JobProcessor(build_effective_settings(session)).process(session, job)


@app.post("/jobs/{job_id}/verify", response_model=JobRead)
async def verify_job(
    job_id: int,
    session: DBSession,
    settings: AppSettings,
) -> UploadJob:
    job = get_job_or_404(session, job_id)
    return await JobProcessor(build_effective_settings(session)).verify_and_cleanup(session, job)


@app.post("/jobs/{job_id}/cleanup", response_model=JobRead)
async def cleanup_job(
    job_id: int,
    session: DBSession,
    settings: AppSettings,
) -> UploadJob:
    job = get_job_or_404(session, job_id)
    if job.status != JobStatus.VERIFIED:
        raise HTTPException(status_code=409, detail="Cleanup requires verified YouTube processing")
    return await JobProcessor(build_effective_settings(session)).cleanup_only(session, job)


app.include_router(ui_router)
