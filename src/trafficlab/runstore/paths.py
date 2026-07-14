"""Имена каталогов запуска и стадий по файловым контрактам."""

from datetime import datetime, timezone
from pathlib import Path
import secrets

CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def new_ulid_like() -> str:
    """Return a dependency-free, lexicographically sortable ULID-shaped id."""
    # 26 Crockford-base32 characters, enough for the run-directory contract shape.
    return "".join(secrets.choice(CROCKFORD32) for _ in range(26))


def new_run_id() -> str:
    return f"{timestamp_utc()}_{new_ulid_like()}"


def stage_directory_name(stage_type: str) -> str:
    return stage_type.replace("-", "_")


def stage_path(run_path: Path, stage_type: str) -> Path:
    return run_path / "stages" / stage_directory_name(stage_type)
