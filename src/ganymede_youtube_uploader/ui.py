from __future__ import annotations

from html import escape
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .ganymede_client import GanymedeClient, GanymedeClientError
from .jobs import create_or_update_job, vod_value
from .models import JobStatus, UploadJob
from .notifications import send_job_notification, send_test_notification
from .ui_auth import (
    SESSION_COOKIE,
    authenticate_admin,
    create_admin_user,
    has_admin_user,
    sign_session,
    verify_session_cookie,
)
from .ui_settings import (
    BOOLEAN_FIELDS,
    SECRET_FIELDS,
    SELECT_FIELDS,
    SETTING_LABELS,
    SETTING_SECTIONS,
    build_effective_settings,
    current_ui_settings,
    update_ui_settings,
)
from .worker import current_running_job_ids, run_job_sync

router = APIRouter()
DBSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def form_data(request: Request) -> dict[str, str]:
    parsed = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - Ganymede YouTube Uploader</title>
  <style>{CSS}</style>
</head>
<body>{body}</body>
</html>"""
    )


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def current_admin_id(request: Request, settings: Settings) -> int | None:
    return verify_session_cookie(request.cookies.get(SESSION_COOKIE), settings)


def require_admin(request: Request, session: Session, settings: Settings) -> int | RedirectResponse:
    if not has_admin_user(session):
        return redirect("/setup")
    user_id = current_admin_id(request, settings)
    if user_id is None:
        return redirect("/login")
    return user_id


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> Response:
    if not has_admin_user(session):
        return redirect("/setup")
    if current_admin_id(request, settings) is None:
        return redirect("/login")
    return redirect("/ui")


@router.get("/setup", response_class=HTMLResponse)
def setup_get(session: DBSession) -> Response:
    if has_admin_user(session):
        return redirect("/login")
    return auth_page("Create Admin", "Create the first admin account.", "/setup", "Create account")


@router.post("/setup")
async def setup_post(
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> Response:
    if has_admin_user(session):
        return redirect("/login")
    data = await form_data(request)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or len(password) < 8:
        return auth_page(
            "Create Admin",
            "Use a username and a password of at least 8 characters.",
            "/setup",
            "Create account",
            error="Admin username and an 8+ character password are required.",
        )
    user = create_admin_user(session, username, password)
    response = redirect("/ui")
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user.id, settings),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.get("/login", response_class=HTMLResponse)
def login_get(session: DBSession) -> Response:
    if not has_admin_user(session):
        return redirect("/setup")
    return auth_page("Admin Login", "Sign in to manage uploads and settings.", "/login", "Log in")


@router.post("/login")
async def login_post(
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> Response:
    data = await form_data(request)
    user = authenticate_admin(session, data.get("username", "").strip(), data.get("password", ""))
    if not user:
        return auth_page(
            "Admin Login",
            "Sign in to manage uploads and settings.",
            "/login",
            "Log in",
            error="Invalid username or password.",
        )
    response = redirect("/ui")
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user.id, settings),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = redirect("/login")
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/ui", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> Response:
    auth = require_admin(request, session, settings)
    if isinstance(auth, RedirectResponse):
        return auth
    return page(
        "Dashboard", shell(status_section(session, settings), settings_section(session, settings))
    )


@router.post("/ui/settings")
async def save_settings(
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> Response:
    auth = require_admin(request, session, settings)
    if isinstance(auth, RedirectResponse):
        return auth
    update_ui_settings(session, await form_data(request), settings)
    return redirect("/ui?saved=1")


@router.post("/ui/settings/test-webhook")
async def test_webhook_settings(
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> Response:
    auth = require_admin(request, session, settings)
    if isinstance(auth, RedirectResponse):
        return auth
    update_ui_settings(session, await form_data(request), settings)
    sent = await send_test_notification(build_effective_settings(session))
    result = "sent" if sent else "failed"
    return redirect(f"/ui?webhook_test={result}")


@router.post("/ui/jobs/{job_id}/retry")
async def ui_retry_job(
    job_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
) -> RedirectResponse:
    auth = require_admin(request, session, settings)
    if isinstance(auth, RedirectResponse):
        return auth
    job = session.get(UploadJob, job_id)
    if job and job.status not in {JobStatus.COMPLETED, JobStatus.NEEDS_MANUAL_REVIEW}:
        job.status = JobStatus.RECEIVED
        job.last_error = None
        session.commit()
        background_tasks.add_task(process_ui_job_background, job.id)
    return redirect("/ui")


@router.post("/ui/jobs/{job_id}/skip")
async def ui_skip_job(
    job_id: int,
    request: Request,
    session: DBSession,
    settings: AppSettings,
) -> RedirectResponse:
    auth = require_admin(request, session, settings)
    if isinstance(auth, RedirectResponse):
        return auth
    job = session.get(UploadJob, job_id)
    if job and job.status != JobStatus.COMPLETED:
        job.status = JobStatus.SKIPPED
        job.last_error = "Skipped by admin"
        session.commit()
        await send_job_notification(build_effective_settings(session), job, "skipped")
    return redirect("/ui")


@router.post("/ui/check-new-vod")
async def ui_check_new_vod(
    request: Request,
    background_tasks: BackgroundTasks,
    session: DBSession,
    settings: AppSettings,
) -> RedirectResponse:
    auth = require_admin(request, session, settings)
    if isinstance(auth, RedirectResponse):
        return auth
    effective_settings = build_effective_settings(session)
    if not effective_settings.tracked_twitch_channel:
        return redirect("/ui?check=missing_channel")
    try:
        vods = await GanymedeClient(
            effective_settings.ganymede_base_url,
            effective_settings.ganymede_api_key,
        ).list_vods(channel_name=effective_settings.tracked_twitch_channel, limit=100)
    except GanymedeClientError:
        return redirect("/ui?check=ganymede_error")
    jobs = []
    for vod in vods:
        ganymede_vod_id = str(vod_value(vod, "id", "vod_id", "vodId") or "") or None
        external_id = (
            str(vod_value(vod, "external_id", "externalId", "ext_id", "extId") or "") or None
        )
        if not ganymede_vod_id and not external_id:
            continue
        jobs.append(
            create_or_update_job(
                session,
                ganymede_vod_id=ganymede_vod_id,
                external_id=external_id,
                title=vod_value(vod, "title"),
            )
        )
    if not jobs:
        return redirect("/ui?check=no_vod")
    for job in jobs:
        if job.status not in {JobStatus.COMPLETED, JobStatus.UPLOADING, JobStatus.SKIPPED}:
            background_tasks.add_task(process_ui_job_background, job.id)
    return redirect("/ui?check=queued")


def process_ui_job_background(job_id: int) -> None:
    run_job_sync(job_id)


def auth_page(
    title: str,
    intro: str,
    action: str,
    button: str,
    error: str | None = None,
) -> HTMLResponse:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return page(
        title,
        f"""
