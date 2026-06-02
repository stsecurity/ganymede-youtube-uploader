import asyncio
import json
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db, init_db
from .ganymede_client import GanymedeClient, GanymedeClientError
from .jobs import (
    JobProcessor,
    channel_matches_tracked,
    create_or_update_job,
    extract_ganymede_archive_message,
    extract_webhook_ids,
    vod_value,
)
from .logging_config import configure_logging
from .models import JobStatus, UploadJob
from .schemas import HealthRead, JobRead, WebhookAccepted
from .security import (
    verify_webhook_secret,
    verify_webhook_url_secret,
    webhook_secret_header,
)
from .ui import router as ui_router
from .ui_settings import build_effective_settings

configure_logging()
app = FastAPI(title="ganymede-youtube-uploader")
DBSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
WebhookSecret = Annotated[str | None, Depends(webhook_secret_header)]


@dataclass(frozen=True)
class WebhookVodRef:
    ganymede_vod_id: str | None
    external_id: str | None
    title: str | None


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_job_or_404(session: Session, job_id: int) -> UploadJob:
    job = session.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def process_job_background(job_id: int) -> None:
    asyncio.run(process_job_background_async(job_id))


async def process_job_background_async(job_id: int) -> None:
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
async def ganymede_webhook_with_header(
    request: Request,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
    provided_secret: WebhookSecret,
    url_secret: str | None = Query(default=None, alias="secret"),
) -> WebhookAccepted:
    verify_webhook_secret(settings, provided_secret, url_secret)
    return await handle_ganymede_webhook(request, background_tasks, session, settings)


@app.post("/webhooks/ganymede/{url_secret}", response_model=WebhookAccepted)
async def ganymede_webhook(
    url_secret: str,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
    request: Request,
) -> WebhookAccepted:
    verify_webhook_url_secret(settings, url_secret)
    return await handle_ganymede_webhook(request, background_tasks, session, settings)


async def handle_ganymede_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session,
    settings: Settings,
) -> WebhookAccepted:
    payload = await parse_webhook_body(request)
    effective_settings = build_effective_settings(session)
    vod_refs = await resolve_webhook_job_fields(payload, effective_settings)
    if not vod_refs:
        raise HTTPException(status_code=422, detail="Webhook did not identify a Ganymede VOD")
    return await enqueue_webhook_jobs(background_tasks, session, vod_refs)


async def parse_webhook_body(request: Request) -> dict[str, Any] | str:
    body = await request.body()
    if not body:
        return {}
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace")
    return decoded if isinstance(decoded, dict) else str(decoded)


async def resolve_webhook_job_fields(
    payload: dict[str, Any] | str,
    settings: Settings,
) -> list[WebhookVodRef]:
    if isinstance(payload, dict):
        ganymede_vod_id, external_id, title = extract_webhook_ids(payload)
        if ganymede_vod_id or external_id:
            return [WebhookVodRef(ganymede_vod_id, external_id, title)]
    message_title, message_channel = extract_ganymede_archive_message(payload)
    if not message_title or not message_channel:
        return []
    if not channel_matches_tracked(message_channel, settings.tracked_twitch_channel):
        raise HTTPException(
            status_code=202, detail="Webhook channel does not match tracked channel"
        )
    try:
        vods = await GanymedeClient(
            settings.ganymede_base_url, settings.ganymede_api_key
        ).find_vods_by_title_and_channel(message_title, message_channel)
    except GanymedeClientError as exc:
        raise HTTPException(
            status_code=202,
            detail=f"Webhook accepted, but no Ganymede VOD could be resolved: {exc}",
        ) from exc
    if not vods:
        raise HTTPException(
            status_code=202,
            detail="Webhook accepted, but no Ganymede VOD matched the title and channel",
        )
    return [vod_ref_from_ganymede_vod(vod, fallback_title=message_title) for vod in vods]


def vod_ref_from_ganymede_vod(
    vod: dict[str, Any], fallback_title: str | None = None
) -> WebhookVodRef:
    return WebhookVodRef(
        ganymede_vod_id=str(vod_value(vod, "id", "vod_id", "vodId") or "") or None,
        external_id=str(
            vod_value(
                vod,
                "external_id",
                "externalId",
                "twitch_vod_id",
                "twitchVodId",
                "ext_id",
                "extId",
            )
            or ""
        )
        or None,
        title=vod_value(vod, "title") or fallback_title,
    )


async def enqueue_webhook_jobs(
    background_tasks: BackgroundTasks,
    session: Session,
    vod_refs: list[WebhookVodRef],
) -> WebhookAccepted:
    jobs = []
    for vod_ref in vod_refs:
        if not vod_ref.ganymede_vod_id and not vod_ref.external_id:
            continue
        jobs.append(
            create_or_update_job(
                session,
                ganymede_vod_id=vod_ref.ganymede_vod_id,
                external_id=vod_ref.external_id,
                title=vod_ref.title,
            )
        )
    if not jobs:
        raise HTTPException(status_code=422, detail="Webhook did not identify a Ganymede VOD")
    for job in jobs:
        if job.status not in {JobStatus.COMPLETED, JobStatus.UPLOADING}:
            background_tasks.add_task(process_job_background, job.id)
    return WebhookAccepted(
        job_id=jobs[0].id,
        job_ids=[job.id for job in jobs],
        status=jobs[0].status,
    )


async def enqueue_webhook_job(
    background_tasks: BackgroundTasks,
    session: Session,
    ganymede_vod_id: str | None,
    external_id: str | None,
    title: str | None,
) -> WebhookAccepted:
    try:
        return await enqueue_webhook_jobs(
            background_tasks,
            session,
            [WebhookVodRef(ganymede_vod_id, external_id, title)],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
