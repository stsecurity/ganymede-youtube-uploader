import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .cleanup import cleanup_ganymede
from .config import Settings
from .ganymede_client import GanymedeClient, GanymedeClientError
from .media_probe import MediaProbeError, duration_within_tolerance, parse_ffprobe, run_ffprobe
from .models import JobStatus, UploadJob
from .path_resolver import GanymedePathResolver
from .youtube_client import YouTubeClient, parse_recording_date

LOGGER = logging.getLogger(__name__)
GANYMEDE_ARCHIVE_MESSAGE_RE = re.compile(
    r"Video Archived:\s*(?P<title>.+)\s+by\s+(?P<channel>.+?)\s*\.?\s*$",
    re.IGNORECASE,
)


TERMINAL_RETRYABLE = {
    JobStatus.FAILED,
    JobStatus.NEEDS_MANUAL_CLEANUP,
    JobStatus.NEEDS_MANUAL_REVIEW,
}


def extract_webhook_ids(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    ganymede_id = (
        payload.get("vod_id")
        or payload.get("vodId")
        or data.get("vod_id")
        or data.get("vodId")
        or payload.get("id")
        or data.get("id")
    )
    external_id = (
        payload.get("external_id")
        or payload.get("externalId")
        or payload.get("twitch_vod_id")
        or payload.get("twitchVodId")
        or data.get("external_id")
        or data.get("externalId")
        or data.get("twitch_vod_id")
        or data.get("twitchVodId")
    )
    title = payload.get("title") or data.get("title")
    return (
        str(ganymede_id) if ganymede_id else None,
        str(external_id) if external_id else None,
        title,
    )


def extract_ganymede_archive_message(
    payload: dict[str, Any] | str,
) -> tuple[str | None, str | None]:
    message = payload if isinstance(payload, str) else find_message_text(payload)
    if not message:
        return None, None
    match = GANYMEDE_ARCHIVE_MESSAGE_RE.search(message)
    if not match:
        return None, None
    return match.group("title").strip(), match.group("channel").strip()


def find_message_text(payload: dict[str, Any]) -> str | None:
    for key in ("message", "content", "text", "description"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    embeds = payload.get("embeds")
    if isinstance(embeds, list):
        for embed in embeds:
            if not isinstance(embed, dict):
                continue
            text = find_message_text(embed)
            if text:
                return text
    return None


def channel_matches_tracked(channel_name: str | None, tracked_channel: str) -> bool:
    if not tracked_channel:
        return True
    return normalized_name(channel_name) == normalized_name(tracked_channel)


def normalized_name(value: str | None) -> str:
    return (value or "").strip().casefold()


def create_or_update_job(
    session: Session,
    *,
    ganymede_vod_id: str | None = None,
    external_id: str | None = None,
    title: str | None = None,
) -> UploadJob:
    if not ganymede_vod_id and not external_id:
        raise ValueError("A Ganymede VOD id or Twitch external VOD id is required")
    query = select(UploadJob).where(
        or_(
            UploadJob.ganymede_vod_id == ganymede_vod_id if ganymede_vod_id else False,
            UploadJob.twitch_external_vod_id == external_id if external_id else False,
        )
    )
    job = session.scalar(query)
    if job is None:
        job = UploadJob(
            ganymede_vod_id=ganymede_vod_id,
            twitch_external_vod_id=external_id,
            title=title,
            status=JobStatus.RECEIVED,
        )
        session.add(job)
    else:
        if ganymede_vod_id and not job.ganymede_vod_id:
            job.ganymede_vod_id = ganymede_vod_id
        if external_id and not job.twitch_external_vod_id:
            job.twitch_external_vod_id = external_id
        if title and not job.title:
            job.title = title
    session.commit()
    session.refresh(job)
    return job


def vod_value(vod: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in vod and vod[key] is not None:
            return vod[key]
    return None


def unwrap_ganymede_record(data: dict[str, Any]) -> dict[str, Any]:
    wrapped = data.get("data")
    if isinstance(wrapped, dict):
        return wrapped
    return data


def is_ganymede_finished(vod: dict[str, Any]) -> bool:
    status = str(vod_value(vod, "status", "video_status", "videoStatus") or "").lower()
    processing = vod_value(vod, "processing", "is_processing", "isProcessing")
    if processing is True:
        return False
    if status and status not in {"done", "finished", "archived", "success", "completed"}:
        return False
    return True


def extract_video_path(vod: dict[str, Any]) -> str | None:
    return vod_value(vod, "video_path", "videoPath", "path", "file_path", "filePath")


def extract_duration(vod: dict[str, Any]) -> int | None:
    value = vod_value(vod, "duration", "duration_seconds", "durationSeconds")
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def build_youtube_title(job: UploadJob, vod: dict[str, Any], option: str) -> str:
    ganymede_title = vod_value(vod, "title")
    if option == "2":
        return str(ganymede_title or job.title or f"Ganymede VOD {job.ganymede_vod_id}")
    if option == "3":
        return f"Ganymede VOD {job.ganymede_vod_id or job.id}"
    if option == "4":
        return f"Upload Job {job.id}"
    return str(job.title or ganymede_title or f"Ganymede VOD {job.ganymede_vod_id or job.id}")


def fail_job(session: Session, job: UploadJob, exc: Exception) -> None:
    job.status = JobStatus.FAILED
    job.last_error = str(exc)
    session.commit()


class JobProcessor:
    def __init__(
        self,
        settings: Settings,
        ganymede_client: GanymedeClient | None = None,
        youtube_client: YouTubeClient | None = None,
    ) -> None:
        self.settings = settings
        self.ganymede = ganymede_client or GanymedeClient(
            settings.ganymede_base_url, settings.ganymede_api_key
        )
        self.youtube = youtube_client or YouTubeClient(
            settings.youtube_client_secret_file,
            settings.youtube_token_file,
            settings.upload_chunk_size_bytes,
        )
        self.resolver = GanymedePathResolver(
            settings.ganymede_videos_mount, settings.ganymede_videos_root_in_ganymede
        )

    async def process(self, session: Session, job: UploadJob) -> UploadJob:
        if job.status == JobStatus.COMPLETED:
            return job
        if job.youtube_video_id and job.status not in {
            JobStatus.COMPLETED,
            JobStatus.NEEDS_MANUAL_CLEANUP,
            JobStatus.NEEDS_MANUAL_REVIEW,
        }:
            return await self.verify_and_cleanup(session, job)
        job.attempt_count += 1
        try:
            await self._run(session, job)
        except Exception as exc:
            LOGGER.exception("Job %s failed", job.id)
            fail_job(session, job, exc)
        session.refresh(job)
        return job

    async def _run(self, session: Session, job: UploadJob) -> None:
        job.status = JobStatus.FETCHING_GANYMEDE_METADATA
        session.commit()
        vod = await self._fetch_vod(job)
        job.ganymede_vod_id = str(vod_value(vod, "id", "vod_id", "vodId") or job.ganymede_vod_id)
        job.twitch_external_vod_id = (
            str(
                vod_value(vod, "external_id", "externalId", "twitch_vod_id", "twitchVodId")
                or vod_value(vod, "ext_id", "extId")
                or job.twitch_external_vod_id
                or ""
            )
            or None
        )
        job.title = job.title or vod_value(vod, "title") or f"Ganymede VOD {job.ganymede_vod_id}"
        job.ganymede_duration = extract_duration(vod)
        if not is_ganymede_finished(vod):
            job.status = JobStatus.WAITING_FOR_GANYMEDE_PROCESSING
            job.last_error = "Ganymede VOD is still processing"
            session.commit()
            return

        try:
            await self.ganymede.lock_vod(job.ganymede_vod_id, locked=True)
            job.status = JobStatus.LOCKED
        except GanymedeClientError as exc:
            LOGGER.info("Ganymede lock unsupported or failed for job %s: %s", job.id, exc)
            job.status = JobStatus.LOCKED
        session.commit()

        job.status = JobStatus.VALIDATING_FILE
        session.commit()
        local_path, local_duration = await self._validate_file(job, vod)
        job.local_mapped_path = str(local_path)
        job.file_size = local_path.stat().st_size
        job.local_duration = local_duration
        session.commit()

        if job.youtube_video_id:
            job.status = JobStatus.UPLOADED
            session.commit()
            await self.verify_and_cleanup(session, job)
            return

        job.status = JobStatus.UPLOADING
        session.commit()
        job.youtube_video_id = self.youtube.upload_video(
            local_path,
            title=build_youtube_title(job, vod, self.settings.youtube_title_option),
            description=self.settings.youtube_description,
            tags=[],
            category_id=self.settings.youtube_category_id,
            privacy_status=self.settings.youtube_default_privacy,
            notify_subscribers=self.settings.youtube_notify_subscribers,
            recording_date=parse_recording_date(vod_value(vod, "created_at", "createdAt", "date")),
        )
        job.status = JobStatus.UPLOADED
        session.commit()
        await self.verify_and_cleanup(session, job)

    async def _fetch_vod(self, job: UploadJob) -> dict[str, Any]:
        if job.ganymede_vod_id:
            return unwrap_ganymede_record(await self.ganymede.get_vod(job.ganymede_vod_id))
        if job.twitch_external_vod_id:
            return unwrap_ganymede_record(
                await self.ganymede.get_vod_by_external_id(job.twitch_external_vod_id)
            )
        raise ValueError("Job has no Ganymede or external VOD id")

    async def _validate_file(self, job: UploadJob, vod: dict[str, Any]) -> tuple[Path, int]:
        path = extract_video_path(vod)
        if not path:
            raise ValueError("Ganymede VOD record did not include a video path")
        local_path = self.resolver.resolve(path)
        if not local_path.exists():
            raise FileNotFoundError(f"Video file does not exist: {local_path}")
        if not local_path.is_file():
            raise ValueError("Resolved video path is not a file")
        if local_path.stat().st_size <= 0:
            raise ValueError("Video file is empty")
        try:
            ganymede_probe = (
                await self.ganymede.get_ffprobe(job.ganymede_vod_id)
                if job.ganymede_vod_id
                else None
            )
            probe = parse_ffprobe(ganymede_probe) if ganymede_probe else run_ffprobe(local_path)
        except GanymedeClientError:
            probe = run_ffprobe(local_path)
        except MediaProbeError:
            raise
        if not probe.has_video:
            raise ValueError("Video file has no video stream")
        if self.settings.require_audio_stream and not probe.has_audio:
            raise ValueError("Video file has no audio stream")
        if not duration_within_tolerance(
            probe.duration_seconds, job.ganymede_duration, self.settings.duration_tolerance_seconds
        ):
            raise ValueError("Local duration differs from Ganymede duration")
        return local_path, probe.duration_seconds

    async def verify_and_cleanup(self, session: Session, job: UploadJob) -> UploadJob:
        if not job.youtube_video_id:
            raise ValueError("Cannot verify without a YouTube video id")
        job.status = JobStatus.VERIFYING_YOUTUBE
        session.commit()
        verification = self.youtube.wait_until_processed(
            job.youtube_video_id,
            job.local_duration or job.ganymede_duration,
            self.settings.duration_tolerance_seconds,
            self.settings.youtube_verify_timeout_minutes * 60,
            self.settings.youtube_verify_interval_seconds,
        )
        if not verification.succeeded:
            job.status = JobStatus.FAILED
            job.last_error = verification.error or "YouTube verification failed"
            session.commit()
            return job
        if self.settings.youtube_final_privacy:
            self.youtube.update_privacy(job.youtube_video_id, self.settings.youtube_final_privacy)
        job.status = JobStatus.VERIFIED
        job.last_error = None
        session.commit()
        job.status = JobStatus.CLEANING_GANYMEDE
        session.commit()
        job.status = await cleanup_ganymede(job, self.ganymede)
        session.commit()
        return job

    async def cleanup_only(self, session: Session, job: UploadJob) -> UploadJob:
        job.status = await cleanup_ganymede(job, self.ganymede)
        session.commit()
        return job
