from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from trafficlab.common.config import ExperimentConfig
from trafficlab.common.trace import CaptureMetadata, TrafficTrace
from trafficlab.comparison.schema import (
    C2stDiagnostic,
    ComparisonResult,
    FanoAllanDiagnostic,
    TransitionMatrixDiagnostic,
)
from trafficlab.fitting.genetic.types import HistoryRow
from trafficlab.generation.models import BestModel

type ArtifactAvailability = Mapping[str, str]

_HEX_DIGEST_LENGTH: Final[int] = 64


def _require_sha256(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError(f"{name} must be a string or None")
    if len(value) != _HEX_DIGEST_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return value


@dataclass(frozen=True, slots=True)
class ArtifactIdentities:
    reference_sha256: str
    generated_sha256: str
    capture_sha256: str
    similarity_sha256: str | None
    best_model_sha256: str | None
    history_sha256: str | None
    experiment_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_sha256", _require_sha256(self.reference_sha256, name="reference_sha256"))
        object.__setattr__(self, "generated_sha256", _require_sha256(self.generated_sha256, name="generated_sha256"))
        object.__setattr__(self, "capture_sha256", _require_sha256(self.capture_sha256, name="capture_sha256"))
        object.__setattr__(self, "similarity_sha256", _require_sha256(self.similarity_sha256, name="similarity_sha256"))
        object.__setattr__(self, "best_model_sha256", _require_sha256(self.best_model_sha256, name="best_model_sha256"))
        object.__setattr__(self, "history_sha256", _require_sha256(self.history_sha256, name="history_sha256"))
        object.__setattr__(
            self,
            "experiment_sha256",
            _require_sha256(self.experiment_sha256, name="experiment_sha256"),
        )


@dataclass(frozen=True, slots=True)
class DashboardRun:
    directory: Path
    identities: ArtifactIdentities
    metadata: CaptureMetadata
    reference: TrafficTrace
    generated: TrafficTrace
    window: float
    similarity: ComparisonResult | None
    best_model: BestModel | None
    history: tuple[HistoryRow, ...] | None
    experiment: ExperimentConfig | None
    unavailable: ArtifactAvailability

    def __post_init__(self) -> None:
        if type(self.identities) is not ArtifactIdentities:
            raise TypeError("identities must be ArtifactIdentities")
        if type(self.metadata) is not CaptureMetadata:
            raise TypeError("metadata must be CaptureMetadata")
        if type(self.reference) is not TrafficTrace:
            raise TypeError("reference must be a TrafficTrace")
        if type(self.generated) is not TrafficTrace:
            raise TypeError("generated must be a TrafficTrace")
        if type(self.window) is not float or self.window <= 0.0:
            raise ValueError("window must be a positive float")
        if self.similarity is not None and type(self.similarity) is not ComparisonResult:
            raise TypeError("similarity must be a ComparisonResult or None")
        if self.best_model is not None and type(self.best_model) is not BestModel:
            raise TypeError("best_model must be a BestModel or None")
        if self.history is not None:
            if type(self.history) is not tuple or any(type(row) is not HistoryRow for row in self.history):
                raise TypeError("history must be a tuple of HistoryRow values or None")
        if self.experiment is not None and type(self.experiment) is not ExperimentConfig:
            raise TypeError("experiment must be an ExperimentConfig or None")
        unavailable: ArtifactAvailability = self.unavailable
        if type(self.unavailable) is not MappingProxyType:
            frozen: dict[str, str] = {name: reason for name, reason in unavailable.items()}
            object.__setattr__(self, "unavailable", MappingProxyType(frozen))
            unavailable = frozen
        if not all(type(name) is str and type(reason) is str for name, reason in unavailable.items()):
            raise TypeError("unavailable must map strings to strings")

    @property
    def reference_packet_count(self) -> int:
        return len(self.reference)

    @property
    def generated_packet_count(self) -> int:
        return len(self.generated)

    @property
    def fano_allan_diagnostic(self) -> FanoAllanDiagnostic | None:
        """Return the validated stored Fano/Allan diagnostic, never recomputed."""
        if self.similarity is None or self.similarity.postfit_diagnostics is None:
            return None
        return self.similarity.postfit_diagnostics.fano_allan.diagnostics

    @property
    def transition_fidelity_diagnostic(self) -> TransitionMatrixDiagnostic | None:
        """Return the validated stored transition-fidelity diagnostic, never recomputed."""
        if self.similarity is None or self.similarity.postfit_diagnostics is None:
            return None
        return self.similarity.postfit_diagnostics.transition_matrix.diagnostics

    @property
    def c2st_diagnostic(self) -> C2stDiagnostic | None:
        """Return the validated stored C2ST diagnostic, never refit."""
        if self.similarity is None or self.similarity.postfit_diagnostics is None:
            return None
        return self.similarity.postfit_diagnostics.classical_c2st.diagnostics
