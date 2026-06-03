import json
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db, init_db
from .ganymede_client import GanymedeClient, GanymedeClientError
from .jobs import (
    channel_matches_tracked,
    create_or_update_job,
    extract_ganymede_archive_message,
    extract_webhook_ids,
    vod_value,
)
from .logging_config import configure_logging
from .models import JobStatus, UploadJob
from .notifications import send_job_notification
from .schemas import HealthRead, JobRead, WebhookAccepted
from .security import (
    verify_webhook_secret,
    verify_webhook_url_secret,
    webhook_secret_header,
)
from .ui import router as ui_router
from .ui_auth import has_admin_user
from .ui_settings import build_effective_settings
from .worker import resume_jobs_after_startup, run_cleanup_sync, run_job_sync, run_verify_sync
from .youtube_client import YouTubeClient, YouTubeClientError

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
    resume_jobs_after_startup()


def get_job_or_404(session: Session, job_id: int) -> UploadJob:
    job = session.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def process_job_background(job_id: int) -> None:
    run_job_sync(job_id)


def make_youtube_client(settings: Settings) -> YouTubeClient:
    return YouTubeClient(
        settings.youtube_client_secret_file,
        settings.youtube_token_file,
        settings.upload_chunk_size_bytes,
    )


@app.get("/health", response_model=HealthRead)
async def health(session: DBSession) -> HealthRead:
    components: dict[str, str] = {}
    try:
        session.execute(text("select 1"))
        components["db"] = "ok"
    except Exception as exc:
        components["db"] = f"error: {exc}"
    try:
        components["ui"] = "ok" if has_admin_user(session) else "setup_required"
    except Exception as exc:
        components["ui"] = f"error: {exc}"

    settings = build_effective_settings(session)
    try:
        await GanymedeClient(settings.ganymede_base_url, settings.ganymede_api_key).healthcheck()
        components["ganymede"] = "ok"
    except Exception as exc:
        components["ganymede"] = f"error: {exc}"
    try:
        youtube = make_youtube_client(settings)
        youtube._credentials()
        components["youtube"] = "ok"
    except YouTubeClientError as exc:
        components["youtube"] = f"error: {exc}"
    except Exception as exc:
        components["youtube"] = f"error: {exc}"

    status = "ok" if all(value == "ok" for value in components.values()) else "degraded"
    return HealthRead(status=status, components=components)


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
        if job.status not in {JobStatus.COMPLETED, JobStatus.UPLOADING, JobStatus.SKIPPED}:
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
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
) -> UploadJob:
    job = get_job_or_404(session, job_id)
    if job.status == JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Completed jobs cannot be retried")
    if job.status == JobStatus.NEEDS_MANUAL_REVIEW:
        raise HTTPException(
            status_code=409,
            detail="Manual review jobs cannot be retried automatically",
        )
    job.status = JobStatus.RECEIVED
    job.last_error = None
    session.commit()
    session.refresh(job)
    background_tasks.add_task(process_job_background, job.id)
    return job


@app.post("/jobs/{job_id}/skip", response_model=JobRead)
async def skip_job(job_id: int, session: DBSession, settings: AppSettings) -> UploadJob:
    job = get_job_or_404(session, job_id)
    if job.status == JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Completed jobs cannot be skipped")
    job.status = JobStatus.SKIPPED
    job.last_error = "Skipped by admin"
    session.commit()
    session.refresh(job)
    await send_job_notification(build_effective_settings(session), job, "skipped")
    return job


@app.post("/jobs/{job_id}/verify", response_model=JobRead)
async def verify_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
) -> UploadJob:
    job = get_job_or_404(session, job_id)
    if job.status == JobStatus.SKIPPED:
        raise HTTPException(status_code=409, detail="Skipped jobs cannot be verified")
    if not job.youtube_video_id:
        raise HTTPException(status_code=409, detail="Verification requires a YouTube video id")
    job.status = JobStatus.VERIFYING_YOUTUBE
    job.last_error = None
    session.commit()
    session.refresh(job)
    background_tasks.add_task(run_verify_sync, job.id)
    return job


@app.post("/jobs/{job_id}/cleanup", response_model=JobRead)
async def cleanup_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
) -> UploadJob:
    job = get_job_or_404(session, job_id)
    if job.status != JobStatus.VERIFIED:
        raise HTTPException(status_code=409, detail="Cleanup requires verified YouTube processing")
    job.status = JobStatus.CLEANING_GANYMEDE
    job.last_error = None
    session.commit()
    session.refresh(job)
    background_tasks.add_task(run_cleanup_sync, job.id)
    return job


app.include_router(ui_router)