<main class="auth-shell">
  <section class="auth-card">
    <div class="brand">Ganymede YouTube Uploader</div>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
    {error_html}
    <form method="post" action="{escape(action)}">
      <label>Username<input name="username" autocomplete="username" required></label>
      <label>Password
        <input name="password" type="password" autocomplete="current-password" required>
      </label>
      <button type="submit">{escape(button)}</button>
    </form>
  </section>
</main>""",
    )


def shell(status_html: str, settings_html: str) -> str:
    return f"""
<header class="topbar">
  <div>
    <strong>Ganymede YouTube Uploader</strong>
    <span>Upload verification and cleanup control</span>
  </div>
  <form method="post" action="/logout"><button class="ghost" type="submit">Log out</button></form>
</header>
<main class="layout">
  {status_html}
  {settings_html}
</main>"""


def status_section(session: Session, settings: Settings) -> str:
    values = current_ui_settings(session, settings)
    tracked_channel = escape(values["TRACKED_TWITCH_CHANNEL"] or "Not set")
    linked_channel = escape(values["LINKED_YOUTUBE_CHANNEL"] or "Not set")
    delete_after_upload = values["DELETE_GANYMEDE_VOD_AFTER_YOUTUBE_UPLOAD"].lower() in {
        "1",
        "true",
        "on",
        "yes",
    }
    cleanup_mode = "Delete after verified upload" if delete_after_upload else "Keep VODs"
    completed_label = "Uploaded and deleted" if delete_after_upload else "Verified uploads"
    jobs = list(session.scalars(select(UploadJob).order_by(UploadJob.updated_at.desc()).limit(12)))
    running_job_ids = current_running_job_ids()
    running_job = session.get(UploadJob, running_job_ids[0]) if running_job_ids else None
    total = session.scalar(select(func.count(UploadJob.id))) or 0
    completed = (
        session.scalar(
            select(func.count(UploadJob.id)).where(UploadJob.status == JobStatus.COMPLETED)
        )
        or 0
    )
    failed = (
        session.scalar(select(func.count(UploadJob.id)).where(UploadJob.status == JobStatus.FAILED))
        or 0
    )
    skipped = (
        session.scalar(
            select(func.count(UploadJob.id)).where(UploadJob.status == JobStatus.SKIPPED)
        )
        or 0
    )
    rows = "\n".join(job_row(job) for job in jobs) or (
        '<tr><td colspan="6" class="empty">No upload jobs yet.</td></tr>'
    )
    running_text = escape(running_job.title or f"Job {running_job.id}") if running_job else "Idle"
    return f"""
