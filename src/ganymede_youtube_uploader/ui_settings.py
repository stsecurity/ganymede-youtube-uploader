from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import AppSetting

SETTING_FIELDS = [
    "APP_BASE_URL",
    "APP_WEBHOOK_SECRET",
    "UI_SESSION_SECRET",
    "DATABASE_URL",
    "GANYMEDE_BASE_URL",
    "GANYMEDE_API_KEY",
    "GANYMEDE_VIDEOS_MOUNT",
    "GANYMEDE_VIDEOS_ROOT_IN_GANYMEDE",
    "YOUTUBE_CLIENT_SECRET_FILE",
    "YOUTUBE_TOKEN_FILE",
    "YOUTUBE_TITLE_OPTION",
    "YOUTUBE_DESCRIPTION",
    "YOUTUBE_TAGS_OPTION",
    "YOUTUBE_TAGS",
    "YOUTUBE_DEFAULT_PRIVACY",
    "YOUTUBE_FINAL_PRIVACY",
    "YOUTUBE_CATEGORY_ID",
    "YOUTUBE_NOTIFY_SUBSCRIBERS",
    "DELETE_GANYMEDE_VOD_AFTER_YOUTUBE_UPLOAD",
    "UPLOAD_CHUNK_SIZE_MB",
    "YOUTUBE_VERIFY_TIMEOUT_MINUTES",
    "YOUTUBE_VERIFY_INTERVAL_SECONDS",
    "DURATION_TOLERANCE_SECONDS",
    "REQUIRE_AUDIO_STREAM",
    "TRACKED_TWITCH_CHANNEL",
    "LINKED_YOUTUBE_CHANNEL",
    "WEBHOOK_NOTIFICATIONS_ENABLED",
    "WEBHOOK_NOTIFICATION_URL",
]
SETTING_SECTIONS = [
    (
        "Base Settings",
        [
            "APP_BASE_URL",
            "APP_WEBHOOK_SECRET",
            "UI_SESSION_SECRET",
            "DATABASE_URL",
        ],
    ),
    (
        "Ganymede Settings",
        [
            "GANYMEDE_BASE_URL",
            "GANYMEDE_API_KEY",
            "GANYMEDE_VIDEOS_MOUNT",
            "GANYMEDE_VIDEOS_ROOT_IN_GANYMEDE",
        ],
    ),
    (
        "YouTube Settings",
        [
            "YOUTUBE_CLIENT_SECRET_FILE",
            "YOUTUBE_TOKEN_FILE",
            "YOUTUBE_TITLE_OPTION",
            "YOUTUBE_DESCRIPTION",
            "YOUTUBE_TAGS_OPTION",
            "YOUTUBE_TAGS",
            "YOUTUBE_DEFAULT_PRIVACY",
            "YOUTUBE_FINAL_PRIVACY",
            "YOUTUBE_CATEGORY_ID",
            "YOUTUBE_NOTIFY_SUBSCRIBERS",
        ],
    ),
    (
        "Uploader Settings",
        [
            "DELETE_GANYMEDE_VOD_AFTER_YOUTUBE_UPLOAD",
            "UPLOAD_CHUNK_SIZE_MB",
            "YOUTUBE_VERIFY_TIMEOUT_MINUTES",
            "YOUTUBE_VERIFY_INTERVAL_SECONDS",
            "DURATION_TOLERANCE_SECONDS",
            "REQUIRE_AUDIO_STREAM",
            "TRACKED_TWITCH_CHANNEL",
            "LINKED_YOUTUBE_CHANNEL",
        ],
    ),
    (
        "Webhook Settings",
        [
            "WEBHOOK_NOTIFICATIONS_ENABLED",
            "WEBHOOK_NOTIFICATION_URL",
        ],
    ),
]

