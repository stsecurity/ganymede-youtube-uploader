import base64
import hashlib
import hmac
import os
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AdminUser

SESSION_COOKIE = "gyu_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7
_PROCESS_SESSION_SECRET = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return (
        "pbkdf2_sha256$390000$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        base64.b64decode(salt),
        int(iterations),
    )
    return hmac.compare_digest(base64.b64encode(expected).decode("ascii"), digest)


def has_admin_user(session: Session) -> bool:
    return session.scalar(select(AdminUser.id).limit(1)) is not None


def create_admin_user(session: Session, username: str, password: str) -> AdminUser:
    user = AdminUser(username=username, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate_admin(session: Session, username: str, password: str) -> AdminUser | None:
    user = session.scalar(select(AdminUser).where(AdminUser.username == username))
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def session_secret(settings: Settings) -> str:
    return settings.ui_session_secret or settings.app_webhook_secret or _PROCESS_SESSION_SECRET


def sign_session(user_id: int, settings: Settings) -> str:
    issued_at = str(int(time.time()))
    payload = f"{user_id}:{issued_at}"
    signature = hmac.new(
        session_secret(settings).encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_session_cookie(cookie_value: str | None, settings: Settings) -> int | None:
    if not cookie_value:
        return None
    try:
        user_id, issued_at, signature = cookie_value.split(":", 2)
        payload = f"{user_id}:{issued_at}"
        expected = hmac.new(
            session_secret(settings).encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    except ValueError:
        return None
    if not hmac.compare_digest(signature, expected):
        return None
    if int(time.time()) - int(issued_at) > SESSION_MAX_AGE_SECONDS:
        return None
    return int(user_id)
