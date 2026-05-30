import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("vod_id")
    parser.add_argument("--secret", default="")
    args = parser.parse_args()
    headers = {"X-Webhook-Secret": args.secret} if args.secret else {}
    response = httpx.post(
        f"{args.base_url.rstrip('/')}/webhooks/ganymede",
        json={"vod_id": args.vod_id},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
