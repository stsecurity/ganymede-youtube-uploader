from pathlib import Path
from typing import Any

import pytest

from ganymede_youtube_uploader.config import Settings
from ganymede_youtube_uploader.ganymede_client import GanymedeClientError
from ganymede_youtube_uploader.jobs import JobProcessor, create_or_update_job
from ganymede_youtube_uploader.models import JobStatus, UploadJob
from ganymede_youtube_uploader.youtube_client import YouTubeVerification


class FakeGanymede:
    def __init__(self, mount: Path) -> None:
        self.deleted: list[tuple[str, bool]] = []
        self.locked: list[tuple[str, bool]] = []
        self.vod = {
            "id": "vod-1",
            "external_id": "ext-1",
            "title": "Fixture",
            "status": "completed",
            "video_path": "/data/videos/video.mp4",
            "duration": 120,
        }

    async def get_vod(
        self, vod_id: str, with_channel: bool = True, with_queue: bool = True
    ) -> dict[str, Any]:
        return self.vod | {"id": vod_id}

    async def get_vod_by_external_id(
        self, external_id: str, with_channel: bool = True, with_queue: bool = True
    ) -> dict[str, Any]:
        return self.vod | {"external_id": external_id}

    async def lock_vod(self, vod_id: str, locked: bool = True) -> None:
        self.locked.append((vod_id, locked))

    async def delete_vod(self, vod_id: str, delete_files: bool = True) -> None:
        self.deleted.append((vod_id, delete_files))

    async def get_ffprobe(self, vod_id: str) -> dict[str, Any]:
        return {
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            "format": {"duration": "120"},
        }


class WrappedFakeGanymede(FakeGanymede):
    async def get_vod(
        self, vod_id: str, with_channel: bool = True, with_queue: bool = True
    ) -> dict[str, Any]:
        return {"success": True, "data": self.vod | {"id": vod_id}}


class CleanupFailingGanymede(FakeGanymede):
    async def delete_vod(self, vod_id: str, delete_files: bool = True) -> None:
        raise GanymedeClientError("delete endpoint failed")


class FakeYouTube:
    def __init__(self) -> None:
        self.uploads = 0
        self.last_upload: dict[str, Any] = {}

    def upload_video(self, *args: Any, **kwargs: Any) -> str:
        self.uploads += 1
        self.last_upload = kwargs
        return "yt-1"

    def wait_until_processed(self, *args: Any, **kwargs: Any) -> YouTubeVerification:
        return YouTubeVerification(True, {}, {"duration": "PT2M"})

    def update_privacy(self, video_id: str, privacy_status: str) -> None:
        pass


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        ganymede_videos_mount=tmp_path,
        ganymede_videos_root_in_ganymede="/data/videos",
        youtube_verify_interval_seconds=1,
    )


def test_webhook_idempotency(session) -> None:
    first = create_or_update_job(session, ganymede_vod_id="vod-1", external_id="ext-1")
    second = create_or_update_job(session, ganymede_vod_id="vod-1", external_id="ext-1")

    assert first.id == second.id
    assert session.query(UploadJob).count() == 1


@pytest.mark.asyncio
async def test_job_state_transition_to_completed(session, tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    fake_ganymede = FakeGanymede(tmp_path)
    fake_youtube = FakeYouTube()
    processor = JobProcessor(settings(tmp_path), fake_ganymede, fake_youtube)
    job = create_or_update_job(session, ganymede_vod_id="vod-1")

    result = await processor.process(session, job)

    assert result.status == JobStatus.COMPLETED
    assert result.youtube_video_id == "yt-1"
    assert fake_youtube.uploads == 1
    assert fake_ganymede.deleted == [("vod-1", True)]


@pytest.mark.asyncio
async def test_job_state_transition_handles_wrapped_ganymede_vod(session, tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    fake_ganymede = WrappedFakeGanymede(tmp_path)
    fake_youtube = FakeYouTube()
    processor = JobProcessor(settings(tmp_path), fake_ganymede, fake_youtube)
    job = create_or_update_job(session, ganymede_vod_id="vod-1")

    result = await processor.process(session, job)

    assert result.status == JobStatus.COMPLETED
    assert fake_youtube.uploads == 1


@pytest.mark.asyncio
async def test_cleanup_requires_verified_status(session, tmp_path: Path) -> None:
    fake_ganymede = FakeGanymede(tmp_path)
    processor = JobProcessor(settings(tmp_path), fake_ganymede, FakeYouTube())
    job = create_or_update_job(session, ganymede_vod_id="vod-1")

    with pytest.raises(ValueError):
        await processor.cleanup_only(session, job)

    assert fake_ganymede.deleted == []


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_error(session, tmp_path: Path) -> None:
    fake_ganymede = CleanupFailingGanymede(tmp_path)
    processor = JobProcessor(settings(tmp_path), fake_ganymede, FakeYouTube())
    job = create_or_update_job(session, ganymede_vod_id="vod-1")
    job.status = JobStatus.VERIFIED
    session.commit()

    result = await processor.cleanup_only(session, job)

    assert result.status == JobStatus.NEEDS_MANUAL_CLEANUP
    assert result.last_error == "Ganymede cleanup failed: delete endpoint failed"


@pytest.mark.asyncio
async def test_retry_does_not_reupload_when_video_id_exists(session, tmp_path: Path) -> None:
    fake_ganymede = FakeGanymede(tmp_path)
    fake_youtube = FakeYouTube()
    processor = JobProcessor(settings(tmp_path), fake_ganymede, fake_youtube)
    job = create_or_update_job(session, ganymede_vod_id="vod-1")
    job.youtube_video_id = "yt-existing"
    job.local_duration = 120
    job.status = JobStatus.UPLOADED
    session.commit()

    result = await processor.process(session, job)

    assert result.status == JobStatus.COMPLETED
    assert fake_youtube.uploads == 0
    assert fake_ganymede.deleted == [("vod-1", True)]


@pytest.mark.asyncio
async def test_youtube_title_option_and_description_are_used(session, tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    fake_ganymede = FakeGanymede(tmp_path)
    fake_ganymede.vod["title"] = "Ganymede Record Title"
    fake_youtube = FakeYouTube()
    configured = settings(tmp_path).model_copy(
        update={
            "youtube_title_option": "2",
            "youtube_description": "Configured upload description.",
        }
    )
    processor = JobProcessor(configured, fake_ganymede, fake_youtube)
    job = create_or_update_job(session, ganymede_vod_id="vod-1", title="Webhook Title")

    await processor.process(session, job)

    assert fake_youtube.last_upload["title"] == "Ganymede Record Title"
    assert fake_youtube.last_upload["description"] == "Configured upload description."
