"""Минимальная модель манифеста dataset package."""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any


@dataclass
class DatasetManifest:
    source: dict[str, Any]
    files: dict[str, str]
    artifact_type: str = "traffic_dataset"
    artifact_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    artifact_id: str | None = None
    created_at: str | None = None
    lineage: list[dict[str, Any]] = field(default_factory=list)

    def identity_payload(self) -> dict[str, Any]:
        payload = {
            "artifact_type": self.artifact_type,
            "artifact_version": self.artifact_version,
            "schema_version": self.schema_version,
            "source": self.source,
            "files": {k: v for k, v in self.files.items() if k not in {"validation.json", "summary.md"}},
            "lineage": self.lineage,
        }
        return payload

    def compute_artifact_id(self) -> str:
        encoded = json.dumps(self.identity_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        self.artifact_id = "sha256:" + hashlib.sha256(encoded).hexdigest()
        return self.artifact_id
