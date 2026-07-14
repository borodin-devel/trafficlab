"""Атомарная публикация файлов и каталогов артефактов."""

from collections.abc import Callable
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def publish_directory(destination: Path, writer: Callable[[Path], None]) -> Path:
    """Publish a directory after `writer` finishes successfully.

    The destination must not already exist. Failed writes leave no partial public
    artifact at `destination`.
    """
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        writer(temporary)
        temporary.replace(destination)
        return destination
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
