import argparse
import sys
from pathlib import Path

repo_src = Path(__file__).resolve().parents[1] / "src"
if str(repo_src) not in sys.path:
    sys.path.insert(0, str(repo_src))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a YouTube OAuth token for uploads.")
    parser.add_argument("--client-secret", type=Path, help="Path to the YouTube OAuth client JSON.")
    parser.add_argument(
        "--token-file", type=Path, help="Path where the OAuth token JSON will be saved."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local callback port for the OAuth flow. Publish this port when running in Docker.",
    )
    parser.add_argument(
        "--redirect-host",
        default="localhost",
        help=(
            "Host used in the OAuth redirect URL. Use localhost with a local Docker host "
            "or SSH tunnel."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Seconds to wait for the browser callback after printing the auth URL.",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Try to open a browser from the current environment.",
    )
    args = parser.parse_args()

    if args.client_secret and args.token_file:
        client_secret = args.client_secret
        token_file = args.token_file
    else:
        from ganymede_youtube_uploader.config import get_settings

        settings = get_settings()
        client_secret = args.client_secret or settings.youtube_client_secret_file
        token_file = args.token_file or settings.youtube_token_file

    token_file.parent.mkdir(parents=True, exist_ok=True)
    from ganymede_youtube_uploader.youtube_client import oauth_interactive

    try:
        oauth_interactive(
            client_secret,
            token_file,
            port=args.port,
            open_browser=args.open_browser,
            redirect_host=args.redirect_host,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "MismatchingStateError":
            raise SystemExit(
                "OAuth callback state did not match. Restart this command, close old auth "
                "tabs, and open only the newly printed Google URL. If the port may have a "
                "stale callback, retry with a different --port and matching Docker publish."
            ) from exc
        raise
    print(f"Saved YouTube token to {token_file}")


if __name__ == "__main__":
    main()
