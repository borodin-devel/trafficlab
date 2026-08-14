"""Shared result and validation primitives for similarity methods."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from trafficlab.errors import TrafficlabError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type FrozenJsonValue = JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
type JsonDiagnostics = Mapping[str, FrozenJsonValue]


def _freeze_json(value: object) -> FrozenJsonValue:
    """Recursively validate and freeze one JSON-compatible value."""
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is str:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("diagnostic floats must be finite")
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        frozen_mapping: dict[str, FrozenJsonValue] = {}
        for key, nested_value in mapping.items():
            if type(key) is not str:
                raise ValueError("diagnostic mapping keys must be strings")
            frozen_mapping[key] = _freeze_json(nested_value)
        return MappingProxyType(frozen_mapping)
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(_freeze_json(item) for item in sequence)
    raise ValueError("diagnostics must contain only JSON-compatible values")


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    """Recursively copy one frozen JSON-compatible value into ordinary JSON data."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested_value) for key, nested_value in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, init=False)
class SimilarityResult:
    """One immutable bounded similarity score with JSON-compatible diagnostics."""

    score: float
    diagnostics: JsonDiagnostics

    def __init__(self, score: float, diagnostics: Mapping[str, object]) -> None:
        if type(score) is not float or not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("score must be a finite float in [0, 1]")
        frozen_diagnostics = _freeze_json(diagnostics)
        if not isinstance(frozen_diagnostics, Mapping):
            raise ValueError("diagnostics must be a mapping")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "diagnostics", frozen_diagnostics)

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a fresh JSON-compatible representation for artifact serialization."""
        return {"score": self.score, "diagnostics": _thaw_json(self.diagnostics)}


def validate_observation_window(W: object) -> float:
    """Validate and return the one finite positive observation window shared by metrics."""
    if type(W) is not float or not math.isfinite(W) or W <= 0.0:
        raise TrafficlabError(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide the finite positive window derived from the reference trace",
        )
    return W
