"""Сериализуемое состояние одного запуска стадии."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from trafficlab.contracts.stage import StageStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        if self.status is not StageStatus.CREATED:
            raise ValueError("only created stages can start")
        self.status = StageStatus.RUNNING
        self.started_at = utc_now()
        self.finished_at = None

    def complete(self, outputs: dict[str, str]) -> None:
        if self.status is not StageStatus.RUNNING:
            raise ValueError("only running stages can complete")
        self.status = StageStatus.COMPLETED
        self.output_hashes = outputs
        self.error = None
        self.finished_at = utc_now()

    def fail(self, code: str, message: str) -> None:
        if self.status in {StageStatus.COMPLETED, StageStatus.CANCELLED}:
            raise ValueError("completed or cancelled stages cannot fail")
        if self.started_at is None:
            self.started_at = utc_now()
        self.status = StageStatus.FAILED
        self.error = {"code": code, "message": message}
        self.finished_at = utc_now()

    def cancel(self, code: str = "cancelled", message: str = "Stage was cancelled") -> None:
        if self.status is StageStatus.COMPLETED:
            raise ValueError("completed stages cannot be cancelled")
        self.status = StageStatus.CANCELLED
        self.error = {"code": code, "message": message}
        self.finished_at = utc_now()

    def invalidate(self, code: str, message: str) -> None:
        if self.status is StageStatus.COMPLETED:
            raise ValueError("completed stages cannot be invalidated")
        if self.started_at is None:
            self.started_at = utc_now()
        self.status = StageStatus.INVALID
        self.error = {"code": code, "message": message}
        self.finished_at = utc_now()

    def validate(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("schema_version must be 1.0.0")
        if self.status in {StageStatus.CREATED, StageStatus.RUNNING} and self.finished_at is not None:
            raise ValueError("created and running stages must not have finished_at")
        if self.status is StageStatus.CREATED and self.started_at is not None:
            raise ValueError("created stages must not have started_at")
        if self.status is StageStatus.COMPLETED:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("completed stages require started_at and finished_at")
            if self.error is not None:
                raise ValueError("completed stages must not have error")
        if self.status in {StageStatus.FAILED, StageStatus.CANCELLED, StageStatus.INVALID}:
            if self.finished_at is None:
                raise ValueError("terminal unsuccessful stages require finished_at")
            if self.error is None:
                raise ValueError("terminal unsuccessful stages require error")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        value = asdict(self)
        value["status"] = self.status.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StageState":
        data = dict(value)
        data["status"] = StageStatus(data["status"])
        state = cls(**data)
        state.validate()
        return state
