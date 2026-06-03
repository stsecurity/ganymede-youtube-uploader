import argparse

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ganymede_youtube_uploader import cli
from ganymede_youtube_uploader.db import Base
from ganymede_youtube_uploader.models import AppSetting


@pytest.mark.asyncio
async def test_cli_uses_sqlite_settings_before_env(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine, expire_on_commit=False)
    captured_base_urls: list[str] = []

    with local() as session:
        session.add(AppSetting(key="GANYMEDE_BASE_URL", value="http://sqlite-ganymede:4000"))
        session.commit()

    def session_local() -> Session:
        return local()

    class FakeProcessor:
        def __init__(self, settings) -> None:
            captured_base_urls.append(settings.ganymede_base_url)

        async def process(self, session: Session, job) -> None:
            return None

    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", session_local)
    monkeypatch.setattr(cli, "JobProcessor", FakeProcessor)

    await cli.run_command(argparse.Namespace(command="enqueue-ganymede-vod", vod_id="vod-1"))

    assert captured_base_urls == ["http://sqlite-ganymede:4000"]
