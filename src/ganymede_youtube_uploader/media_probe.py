import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MediaProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: int
    has_video: bool
    has_audio: bool
    raw: dict[str, Any]


def parse_ffprobe(data: dict[str, Any]) -> ProbeResult:
    streams = data.get("streams") or []
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration = data.get("format", {}).get("duration")
    if duration is None:
        durations = [stream.get("duration") for stream in streams if stream.get("duration")]
        duration = durations[0] if durations else None
    if duration is None:
        raise MediaProbeError("ffprobe output did not include duration")
    return ProbeResult(
        duration_seconds=round(float(duration)),
        has_video=has_video,
        has_audio=has_audio,
        raw=data,
    )


def run_ffprobe(path: Path) -> ProbeResult:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MediaProbeError(result.stderr.strip() or "ffprobe failed")
    return parse_ffprobe(json.loads(result.stdout))


def duration_within_tolerance(actual: int, expected: int | None, tolerance: int) -> bool:
    if expected is None:
        return True
    return abs(actual - expected) <= tolerance
