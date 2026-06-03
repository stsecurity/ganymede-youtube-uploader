import pytest

from ganymede_youtube_uploader.config import Settings
from ganymede_youtube_uploader.models import JobStatus, UploadJob
from ganymede_youtube_uploader.notifications import (
    build_job_notification_payload,
    build_test_notification_payload,
    send_job_notification,
)


def test_build_job_notification_payload_for_rocketchat() -> None:
    job = UploadJob(
        id=7,
        title="A Good VOD",
        ganymede_vod_id="vod-1",
        youtube_video_id="yt-1",
        status=JobStatus.COMPLETED,
    )

    payload = build_job_notification_payload(job, "completed")

    assert set(payload) == {"text"}
    assert "Upload completed: A Good VOD" in payload["text"]
    assert "Job: #7" in payload["text"]
    assert "Ganymede VOD: vod-1" in payload["text"]
    assert "YouTube video: yt-1" in payload["text"]


def test_build_test_notification_payload_for_rocketchat() -> None:
    payload = build_test_notification_payload()

    assert payload == {"text": "Ganymede YouTube Uploader test notification."}


@pytest.mark.asyncio
async def test_send_job_notification_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    class FakeAsyncClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, json: dict[str, str]):
            nonlocal called
            called = True

    monkeypatch.setattr(
        "ganymede_youtube_uploader.notifications.httpx.AsyncClient", FakeAsyncClient
    )

    await send_job_notification(
        Settings(webhook_notifications_enabled=False), UploadJob(id=1), "completed"
    )

    assert called is False


@pytest.mark.asyncio
async def test_send_job_notification_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, json: dict[str, str]) -> FakeResponse:
            posts.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(
        "ganymede_youtube_uploader.notifications.httpx.AsyncClient", FakeAsyncClient
    )
    settings = Settings(
        webhook_notifications_enabled=True,
        webhook_notification_url="http://rocketchat/hooks/test",
    )
    job = UploadJob(id=1, title="Done", status=JobStatus.COMPLETED)

    await send_job_notification(settings, job, "completed")

    assert posts == [
        (
            "http://rocketchat/hooks/test",
            {"text": "Upload completed: Done\nJob: #1\nStatus: completed"},
        )
    ]
