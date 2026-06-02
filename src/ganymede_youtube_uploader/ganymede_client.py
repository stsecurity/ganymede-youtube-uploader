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
        return await self._request(
            "GET",
            f"vod/{vod_id}",
            params={"with_channel": with_channel, "with_queue": with_queue},
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
        if isinstance(data, list):
            if not data:
                raise GanymedeClientError(f"No Ganymede VOD found for external id {external_id}")
            return data[0]
        return data

    async def find_vod_by_title_and_channel(
        self,
        title: str,
        channel_name: str,
        with_channel: bool = True,
        with_queue: bool = True,
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "vod",
            params={
                "title": title,
                "channel_name": channel_name,
                "with_channel": with_channel,
                "with_queue": with_queue,
                "limit": 25,
            },
        )
        candidates = data if isinstance(data, list) else data.get("items", data.get("data", []))
        for vod in candidates:
            if _normalized(vod.get("title")) != _normalized(title):
                continue
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
            if _normalized(channel_name) in {_normalized(name) for name in names if name}:
                return vod
        raise GanymedeClientError(
            f"No Ganymede VOD found for title {title!r} and channel {channel_name!r}"
        )

    async def lock_vod(self, vod_id: str, locked: bool = True) -> dict[str, Any] | None:
        return await self._request("PATCH", f"vod/{vod_id}", json={"locked": locked})

    async def delete_vod(self, vod_id: str, delete_files: bool = True) -> dict[str, Any] | None:
        return await self._request("DELETE", f"vod/{vod_id}", params={"delete_files": delete_files})

    async def get_ffprobe(self, vod_id: str) -> dict[str, Any] | None:
        return await self._request("GET", f"vod/{vod_id}/ffprobe")


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


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
