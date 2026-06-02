from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ganymede_youtube_uploader.config import Settings, get_settings
from ganymede_youtube_uploader.db import Base, get_db
from ganymede_youtube_uploader.main import app


@pytest.fixture()
def ui_client(tmp_path: Path) -> Generator[TestClient, None, None]:
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
            ui_env_file=tmp_path / ".env",
            ui_session_secret="test-secret",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_first_start_setup_login_and_dashboard(ui_client: TestClient) -> None:
    response = ui_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/setup"

    response = ui_client.post(
        "/setup",
        data={"username": "admin", "password": "long-enough-password"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui"
    assert "gyu_session" in response.headers["set-cookie"]

    dashboard = ui_client.get("/ui")
    assert dashboard.status_code == 200
    assert "Status / Log" in dashboard.text
    assert "Settings" in dashboard.text


def test_settings_save_updates_dashboard_and_env_file(
    ui_client: TestClient, tmp_path: Path
) -> None:
    ui_client.post(
        "/setup",
        data={"username": "admin", "password": "long-enough-password"},
        follow_redirects=False,
    )

    response = ui_client.post(
        "/ui/settings",
        data={
            "TRACKED_TWITCH_CHANNEL": "channel-name",
            "LINKED_YOUTUBE_CHANNEL": "Uploads Channel",
            "GANYMEDE_BASE_URL": "http://ganymede:4000/api/v1",
            "YOUTUBE_NOTIFY_SUBSCRIBERS": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    dashboard = ui_client.get("/ui")
    assert "channel-name" in dashboard.text
    assert "Uploads Channel" in dashboard.text
    assert "TRACKED_TWITCH_CHANNEL=channel-name" in (tmp_path / ".env").read_text()


def test_youtube_settings_render_as_dropdowns_and_save(
    ui_client: TestClient, tmp_path: Path
) -> None:
    ui_client.post(
        "/setup",
        data={"username": "admin", "password": "long-enough-password"},
        follow_redirects=False,
    )

    dashboard = ui_client.get("/ui")

    assert '<select name="YOUTUBE_DEFAULT_PRIVACY">' in dashboard.text
    assert '<select name="YOUTUBE_FINAL_PRIVACY">' in dashboard.text
    assert '<select name="YOUTUBE_CATEGORY_ID">' in dashboard.text
    assert '<select name="YOUTUBE_TITLE_OPTION">' in dashboard.text
    assert "Youtube Title" in dashboard.text
    assert "Youtube Category" in dashboard.text
    assert "Gaming" in dashboard.text

    response = ui_client.post(
        "/ui/settings",
        data={
            "YOUTUBE_DEFAULT_PRIVACY": "unlisted",
            "YOUTUBE_FINAL_PRIVACY": "public",
            "YOUTUBE_CATEGORY_ID": "24",
            "YOUTUBE_TITLE_OPTION": "2",
            "YOUTUBE_DESCRIPTION": "Custom upload description.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    env_text = (tmp_path / ".env").read_text()
    assert "YOUTUBE_DEFAULT_PRIVACY=unlisted" in env_text
    assert "YOUTUBE_FINAL_PRIVACY=public" in env_text
    assert "YOUTUBE_CATEGORY_ID=24" in env_text
    assert "YOUTUBE_TITLE_OPTION=2" in env_text
    assert "YOUTUBE_DESCRIPTION=Custom upload description." in env_text


def test_check_new_vod_button_enqueues_all_tracked_channel_vods(
    ui_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeGanymedeClient:
        def __init__(self, base_url: str, api_key: str) -> None:
            pass

        async def list_vods(self, channel_name: str = "", limit: int = 100) -> list[dict[str, Any]]:
            assert channel_name == "stsecurity"
            assert limit == 100
            return [
                {
                    "id": "old-vod",
                    "ext_id": "old-ext",
                    "title": "Older VOD",
                    "created_at": "2026-06-01T10:00:00Z",
                    "edges": {"channel": {"name": "stsecurity"}},
                },
                {
                    "id": "new-vod",
                    "ext_id": "new-ext",
                    "title": "Newest VOD",
                    "created_at": "2026-06-01T12:00:00Z",
                    "edges": {"channel": {"name": "stsecurity"}},
                },
            ]

    async def noop_process_ui_job_background(job_id: int) -> None:
        return None

    monkeypatch.setattr("ganymede_youtube_uploader.ui.GanymedeClient", FakeGanymedeClient)
    monkeypatch.setattr(
        "ganymede_youtube_uploader.ui.process_ui_job_background",
        noop_process_ui_job_background,
    )
    ui_client.post(
        "/setup",
        data={"username": "admin", "password": "long-enough-password"},
        follow_redirects=False,
    )
    ui_client.post(
        "/ui/settings",
        data={"TRACKED_TWITCH_CHANNEL": "stsecurity"},
        follow_redirects=False,
    )

    dashboard = ui_client.get("/ui")
    assert "Upload all found VODs now" in dashboard.text

    response = ui_client.post("/ui/check-new-vod", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui?check=queued"
    dashboard = ui_client.get("/ui")
    assert "Newest VOD" in dashboard.text
    assert "Older VOD" in dashboard.text
