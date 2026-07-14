"""Контракт манифеста dataset package."""

from dataclasses import dataclass, field
from typing import Any

from trafficlab.contracts.stage_state import utc_now
from trafficlab.runstore.hashes import hash_json

DERIVED_DATASET_FILES = {"validation.json", "summary.md"}


@dataclass
class DatasetManifest:
    source: dict[str, Any]
    files: dict[str, str]
    row_counts: dict[str, int] = field(default_factory=lambda: {"packets": 0, "flows": 0, "sessions": 0})
    decoder: dict[str, Any] | None = None
    time_unit: str = "nanosecond"
    payload_retained: bool = False
    artifact_type: str = "traffic_dataset"
    artifact_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    artifact_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    lineage: list[dict[str, Any]] = field(default_factory=list)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "artifact_version": self.artifact_version,
            "schema_version": self.schema_version,
            "source": self.source,
            "decoder": self.decoder,
            "row_counts": self.row_counts,
            "time_unit": self.time_unit,
            "payload_retained": self.payload_retained,
            "files": {key: value for key, value in self.files.items() if key not in DERIVED_DATASET_FILES},
            "lineage": self.lineage,
        }

    def compute_artifact_id(self) -> str:
        self.artifact_id = hash_json(self.identity_payload())
        return self.artifact_id

    def to_dict(self) -> dict[str, Any]:
        artifact_id = self.artifact_id or self.compute_artifact_id()
        return {
            "artifact_type": self.artifact_type,
            "artifact_version": self.artifact_version,
            "schema_version": self.schema_version,
            "artifact_id": artifact_id,
            "created_at": self.created_at,
            "source": self.source,
            "decoder": self.decoder,
            "row_counts": self.row_counts,
            "time_unit": self.time_unit,
            "payload_retained": self.payload_retained,
            "files": self.files,
            "lineage": self.lineage,
        }

    def validate(self) -> None:
        if self.artifact_type != "traffic_dataset":
            raise ValueError("artifact_type must be traffic_dataset")
        if self.time_unit != "nanosecond":
            raise ValueError("time_unit must be nanosecond")
        for key in ("packets", "flows", "sessions"):
            if key not in self.row_counts:
                raise ValueError(f"row_counts must include {key}")
