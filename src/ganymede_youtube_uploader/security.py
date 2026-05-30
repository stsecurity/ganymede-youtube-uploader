from fastapi import Header, HTTPException

from .config import Settings


def verify_webhook_secret(settings: Settings, provided_secret: str | None) -> None:
    if settings.app_webhook_secret and provided_secret != settings.app_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


async def webhook_secret_header(x_webhook_secret: str | None = Header(default=None)) -> str | None:
    return x_webhook_secret