SECRET_FIELDS = {"APP_WEBHOOK_SECRET", "UI_SESSION_SECRET", "GANYMEDE_API_KEY"}
BOOLEAN_FIELDS = {
    "YOUTUBE_NOTIFY_SUBSCRIBERS",
    "DELETE_GANYMEDE_VOD_AFTER_YOUTUBE_UPLOAD",
    "REQUIRE_AUDIO_STREAM",
    "WEBHOOK_NOTIFICATIONS_ENABLED",
}
INTEGER_FIELDS = {
    "UPLOAD_CHUNK_SIZE_MB",
    "YOUTUBE_VERIFY_TIMEOUT_MINUTES",
    "YOUTUBE_VERIFY_INTERVAL_SECONDS",
    "DURATION_TOLERANCE_SECONDS",
}
SETTING_LABELS = {
    "YOUTUBE_TITLE_OPTION": "Youtube Title",
    "YOUTUBE_TAGS_OPTION": "Youtube Tags",
    "YOUTUBE_TAGS": "Youtube Custom Tags",
    "YOUTUBE_CATEGORY_ID": "Youtube Category",
    "DELETE_GANYMEDE_VOD_AFTER_YOUTUBE_UPLOAD": (
        "Delete VOD from Ganymede after successfully uploading to YouTube"
    ),
}
YOUTUBE_TITLE_OPTIONS = [
    ("1", "1. Webhook/Ganymede title"),
    ("2", "2. Ganymede VOD title"),
    ("3", "3. Ganymede VOD ID"),
    ("4", "4. Upload job ID"),
]
YOUTUBE_TAGS_OPTIONS = [
    ("none", "No tags"),
    ("custom", "Custom tags"),
]
PRIVACY_OPTIONS = [
    ("private", "Private"),
    ("unlisted", "Unlisted"),
    ("public", "Public"),
]
YOUTUBE_CATEGORY_OPTIONS = [
    ("1", "Film & Animation"),
    ("2", "Autos & Vehicles"),
    ("10", "Music"),
    ("15", "Pets & Animals"),
    ("17", "Sports"),
    ("19", "Travel & Events"),
    ("20", "Gaming"),
    ("22", "People & Blogs"),
    ("23", "Comedy"),
    ("24", "Entertainment"),
    ("25", "News & Politics"),
    ("26", "Howto & Style"),
    ("27", "Education"),
    ("28", "Science & Technology"),
    ("29", "Nonprofits & Activism"),
]
SELECT_FIELDS = {
    "YOUTUBE_TITLE_OPTION": YOUTUBE_TITLE_OPTIONS,
    "YOUTUBE_TAGS_OPTION": YOUTUBE_TAGS_OPTIONS,
    "YOUTUBE_DEFAULT_PRIVACY": PRIVACY_OPTIONS,
    "YOUTUBE_FINAL_PRIVACY": [("", "No change after verification"), *PRIVACY_OPTIONS],
    "YOUTUBE_CATEGORY_ID": YOUTUBE_CATEGORY_OPTIONS,
}
SELECT_DEFAULTS = {
    "YOUTUBE_TITLE_OPTION": "1",
    "YOUTUBE_TAGS_OPTION": "none",
    "YOUTUBE_DEFAULT_PRIVACY": "private",
    "YOUTUBE_FINAL_PRIVACY": "",
    "YOUTUBE_CATEGORY_ID": "20",
}


def env_to_attr(key: str) -> str:
    return key.lower()


def get_setting_overrides(session: Session) -> dict[str, str]:
    return {setting.key: setting.value for setting in session.query(AppSetting).all()}


def current_ui_settings(session: Session, settings: Settings) -> dict[str, str]:
    overrides = get_setting_overrides(session)
    values = {}
    for key in SETTING_FIELDS:
        attr = env_to_attr(key)
        values[key] = overrides.get(key, str(getattr(settings, attr, "") or ""))
    return values


def update_ui_settings(session: Session, submitted: dict[str, str], settings: Settings) -> None:
    current = current_ui_settings(session, settings)
    for key in SETTING_FIELDS:
        value = submitted.get(key, "")
        if key in SECRET_FIELDS and not value:
            value = current.get(key, "")
        value = normalize_setting_value(key, value)
        setting = session.get(AppSetting, key)
        if setting:
            setting.value = value
        else:
            session.add(AppSetting(key=key, value=value))
    session.commit()
    write_env_file(settings.ui_env_file, current_ui_settings(session, settings))
    get_settings.cache_clear()


def normalize_setting_value(key: str, value: str) -> str:
    value = value.strip()
    if key in BOOLEAN_FIELDS:
        return "true" if value.lower() in {"1", "true", "on", "yes"} else "false"
    if key in SELECT_FIELDS:
        allowed_values = {option_value for option_value, _ in SELECT_FIELDS[key]}
        return value if value in allowed_values else SELECT_DEFAULTS[key]
    return value


def build_effective_settings(session: Session) -> Settings:
    base = get_settings()
    updates: dict[str, Any] = {}
    for key, value in get_setting_overrides(session).items():
        if key not in SETTING_FIELDS:
            continue
        attr = env_to_attr(key)
        if key in BOOLEAN_FIELDS:
            updates[attr] = value.lower() in {"1", "true", "on", "yes"}
        elif key in INTEGER_FIELDS and value:
            updates[attr] = int(value)
        elif key in {
            "GANYMEDE_VIDEOS_MOUNT",
            "YOUTUBE_CLIENT_SECRET_FILE",
            "YOUTUBE_TOKEN_FILE",
        }:
            updates[attr] = Path(value)
        else:
            updates[attr] = value
    return base.model_copy(update=updates)


def write_env_file(path: Path, values: dict[str, str]) -> None:
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            existing[key] = value
    existing.update(values)
    lines = [f"{key}={existing[key]}" for key in sorted(existing)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
