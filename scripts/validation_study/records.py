"""Records owner for Validation Study tooling."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol

from scripts.validation_study.common import require_frozen_mapping, require_type

if TYPE_CHECKING:
    from scripts.validation_study.common import FrozenJsonObject, WorkloadName
    from trafficlab.common.compatibility import ContentIdentity
    from trafficlab.comparison.schema import ComparisonResult
    from trafficlab.generation.models.fitted_model import BestModel


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class HeldOutEvaluation:
    """One study-only evaluation of a frozen training model on an independent capture."""

    training_model: BestModel
    training_model_identity: ContentIdentity
    capture_identity: ContentIdentity
    reference_identity: ContentIdentity
    generated_identity: ContentIdentity
    similarity_settings_identity: ContentIdentity
    generated_pcapng: bytes
    comparison: ComparisonResult
    comparison_json: bytes
    seed: int
    observation_window_seconds: float


@dataclass(frozen=True, slots=True)
class StudyRunSpec:
    execution_order: int
    run_id: str
    workload: WorkloadName
    repeat: int
    config_path: Path
    run_directory: Path
    transfer_evidence_directory: Path


@dataclass(frozen=True, slots=True)
class StudyRunRecord:
    execution_order: int
    run_id: str
    key: FrozenJsonObject
    config_path: str
    run_directory: str
    transfer_evidence_directory: str
    elapsed_seconds: float
    reuse: FrozenJsonObject
    cleanup_verified: bool
    transfer_responses: tuple[FrozenJsonObject, ...]
    artifact_sha256: FrozenJsonObject
    reference: FrozenJsonObject
    generated: FrozenJsonObject
    family_champions: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    winner: FrozenJsonObject
    fresh_simulation: FrozenJsonObject
    published: FrozenJsonObject
    raw_sequence: FrozenJsonObject

    def __post_init__(self) -> None:
        for name in (
            "key",
            "reuse",
            "artifact_sha256",
            "reference",
            "generated",
            "winner",
            "fresh_simulation",
            "published",
            "raw_sequence",
        ):
            require_frozen_mapping(getattr(self, name), name=name)
        require_type(
            type(self.transfer_responses) is tuple
            and all(type(item) is MappingProxyType for item in self.transfer_responses),
            "transfer_responses must be a tuple of frozen JSON objects",
        )
        require_type(
            type(self.family_champions) is tuple
            and len(self.family_champions) == 3
            and all(type(item) is MappingProxyType for item in self.family_champions),
            "family_champions must be three frozen JSON objects",
        )


@dataclass(frozen=True, slots=True)
class ReproductionRecord:
    document: FrozenJsonObject

    def __post_init__(self) -> None:
        require_frozen_mapping(self.document, name="reproduction document")


@dataclass(frozen=True, slots=True)
class StudyResults:
    schema_version: int
    environment: FrozenJsonObject
    protocol: FrozenJsonObject
    runs: tuple[StudyRunRecord, ...]
    natural_variation: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    workload_summaries: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    reproduction: ReproductionRecord

    def __post_init__(self) -> None:
        require_frozen_mapping(self.environment, name="environment")
        require_frozen_mapping(self.protocol, name="protocol")
        require_type(
            type(self.runs) is tuple and all(type(item) is StudyRunRecord for item in self.runs),
            "runs must be a tuple of study run records",
        )
        for name, value in (
            ("natural_variation", self.natural_variation),
            ("workload_summaries", self.workload_summaries),
        ):
            require_type(
                type(value) is tuple and len(value) == 3 and all(type(item) is MappingProxyType for item in value),
                f"{name} must be three frozen JSON objects",
            )
        require_type(type(self.reproduction) is ReproductionRecord, "reproduction must be a reproduction record")


@dataclass(frozen=True, slots=True)
class PrerequisiteResults:
    schema_version: int
    created_utc: str
    study_id: str
    git_commit: str
    git_tree_clean: bool
    url: str
    tools: FrozenJsonObject
    images: FrozenJsonObject
    capability: FrozenJsonObject
    config_sha256: FrozenJsonObject
    commands: tuple[FrozenJsonObject, FrozenJsonObject]

    def __post_init__(self) -> None:
        for name in ("tools", "images", "capability", "config_sha256"):
            require_frozen_mapping(getattr(self, name), name=name)
        require_type(
            type(self.commands) is tuple
            and len(self.commands) == 2
            and all(type(item) is MappingProxyType for item in self.commands),
            "commands must be two frozen JSON objects",
        )
