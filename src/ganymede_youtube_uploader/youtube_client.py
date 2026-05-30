import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dateutil.parser import isoparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .media_probe import duration_within_tolerance

LOGGER = logging.getLogger(__name__)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


class YouTubeClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class YouTubeVerification:
    succeeded: bool
    status: dict[str, Any]
    content_details: dict[str, Any]
    error: str | None = None


def parse_iso8601_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    import re

    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    parts = {key: int(val or 0) for key, val in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


class YouTubeClient:
    def __init__(
        self,
        client_secret_file: Path,
        token_file: Path,
        chunk_size_bytes: int,
    ) -> None:
        self.client_secret_file = client_secret_file
        self.token_file = token_file
        self.chunk_size_bytes = chunk_size_bytes

    def _credentials(self) -> Credentials:
        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.token_file.write_text(creds.to_json(), encoding="utf-8")
        if not creds or not creds.valid:
            raise YouTubeClientError("YouTube OAuth token is missing or invalid")
        return creds

    def service(self):
        return build("youtube", "v3", credentials=self._credentials(), cache_discovery=False)

    def upload_video(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        category_id: str,
        privacy_status: str,
        notify_subscribers: bool,
        recording_date: datetime | None = None,
    ) -> str:
        snippet: dict[str, Any] = {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        }
        if recording_date:
            snippet["recordingDetails"] = {"recordingDate": recording_date.isoformat()}
        body = {"snippet": snippet, "status": {"privacyStatus": privacy_status}}
        media = MediaFileUpload(str(path), chunksize=self.chunk_size_bytes, resumable=True)
        request = (
            self.service()
            .videos()
            .insert(
                part="snippet,status,recordingDetails",
                body=body,
                media_body=media,
                notifySubscribers=notify_subscribers,
            )
        )
        response = None
        try:
            while response is None:
                _, response = request.next_chunk()
        except HttpError as exc:
            raise YouTubeClientError(f"YouTube upload failed: {exc.status_code}") from exc
        video_id = response.get("id")
        if not video_id:
            raise YouTubeClientError("YouTube upload response did not include video id")
        return video_id

    def get_video(self, video_id: str) -> dict[str, Any]:
        response = (
            self.service()
            .videos()
            .list(part="status,processingDetails,contentDetails", id=video_id)
            .execute()
        )
        items = response.get("items") or []
        if not items:
            raise YouTubeClientError(f"YouTube video not found: {video_id}")
        return items[0]

    def verify_video(
        self, video_id: str, expected_duration: int | None, tolerance: int
    ) -> YouTubeVerification:
        video = self.get_video(video_id)
        status = video.get("status") or {}
        processing = video.get("processingDetails") or {}
        content = video.get("contentDetails") or {}
        if status.get("failureReason") or status.get("rejectionReason"):
            return YouTubeVerification(
                False, status, content, "YouTube status contains failure/rejection"
            )
        processing_status = processing.get("processingStatus")
        if processing_status and processing_status not in {"succeeded", "terminated"}:
            return YouTubeVerification(
                False, status, content, f"Processing status is {processing_status}"
            )
        duration = parse_iso8601_duration_seconds(content.get("duration"))
        if duration is not None and not duration_within_tolerance(
            duration, expected_duration, tolerance
        ):
            return YouTubeVerification(
                False, status, content, "YouTube duration differs from local duration"
            )
        return YouTubeVerification(True, status, content)

    def wait_until_processed(
        self,
        video_id: str,
        expected_duration: int | None,
        tolerance: int,
        timeout_seconds: int,
        interval_seconds: int,
    ) -> YouTubeVerification:
        deadline = time.monotonic() + timeout_seconds
        last = None
        while time.monotonic() < deadline:
            last = self.verify_video(video_id, expected_duration, tolerance)
            if last.succeeded:
                return last
            LOGGER.info("YouTube video %s not verified yet: %s", video_id, last.error)
            time.sleep(interval_seconds)
        return last or YouTubeVerification(
            False, {}, {}, "Timed out waiting for YouTube processing"
        )

    def update_privacy(self, video_id: str, privacy_status: str) -> None:
        self.service().videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": privacy_status}},
        ).execute()


def oauth_interactive(client_secret_file: Path, token_file: Path) -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
    creds = flow.run_local_server(port=0)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")


def parse_recording_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return isoparse(value)
    except ValueError:
        return None
