import pytest

from ganymede_youtube_uploader.ganymede_client import GanymedeClient


def test_ganymede_url_construction() -> None:
    client = GanymedeClient("http://ganymede:4000/api/v1/")

    assert client._url("/vod/abc") == "http://ganymede:4000/api/v1/vod/abc"


def test_ganymede_url_adds_api_prefix_when_missing() -> None:
    client = GanymedeClient("https://twitch.stsecurity.moe/")

    assert client._url("/vod/abc") == "https://twitch.stsecurity.moe/api/v1/vod/abc"


def test_auth_header_only_when_configured() -> None:
    assert GanymedeClient("http://x")._headers() == {}
    assert GanymedeClient("http://x", "secret")._headers() == {"Authorization": "Bearer secret"}


@pytest.mark.asyncio
async def test_find_vod_matches_edges_channel_shape() -> None:
    client = GanymedeClient("http://ganymede:4000")

    async def fake_request(method: str, path: str, **kwargs):
        return {
            "success": True,
            "data": [
                {
                    "id": "vod-1",
                    "ext_id": "2786077556",
                    "title": "Imaginarium theater",
                    "edges": {
                        "channel": {
                            "name": "stsecurity",
                            "display_name": "stsecurity",
                        }
                    },
                }
            ],
        }

    client._request = fake_request

    vod = await client.find_vod_by_title_and_channel("Imaginarium theater", "stsecurity")

    assert vod["id"] == "vod-1"


@pytest.mark.asyncio
async def test_find_vods_returns_all_matching_title_and_channel_vods() -> None:
    client = GanymedeClient("http://ganymede:4000")

    async def fake_request(method: str, path: str, **kwargs):
        return {
            "data": [
                {
                    "id": "vod-1",
                    "title": "Same Title",
                    "edges": {"channel": {"name": "stsecurity"}},
                },
                {
                    "id": "vod-2",
                    "title": "Same Title",
                    "edges": {"channel": {"name": "stsecurity"}},
                },
                {
                    "id": "vod-3",
                    "title": "Other Title",
                    "edges": {"channel": {"name": "stsecurity"}},
                },
            ]
        }

    client._request = fake_request

    vods = await client.find_vods_by_title_and_channel("Same Title", "stsecurity")

    assert [vod["id"] for vod in vods] == ["vod-1", "vod-2"]


@pytest.mark.asyncio
async def test_list_vods_filters_edges_channel_shape() -> None:
    client = GanymedeClient("http://ganymede:4000")

    async def fake_request(method: str, path: str, **kwargs):
        return {
            "data": [
                {
                    "id": "vod-1",
                    "title": "Tracked",
                    "edges": {"channel": {"name": "stsecurity"}},
                },
                {
                    "id": "vod-2",
                    "title": "Other",
                    "edges": {"channel": {"name": "other"}},
                },
            ]
        }

    client._request = fake_request

    vods = await client.list_vods(channel_name="stsecurity")

    assert [vod["id"] for vod in vods] == ["vod-1"]
