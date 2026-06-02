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
