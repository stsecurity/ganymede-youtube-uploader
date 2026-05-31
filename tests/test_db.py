from pathlib import Path

from ganymede_youtube_uploader.db import ensure_sqlite_parent, make_engine


def test_sqlite_parent_directory_is_created(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "uploader.sqlite"

    ensure_sqlite_parent(f"sqlite:///{database_path}")

    assert database_path.parent.is_dir()


def test_make_engine_can_create_sqlite_file_in_new_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "new-data" / "uploader.sqlite"
    engine = make_engine(f"sqlite:///{database_path}")

    with engine.begin() as connection:
        connection.exec_driver_sql("select 1")

    assert database_path.exists()
