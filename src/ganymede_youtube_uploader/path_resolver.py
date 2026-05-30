from pathlib import Path


class PathResolutionError(ValueError):
    pass


class GanymedePathResolver:
    def __init__(self, videos_mount: Path, ganymede_root: str) -> None:
        self.videos_mount = videos_mount.resolve()
        self.ganymede_root = ganymede_root.rstrip("/")

    def resolve(self, ganymede_path: str) -> Path:
        if not ganymede_path:
            raise PathResolutionError("Missing Ganymede video path")
        normalized = ganymede_path.replace("\\", "/")
        root = self.ganymede_root
        if normalized != root and not normalized.startswith(f"{root}/"):
            raise PathResolutionError("Ganymede path is outside configured root")
        relative = normalized.removeprefix(root).lstrip("/")
        candidate = (self.videos_mount / relative).resolve()
        if candidate != self.videos_mount and self.videos_mount not in candidate.parents:
            raise PathResolutionError("Resolved path escapes configured videos mount")
        return candidate
