from typing import Any

import httpx


class GanymedeClientError(RuntimeError):
    pass


class GanymedeClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 30.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as client:
            response = await client.request(method, self._url(path), **kwargs)
        if response.status_code == 404:
            raise GanymedeClientError(f"Ganymede endpoint not found: {path}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GanymedeClientError(f"Ganymede API error {response.status_code}: {path}") from exc
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def healthcheck(self) -> bool:
        try:
            await self._request("GET", "health")
        except GanymedeClientError:
            await self._request("GET", "vod", params={"limit": 1})
        return True

    async def get_vod(
        self, vod_id: str, with_channel: bool = True, with_queue: bool = True
    ) -> dict[str, Any]:
        return _single_vod(
            await self._request(
                "GET",
                f"vod/{vod_id}",
                params={"with_channel": with_channel, "with_queue": with_queue},
            ),
            f"No Ganymede VOD found for id {vod_id}",
        )

    async def get_vod_by_external_id(
        self, external_id: str, with_channel: bool = True, with_queue: bool = True
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "vod",
            params={
                "external_id": external_id,
                "with_channel": with_channel,
                "with_queue": with_queue,
            },
        )
        return _single_vod(data, f"No Ganymede VOD found for external id {external_id}")

    async def list_vods(
        self,
        channel_name: str = "",
        limit: int = 100,
        with_channel: bool = True,
        with_queue: bool = True,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "with_channel": with_channel,
            "with_queue": with_queue,
            "limit": limit,
        }
        if channel_name:
            params["channel_name"] = channel_name
        data = await self._request("GET", "vod", params=params)
        vods = _vod_items(data)
        if channel_name:
            vods = [vod for vod in vods if _vod_matches_channel(vod, channel_name)]
        return vods[:limit]

    async def find_vod_by_title_and_channel(
        self,
        title: str,
        channel_name: str,
        with_channel: bool = True,
        with_queue: bool = True,
    ) -> dict[str, Any]:
        matches = await self.find_vods_by_title_and_channel(
            title,
            channel_name,
            with_channel=with_channel,
            with_queue=with_queue,
        )
        if matches:
            return matches[0]
        raise GanymedeClientError(
            f"No Ganymede VOD found for title {title!r} and channel {channel_name!r}"
        )

    async def find_vods_by_title_and_channel(
        self,
        title: str,
        channel_name: str,
        with_channel: bool = True,
        with_queue: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "vod",
            params={
                "title": title,
                "channel_name": channel_name,
                "with_channel": with_channel,
                "with_queue": with_queue,
                "limit": limit,
            },
        )
        candidates = _vod_items(data)
        matches = []
        for vod in candidates:
            if _normalized(vod.get("title")) != _normalized(title):
                continue
            if _vod_matches_channel(vod, channel_name):
                matches.append(vod)
        return matches

    async def lock_vod(self, vod_id: str, locked: bool = True) -> dict[str, Any] | None:
        return await self._request("PATCH", f"vod/{vod_id}", json={"locked": locked})

    async def delete_vod(self, vod_id: str, delete_files: bool = True) -> dict[str, Any] | None:
        return await self._request("DELETE", f"vod/{vod_id}", params={"delete_files": delete_files})

    async def get_ffprobe(self, vod_id: str) -> dict[str, Any] | None:
        return await self._request("GET", f"vod/{vod_id}/ffprobe")


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _vod_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [vod for vod in data if isinstance(vod, dict)]
    if not isinstance(data, dict):
        return []
    items = data.get("items", data.get("data", []))
    return [vod for vod in items if isinstance(vod, dict)] if isinstance(items, list) else []


def _single_vod(data: Any, error: str) -> dict[str, Any]:
    if isinstance(data, dict):
        wrapped = data.get("data")
        if isinstance(wrapped, dict):
            return wrapped
        if isinstance(wrapped, list) and wrapped and isinstance(wrapped[0], dict):
            return wrapped[0]
        if "id" in data or "video_path" in data or "videoPath" in data:
            return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    raise GanymedeClientError(error)


def _vod_matches_channel(vod: dict[str, Any], channel_name: str) -> bool:
    channel = _channel_from_vod(vod)
    names = {
        vod.get("channel_name"),
        vod.get("channelName"),
        vod.get("channel_display_name"),
        vod.get("channelDisplayName"),
        channel.get("name"),
        channel.get("display_name"),
        channel.get("displayName"),
    }
    return _normalized(channel_name) in {_normalized(name) for name in names if name}


def _channel_from_vod(vod: dict[str, Any]) -> dict[str, Any]:
    channel = vod.get("channel")
    if isinstance(channel, dict):
        return channel
    edges = vod.get("edges")
    if isinstance(edges, dict) and isinstance(edges.get("channel"), dict):
        return edges["channel"]
    return {}


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/v1"):
        return normalized
    return f"{normalized}/api/v1"
