from pathlib import Path

import pytest

from ganymede_youtube_uploader.path_resolver import GanymedePathResolver, PathResolutionError


def test_maps_ganymede_path_to_mount(tmp_path: Path) -> None:
    resolver = GanymedePathResolver(tmp_path, "/data/videos")

    resolved = resolver.resolve("/data/videos/channel/video.mp4")

    assert resolved == (tmp_path / "channel" / "video.mp4").resolve()


def test_rejects_path_outside_ganymede_root(tmp_path: Path) -> None:
    resolver = GanymedePathResolver(tmp_path, "/data/videos")

    with pytest.raises(PathResolutionError):
        resolver.resolve("/etc/passwd")


def test_rejects_path_traversal(tmp_path: Path) -> None:
    resolver = GanymedePathResolver(tmp_path, "/data/videos")

    with pytest.raises(PathResolutionError):
        resolver.resolve("/data/videos/../secret.mp4")
