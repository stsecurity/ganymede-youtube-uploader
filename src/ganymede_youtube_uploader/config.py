from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_base_url: str = ""
    app_webhook_secret: str = ""
    ui_session_secret: str = ""
    ui_env_file: Path = Path(".env")
    database_url: str = "sqlite:////data/uploader.sqlite"

    ganymede_base_url: str = "http://ganymede:4000/api/v1"
    ganymede_api_key: str = ""
    ganymede_videos_mount: Path = Path("/ganymede/videos")
    ganymede_videos_root_in_ganymede: str = "/data/videos"
    allow_vod_directory_scan: bool = False

    youtube_client_secret_file: Path = Path("/data/youtube_client_secret.json")
    youtube_token_file: Path = Path("/data/youtube_token.json")
    youtube_title_option: str = "1"
    youtube_description: str = "Uploaded from a completed Ganymede archive."
    youtube_default_privacy: str = "private"
    youtube_final_privacy: str = ""
    youtube_category_id: str = "20"
    youtube_notify_subscribers: bool = False

    upload_chunk_size_mb: int = Field(default=8, ge=1)
    youtube_verify_timeout_minutes: int = Field(default=360, ge=1)
    youtube_verify_interval_seconds: int = Field(default=300, ge=1)

    duration_tolerance_seconds: int = Field(default=30, ge=0)
    require_audio_stream: bool = False

    tracked_twitch_channel: str = ""
    linked_youtube_channel: str = ""
    webhook_notifications_enabled: bool = False
    webhook_notification_url: str = ""

    @computed_field
    @property
    def upload_chunk_size_bytes(self) -> int:
        return self.upload_chunk_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
