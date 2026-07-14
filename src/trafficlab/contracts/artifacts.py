"""Ссылки на опубликованные артефакты."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    artifact_type: str
    relative_path: str


@dataclass(frozen=True)
class StageResult:
    stage_key: str
    outputs: tuple[ArtifactRef, ...] = ()
