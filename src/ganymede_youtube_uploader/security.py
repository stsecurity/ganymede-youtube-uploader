from fastapi import Header, HTTPException

from .config import Settings


def verify_webhook_secret(
    settings: Settings,
    provided_secret: str | None,
    url_secret: str | None = None,
) -> None:
    if not settings.app_webhook_secret:
        return
    if settings.app_webhook_secret in {provided_secret, url_secret}:
        return
    raise HTTPException(status_code=401, detail="Invalid webhook secret")


def verify_webhook_url_secret(settings: Settings, provided_secret: str) -> None:
    if settings.app_webhook_secret and provided_secret != settings.app_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


async def webhook_secret_header(x_webhook_secret: str | None = Header(default=None)) -> str | None:
    return x_webhook_secret
