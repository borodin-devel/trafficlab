"""Контракт состояния стадии и детерминированный ключ запуска."""

from dataclasses import dataclass
import hashlib
import json
from enum import StrEnum
from typing import Any


class StageStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INVALID = "invalid"


@dataclass(frozen=True)
class StageKeyInput:
    stage_type: str
    normalized_config: dict[str, Any]
    ordered_input_hashes: tuple[str, ...]
    implementation_version: str


def compute_stage_key(value: StageKeyInput) -> str:
    payload = {
        "stage_type": value.stage_type,
        "normalized_config": value.normalized_config,
        "ordered_input_hashes": list(value.ordered_input_hashes),
        "implementation_version": value.implementation_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
