from ganymede_youtube_uploader.config import get_settings
from ganymede_youtube_uploader.youtube_client import oauth_interactive


def main() -> None:
    settings = get_settings()
    oauth_interactive(settings.youtube_client_secret_file, settings.youtube_token_file)
    print(f"Saved YouTube token to {settings.youtube_token_file}")


if __name__ == "__main__":
    main()
