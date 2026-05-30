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

## API

- `GET /health`
- `POST /webhooks/ganymede`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/retry`
- `POST /jobs/{job_id}/verify`
- `POST /jobs/{job_id}/cleanup`

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

