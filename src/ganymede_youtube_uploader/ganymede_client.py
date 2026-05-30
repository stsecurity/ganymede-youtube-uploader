from typing import Any

import httpx


class GanymedeClientError(RuntimeError):
    pass


class GanymedeClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
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

    async def lock_vod(self, vod_id: str, locked: bool = True) -> dict[str, Any] | None:
        return await self._request("PATCH", f"vod/{vod_id}", json={"locked": locked})

    async def delete_vod(self, vod_id: str, delete_files: bool = True) -> dict[str, Any] | None:
        return await self._request("DELETE", f"vod/{vod_id}", params={"delete_files": delete_files})

    async def get_ffprobe(self, vod_id: str) -> dict[str, Any] | None:
        return await self._request("GET", f"vod/{vod_id}/ffprobe")