<section class="panel status-panel">
  <div class="section-head">
    <div><h1>Status / Log</h1><p>Recent uploads, cleanup, and current worker state.</p></div>
    <span class="state">{running_text}</span>
  </div>
  <div class="metrics">
    <div><span>Tracked Twitch channel</span><strong>{tracked_channel}</strong></div>
    <div><span>Linked YouTube channel</span><strong>{linked_channel}</strong></div>
    <div><span>Total jobs</span><strong>{total}</strong></div>
    <div><span>{completed_label}</span><strong>{completed}</strong></div>
    <div><span>Ganymede cleanup</span><strong>{cleanup_mode}</strong></div>
    <div><span>Failed</span><strong>{failed}</strong></div>
    <div><span>Skipped</span><strong>{skipped}</strong></div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Job</th><th>Title</th><th>Status</th><th>Ganymede</th><th>YouTube</th><th>Action</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>"""


def job_row(job: UploadJob) -> str:
    actions = []
    if job.status in {
        JobStatus.FAILED,
        JobStatus.NEEDS_MANUAL_CLEANUP,
        JobStatus.SKIPPED,
    }:
        actions.append(
            f'<form method="post" action="/ui/jobs/{job.id}/retry">'
            '<button class="mini" type="submit">Retry</button></form>'
        )
    if job.status not in {JobStatus.COMPLETED, JobStatus.SKIPPED}:
        actions.append(
            f'<form method="post" action="/ui/jobs/{job.id}/skip">'
            '<button class="mini danger" type="submit">Skip</button></form>'
        )
    action_html = f'<div class="actions">{"".join(actions)}</div>' if actions else ""
    detail = escape(job.last_error or "")
    return f"""
<tr>
  <td>#{job.id}<small>{job.attempt_count} attempts</small></td>
  <td>{escape(job.title or "-")}<small>{detail}</small></td>
  <td><span class="badge {escape(job.status.value)}">{escape(job.status.value)}</span></td>
  <td>{escape(job.ganymede_vod_id or "-")}</td>
  <td>{escape(job.youtube_video_id or "-")}</td>
  <td>{action_html}</td>
</tr>"""


def settings_section(session: Session, settings: Settings) -> str:
    values = current_ui_settings(session, settings)
    sections = "\n".join(settings_group(title, keys, values) for title, keys in SETTING_SECTIONS)
    env_file = escape(str(settings.ui_env_file))
    return f"""
<section class="panel settings-panel">
  <div class="section-head">
    <div><h1>Settings</h1><p>Values are stored in SQLite and mirrored to {env_file}.</p></div>
  </div>
  <form method="post" action="/ui/settings" class="settings-grid">
    {sections}
    <div class="form-actions">
      <p>Changing database paths or Docker-provided env vars may require a service restart.</p>
      <button type="submit">Save settings</button>
    </div>
  </form>
  <form method="post" action="/ui/check-new-vod" class="manual-action">
    <p>Check Ganymede now and queue every VOD found for the tracked channel.</p>
    <button type="submit" class="secondary">Upload all found VODs now</button>
  </form>
</section>"""


def settings_group(title: str, keys: list[str], values: dict[str, str]) -> str:
    fields = "\n".join(setting_field(key, values.get(key, "")) for key in keys)
    test_button = ""
    if title == "Webhook Settings":
        test_button = (
            '<button type="submit" class="secondary mini" '
            'formaction="/ui/settings/test-webhook">Test notification</button>'
        )
    return f"""
<fieldset class="settings-group">
  <legend>
    <span>{escape(title)}</span>
    {test_button}
  </legend>
  <div class="settings-group-grid">
    {fields}
  </div>
</fieldset>"""


def setting_field(key: str, value: str) -> str:
    label = SETTING_LABELS.get(key, key.replace("_", " ").title())
    if key in BOOLEAN_FIELDS:
        checked = "checked" if value.lower() in {"1", "true", "on", "yes"} else ""
        return f"""
<label class="check">
  <input name="{key}" type="checkbox" value="true" {checked}>
  <span>{escape(label)}</span>
</label>"""
    if key in SELECT_FIELDS:
        options = "\n".join(
            select_option(option_value, option_label, value)
            for option_value, option_label in SELECT_FIELDS[key]
        )
        return f"""
<label>
  <span>{escape(label)}</span>
  <select name="{key}">{options}</select>
