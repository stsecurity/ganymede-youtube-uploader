FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir .

RUN useradd --create-home --home-dir /home/app app \
    && mkdir -p /data \
    && chown -R app:app /data /app

USER app

EXPOSE 8000
VOLUME ["/data", "/ganymede/videos"]

CMD ["uvicorn", "ganymede_youtube_uploader.main:app", "--host", "0.0.0.0", "--port", "8000"]

