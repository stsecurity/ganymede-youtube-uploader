from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ganymede_youtube_uploader.config import Settings, get_settings
from ganymede_youtube_uploader.db import Base, get_db
from ganymede_youtube_uploader.ganymede_client import GanymedeClientError
from ganymede_youtube_uploader.jobs import (
    channel_matches_tracked,
    extract_ganymede_archive_message,
)
from ganymede_youtube_uploader.main import app, resolve_webhook_job_fields


@pytest.fixture()
def webhook_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db() -> Generator[Session, None, None]:
        session = local()
        try:
            yield session
        finally:
            session.close()

    def override_settings() -> Settings:
        return Settings(
            database_url="sqlite:///:memory:",
            app_webhook_secret="url-secret",
            tracked_twitch_channel="Streamer",
            ui_env_file=tmp_path / ".env",
        )

    async def noop_process_job_background(job_id: int) -> None:
        return None

    monkeypatch.setattr(
        "ganymede_youtube_uploader.main.process_job_background",
        noop_process_job_background,
    )
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_url_secret_allows_ganymede_webhook_without_header(webhook_client: TestClient) -> None:
    response = webhook_client.post(
        "/webhooks/ganymede/url-secret",
        json={"vod_id": "vod-1", "title": "Archived VOD"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == 1


def test_query_secret_allows_ganymede_webhook_without_header(webhook_client: TestClient) -> None:
    response = webhook_client.post(
        "/webhooks/ganymede?secret=url-secret",
        json={"vod_id": "vod-2", "title": "Archived VOD"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == 1


def test_ganymede_default_message_is_parsed_without_template_changes() -> None:
    title, channel = extract_ganymede_archive_message(
        {"content": "✅ Video Archived: Speedrun by Night by Streamer."}
    )

    assert title == "Speedrun by Night"
    assert channel == "Streamer"


def test_channel_matching_is_case_insensitive() -> None:
    assert channel_matches_tracked("Streamer", "streamer")
    assert not channel_matches_tracked("Other", "streamer")


@pytest.mark.asyncio
async def test_message_webhook_resolves_matching_ganymede_vod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGanymedeClient:
        def __init__(self, base_url: str, api_key: str) -> None:
            pass

        async def find_vod_by_title_and_channel(
            self, title: str, channel_name: str
        ) -> dict[str, Any]:
            return {
                "id": "vod-1",
                "external_id": "ext-1",
                "title": title,
                "channel": {"displayName": channel_name},
            }

    monkeypatch.setattr("ganymede_youtube_uploader.main.GanymedeClient", FakeGanymedeClient)

    ganymede_id, external_id, title = await resolve_webhook_job_fields(
        {"content": "✅ Video Archived: My VOD by Streamer."},
        Settings(tracked_twitch_channel="streamer"),
    )

    assert ganymede_id == "vod-1"
    assert external_id == "ext-1"
    assert title == "My VOD"


@pytest.mark.asyncio
async def test_message_webhook_lookup_failure_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGanymedeClient:
        def __init__(self, base_url: str, api_key: str) -> None:
            pass

        async def find_vod_by_title_and_channel(
            self, title: str, channel_name: str
        ) -> dict[str, Any]:
            raise GanymedeClientError("Ganymede endpoint not found: vod")

    monkeypatch.setattr("ganymede_youtube_uploader.main.GanymedeClient", FakeGanymedeClient)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_webhook_job_fields(
            {"content": "鉁?Video Archived: My VOD by Streamer."},
            Settings(tracked_twitch_channel="streamer"),
        )

    assert exc_info.value.status_code == 202
