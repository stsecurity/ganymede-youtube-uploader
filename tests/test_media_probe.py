import json
from pathlib import Path

from ganymede_youtube_uploader.media_probe import duration_within_tolerance, parse_ffprobe


def test_parse_ffprobe_fixture() -> None:
    data = json.loads(Path("tests/fixtures/ffprobe_video.json").read_text())

    result = parse_ffprobe(data)

    assert result.duration_seconds == 120
    assert result.has_video is True
    assert result.has_audio is True


def test_duration_tolerance() -> None:
    assert duration_within_tolerance(100, 120, 20)
    assert not duration_within_tolerance(99, 120, 20)
