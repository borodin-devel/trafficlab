"""Сериализуемое состояние одного запуска стадии."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from trafficlab.contracts.stage import StageStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class StageState:
    stage_type: str
    stage_key: str
    normalized_config: dict[str, Any]
    ordered_input_hashes: list[str]
    implementation_version: str
    status: StageStatus = StageStatus.CREATED
    schema_version: str = "1.0.0"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    output_hashes: dict[str, str] = field(default_factory=dict)
    error: dict[str, str] | None = None

    def start(self) -> None:
        self.status = StageStatus.RUNNING
        self.started_at = utc_now()

    def complete(self, outputs: dict[str, str]) -> None:
        self.status = StageStatus.COMPLETED
        self.output_hashes = outputs
        self.finished_at = utc_now()

    def fail(self, code: str, message: str) -> None:
        self.status = StageStatus.FAILED
        self.error = {"code": code, "message": message}
        self.finished_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value
