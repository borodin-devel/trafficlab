"""Чтение и запись JSON-манифестов контрактов."""

import json
from pathlib import Path
from typing import Any

from trafficlab.runstore.atomic import atomic_write_json


def write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_json(path, value)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value
