"""Shared result and validation primitives for similarity methods."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.errors import TrafficlabError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type FrozenJsonValue = JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
type JsonDiagnostics = Mapping[str, FrozenJsonValue]


def validated_numeric_sample(
    values: Iterable[object],
    *,
    error_name: str,
    corrective_action: str,
    require_nonempty: bool,
) -> tuple[int | float, ...]:
    """Materialize one exact finite numeric compatibility sample."""
    try:
        sample = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {error_name}: values must be iterable", corrective_action=corrective_action
        ) from error
    if require_nonempty and not sample:
        raise TrafficlabError(
            f"invalid {error_name}: at least one value is required", corrective_action=corrective_action
        )
    if any(type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) for value in sample):
        raise TrafficlabError(
            f"invalid {error_name}: values must be finite numbers", corrective_action=corrective_action
        )
    return cast(tuple[int | float, ...], sample)


def validated_numeric_array(
    values: Iterable[object] | NDArray[np.generic],
    *,
    error_name: str,
    corrective_action: str,
    require_nonempty: bool,
    as_float64: bool,
) -> NDArray[np.generic]:
    """Validate one numeric vector and optionally convert it to float64 once."""
    raw: object = values
    array = cast(NDArray[np.generic], raw) if isinstance(raw, np.ndarray) else None
    if array is None:
        sample = validated_numeric_sample(
            cast(Iterable[object], values),
            error_name=error_name,
            corrective_action=corrective_action,
            require_nonempty=require_nonempty,
        )
        array = np.asarray(sample)
    if (
        array.ndim != 1
        or (require_nonempty and not len(array))
        or not (np.issubdtype(array.dtype, np.integer) or np.issubdtype(array.dtype, np.floating))
    ):
        raise TrafficlabError(
            f"invalid {error_name}: values must be finite numbers", corrective_action=corrective_action
        )
    try:
        numeric = np.asarray(array, dtype=np.float64) if as_float64 else array
    except (OverflowError, TypeError, ValueError) as error:
        raise TrafficlabError(
            f"invalid {error_name}: values cannot be evaluated safely", corrective_action=corrective_action
        ) from error
    if not np.all(np.isfinite(numeric)):
        raise TrafficlabError(
            f"invalid {error_name}: values must be finite numbers", corrective_action=corrective_action
        )
    return numeric


def validated_weights(
    values: Iterable[object], *, name: str, expected_length: int | None = None, count_name: str = "item"
) -> tuple[float, ...]:
    """Return one finite nonnegative vector whose precise sum is one."""
    try:
        weights = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {name}: weights must be iterable",
            corrective_action="provide finite nonnegative weights that sum to one",
        ) from error
    if expected_length is not None and len(weights) != expected_length:
        raise TrafficlabError(
            f"invalid {name}: weight count must match {count_name} count",
            corrective_action=f"provide one finite normalized weight for every {count_name}",
        )
    if not weights or any(type(weight) is not float or not math.isfinite(weight) or weight < 0.0 for weight in weights):
        raise TrafficlabError(
            f"invalid {name}: weights must be finite nonnegative floats",
            corrective_action="provide finite nonnegative weights that sum to one",
        )
    typed = cast(tuple[float, ...], weights)
    try:
        total = math.fsum(typed)
    except OverflowError as error:
        raise TrafficlabError(
            f"invalid {name}: weights cannot be summed safely",
            corrective_action="provide finite nonnegative weights that sum to one",
        ) from error
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise TrafficlabError(
            f"invalid {name}: weights must sum to one",
            corrective_action="provide finite nonnegative weights that sum to one",
        )
    return typed


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
