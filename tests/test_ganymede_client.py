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