</label>"""
    input_type = "password" if key in SECRET_FIELDS else "text"
    rendered_value = "" if key in SECRET_FIELDS and value else escape(value)
    placeholder = "Stored; leave blank to keep" if key in SECRET_FIELDS and value else ""
    return f"""
<label>
  <span>{escape(label)}</span>
  <input name="{key}" type="{input_type}" value="{rendered_value}" placeholder="{placeholder}">
</label>"""


def select_option(option_value: str, option_label: str, current_value: str) -> str:
    selected = "selected" if option_value == current_value else ""
    return f'<option value="{escape(option_value)}" {selected}>{escape(option_label)}</option>'


CSS = """
:root {
  color-scheme: dark;
  --bg: #101014;
  --panel: #181820;
  --panel-2: #20202a;
  --line: #30303d;
  --text: #f3f0ff;
  --muted: #aaa7b8;
  --accent: #9147ff;
  --accent-2: #b986ff;
  --danger: #ff6767;
  --ok: #56d68a;
  --warn: #ffca66;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font:
    14px/1.45 Inter,
    ui-sans-serif,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 28px;
  border-bottom: 1px solid var(--line);
  background: #15151c;
}
.topbar strong, .brand { display: block; font-size: 16px; font-weight: 800; }
.topbar span, p, small { color: var(--muted); }
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(360px, .75fr);
  gap: 18px;
  padding: 22px;
}
.panel, .auth-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 20px 60px rgb(0 0 0 / 24%);
}
.panel { padding: 20px; min-width: 0; }
.section-head {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
h1 { margin: 0; font-size: 22px; line-height: 1.15; letter-spacing: 0; }
.section-head p { margin: 6px 0 0; }
.state {
  border: 1px solid color-mix(in srgb, var(--accent), var(--line) 45%);
  color: var(--accent-2);
  padding: 7px 10px;
  border-radius: 6px;
  white-space: nowrap;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
  margin-bottom: 18px;
}
.metrics div {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 12px;
}
.metrics span, label span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 6px;
}
.metrics strong { display: block; overflow-wrap: anywhere; font-size: 18px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }
table { width: 100%; border-collapse: collapse; min-width: 760px; }
th, td {
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
th { color: var(--muted); font-size: 12px; font-weight: 700; background: #14141b; }
td small { display: block; margin-top: 4px; overflow-wrap: anywhere; }
.badge {
  display: inline-block;
  border-radius: 6px;
  padding: 5px 7px;
  background: #292935;
  color: var(--muted);
  font-size: 12px;
}
.badge.completed { color: var(--ok); }
.badge.failed, .badge.needs_manual_cleanup, .error { color: var(--danger); }
.badge.uploading, .badge.verifying_youtube, .badge.cleaning_ganymede { color: var(--warn); }
.badge.skipped { color: var(--muted); }
button {
  border: 0;
  border-radius: 6px;
  background: var(--accent);
  color: white;
  padding: 10px 14px;
  font-weight: 750;
  cursor: pointer;
}
button:hover { background: #7f35f0; }
button.ghost, button.mini { background: var(--panel-2); border: 1px solid var(--line); }
button.mini { padding: 7px 10px; }
button.mini.danger { color: var(--danger); }
button.secondary {
  background: var(--panel-2);
  border: 1px solid var(--accent);
  color: var(--accent-2);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.settings-grid {
  display: grid;
  gap: 16px;
}
.settings-group {
  border: 1px solid var(--line);
  border-radius: 6px;
  margin: 0;
  padding: 14px;
  min-width: 0;
}
.settings-group legend {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 0 4px;
  color: var(--text);
  font-weight: 800;
}
.settings-group-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}
label { min-width: 0; }
input, select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  background: #111118;
  color: var(--text);
  font: inherit;
}
select { appearance: auto; }
.check {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
}
.check input { width: 18px; height: 18px; accent-color: var(--accent); }
.check span { margin: 0; }
.form-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  border-top: 1px solid var(--line);
  padding-top: 14px;
}
.manual-action {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  border-top: 1px solid var(--line);
  margin-top: 14px;
  padding-top: 14px;
}
.auth-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 22px;
}
.auth-card {
  width: min(440px, 100%);
  padding: 26px;
}
.auth-card h1 { margin-top: 18px; }
.auth-card form { display: grid; gap: 14px; margin-top: 22px; }
.empty { color: var(--muted); text-align: center; }
@media (max-width: 1100px) {
  .layout { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .topbar, .section-head, .form-actions, .manual-action {
    align-items: stretch;
    flex-direction: column;
  }
  .layout { padding: 12px; }
  .panel { padding: 14px; }
  .metrics, .settings-group-grid { grid-template-columns: 1fr; }
}
"""
