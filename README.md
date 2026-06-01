# ganymede-youtube-uploader

Minimal FastAPI service that receives completed Ganymede VOD events, uploads the video to YouTube, verifies YouTube processing, then asks Ganymede to delete the VOD and files through the Ganymede API.

It never monitors Twitch, calls Twitch APIs, uses browser automation, writes to the Ganymede VOD mount, or deletes VOD files directly.

## Setup

1. Copy `.env.example` to `.env`.
2. Put YouTube OAuth files under `/data` or the mounted `./uploader-data` folder.
3. Initialize OAuth:

```bash
python -m scripts.init_youtube_oauth
```

4. Run with Docker:

```bash
docker compose -f docker-compose.example.yml up --build
```

5. Open `http://localhost:8000`. On first start, create the admin account.

## API

- `GET /health`
- `POST /webhooks/ganymede`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/retry`
- `POST /jobs/{job_id}/verify`
- `POST /jobs/{job_id}/cleanup`

## Web UI

The service includes a small admin UI at `/`.

On first start it redirects to `/setup` so you can create the first admin account. After login, the dashboard has:

- `Status / Log`: tracked channel label, linked YouTube channel label, job counts, current running task, recent uploads, cleanup status, and retry actions for failed jobs.
- `Settings`: editable service settings. Values are stored in SQLite and mirrored to the configured `.env` file when the app can write it.

The `TRACKED_TWITCH_CHANNEL` field is only a label/config value for this uploader. It does not enable Twitch monitoring and the service still does not call Twitch APIs.

Settings supplied by Docker or the process environment can take effect only after the service is restarted. Settings saved in the UI are used as SQLite overrides for new job processing where possible.

YouTube upload metadata can be adjusted in settings:

- `Youtube Title` chooses the upload title source: webhook/Ganymede title, Ganymede VOD title, Ganymede VOD ID, or upload job ID.
- `YOUTUBE_DESCRIPTION` sets the upload description text.

## Ganymede Webhook

Ganymede's notification screen only provides a webhook URL and message template. You can keep the default message:

```text
✅ Video Archived: {{vod_title}} by {{channel_display_name}}.
```

Set the webhook URL to include the uploader secret in the URL:

```text
http://uploader:8000/webhooks/ganymede/<APP_WEBHOOK_SECRET>
```

or:

```text
http://uploader:8000/webhooks/ganymede?secret=<APP_WEBHOOK_SECRET>
```

The uploader parses the title and channel name from the message, checks the channel against `TRACKED_TWITCH_CHANNEL` when set, then asks Ganymede's API for the matching VOD record before starting the upload workflow. This does not monitor Twitch and does not call Twitch APIs.

## CLI

```bash
python -m ganymede_youtube_uploader.cli status
python -m ganymede_youtube_uploader.cli retry 1
python -m ganymede_youtube_uploader.cli enqueue-ganymede-vod 123
python -m ganymede_youtube_uploader.cli enqueue-external-id 987654321
python -m ganymede_youtube_uploader.cli verify 1
python -m ganymede_youtube_uploader.cli cleanup 1
```

## Assumptions

Ganymede API paths vary by version. All path construction is isolated in `ganymede_client.py`. The first supported paths are:

- `GET /vod/{id}`
- `GET /vod?external_id={external_id}`
- `GET /vod?title={title}&channel_name={channel_name}&limit=25`
- `PATCH /vod/{id}` with `{"locked": true}`
- `DELETE /vod/{id}?delete_files=true`
- `GET /vod/{id}/ffprobe`

If a running Ganymede version does not support lock or ffprobe endpoints, the job logs that step and continues where safe. Cleanup still only uses the Ganymede API.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```
