"""Канонизация и SHA-256 для идентификации артефактов."""

import hashlib
import json
from pathlib import Path
from typing import Any

SHA256_PREFIX = "sha256:"


def canonical_json(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes used by contract hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hash_bytes(value: bytes) -> str:
    return SHA256_PREFIX + hashlib.sha256(value).hexdigest()


def hash_json(value: Any) -> str:
    return hash_bytes(canonical_json(value))


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return SHA256_PREFIX + digest.hexdigest()


def is_sha256(value: str) -> bool:
    if not value.startswith(SHA256_PREFIX):
        return False
    hex_value = value.removeprefix(SHA256_PREFIX)
    return len(hex_value) == 64 and all(character in "0123456789abcdef" for character in hex_value)


def require_sha256(value: str, field_name: str) -> None:
    if not is_sha256(value):
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex characters>")
