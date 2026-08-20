"""Documented autocorrelation similarity for canonical traffic traces."""

import math
from collections.abc import Iterable
from typing import cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.errors import TrafficlabError
from trafficlab.similarity.common import FrozenJsonValue, JsonDiagnostics, SimilarityResult, validate_observation_window
from trafficlab.trace import Direction, TraceEvent, TrafficTrace

_ROUNDING_TOLERANCE = 1e-15
type _NumericSample = tuple[int | float, ...] | NDArray[np.float64] | NDArray[np.uint32]


class AutocorrelationSamplesInsufficientError(TrafficlabError):
    """Configured autocorrelation lags exceed at least one observed sample."""


def _clamp_documented_range(value: float, *, lower: float, upper: float, name: str) -> float:
    """Clamp negligible roundoff at one documented interval boundary."""
    if not math.isfinite(value):
        raise TrafficlabError(
            f"invalid {name}: computation produced a nonfinite value",
            corrective_action="provide finite samples and normalized weights",
        )
    if lower <= value <= upper:
        return value
    if lower - _ROUNDING_TOLERANCE <= value < lower:
        return lower
    if upper < value <= upper + _ROUNDING_TOLERANCE:
        return upper
    raise TrafficlabError(
        f"invalid {name}: computation produced a value outside [{lower}, {upper}]",
        corrective_action="provide finite samples and normalized weights",
    )


def _validated_numeric_values(values: Iterable[object]) -> tuple[int | float, ...]:
    """Materialize one finite numeric sample for direct autocorrelation."""
    try:
        sample = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            "invalid autocorrelation sample: values must be iterable",
            corrective_action="provide an iterable of finite numeric values",
        ) from error
    numeric_values: list[int | float] = []
    for value in sample:
        if type(value) is int:
            numeric_values.append(value)
        elif type(value) is float and math.isfinite(value):
            numeric_values.append(value)
        else:
            raise TrafficlabError(
                "invalid autocorrelation sample: values must be finite numbers",
                corrective_action="provide an iterable of finite numeric values",
            )
    return tuple(numeric_values)


def _validated_numeric_array(values: Iterable[object] | NDArray[np.generic]) -> NDArray[np.float64]:
    """Return one finite float64 array while retaining the generic scalar boundary."""
    raw_values: object = values
    array = cast(NDArray[np.generic], raw_values) if isinstance(raw_values, np.ndarray) else None
    if (
        array is not None
        and array.ndim == 1
        and (np.issubdtype(array.dtype, np.integer) or np.issubdtype(array.dtype, np.floating))
    ):
        try:
            numeric = np.asarray(array, dtype=np.float64)
        except (OverflowError, TypeError, ValueError) as error:
            raise TrafficlabError(
                "invalid autocorrelation sample: values cannot be evaluated safely",
                corrective_action="provide finite numeric values within the supported arithmetic range",
            ) from error
        if not np.all(np.isfinite(numeric)):
            raise TrafficlabError(
                "invalid autocorrelation sample: values must be finite numbers",
                corrective_action="provide an iterable of finite numeric values",
            )
        return numeric
    sample = _validated_numeric_values(cast(Iterable[object], values))
    try:
        return np.asarray(sample, dtype=np.float64)
    except (OverflowError, TypeError, ValueError) as error:
        raise TrafficlabError(
            "invalid autocorrelation sample: values cannot be evaluated safely",
            corrective_action="provide finite numeric values within the supported arithmetic range",
        ) from error


def _validated_lag(lag: object, *, sample_length: int) -> int:
    """Return a positive lag that has paired values in its sample."""
    if type(lag) is not int or lag <= 0 or lag >= sample_length:
        raise TrafficlabError(
            "invalid autocorrelation lag: it must be a positive integer smaller than the sample length",
            corrective_action="provide positive lags smaller than every autocorrelation sample",
        )
    return lag


def _sample_autocorrelations(
    values: Iterable[object] | NDArray[np.generic], lags: tuple[int, ...]
) -> tuple[float, ...]:
    """Evaluate selected lags after one validation, centering, and denominator pass."""
    sample = _validated_numeric_array(values)
    validated_lags = tuple(_validated_lag(lag, sample_length=len(sample)) for lag in lags)
    if np.all(sample == sample[0]):
        return tuple(0.0 for _lag in validated_lags)
    try:
        largest_magnitude = float(np.max(np.abs(sample)))
        _, scale_exponent = math.frexp(largest_magnitude)
        scaled = np.ldexp(sample, -scale_exponent)
        mean = math.fsum(float(value) for value in scaled) / len(scaled)
        centered = scaled - mean
        denominator = float(np.dot(centered, centered))
    except (OverflowError, ValueError) as error:
        raise TrafficlabError(
            "invalid autocorrelation sample: values cannot be evaluated safely",
            corrective_action="provide finite numeric values within the supported arithmetic range",
        ) from error
    if denominator == 0.0:
        return tuple(0.0 for _lag in validated_lags)
    return tuple(
        _clamp_documented_range(
            float(np.dot(centered[:-lag], centered[lag:])) / denominator,
            lower=-1.0,
            upper=1.0,
            name="autocorrelation",
        )
        for lag in validated_lags
    )


def sample_autocorrelation(values: Iterable[object], lag: object) -> float:
    """Return the documented whole-series-mean sample autocorrelation at one lag."""
    return _sample_autocorrelations(values, (cast(int, lag),))[0]


def _validated_lags(lags: Iterable[object]) -> tuple[int, ...]:
    """Materialize the nonempty unique positive configured lag vector."""
    try:
        validated_lags = tuple(lags)
    except TypeError as error:
        raise TrafficlabError(
            "invalid autocorrelation lags: lags must be iterable",
            corrective_action="provide unique positive integer lags",
        ) from error
    if not validated_lags or any(type(lag) is not int or lag <= 0 for lag in validated_lags):
        raise TrafficlabError(
            "invalid autocorrelation lags: lags must be unique positive integers",
            corrective_action="provide unique positive integer lags",
        )
    typed_lags = tuple(lag for lag in validated_lags if type(lag) is int)
    if len(typed_lags) != len(set(typed_lags)):
        raise TrafficlabError(
            "invalid autocorrelation lags: lags must be unique positive integers",
            corrective_action="provide unique positive integer lags",
        )
    return typed_lags


def _validated_weights(values: Iterable[object], *, name: str, expected_length: int | None = None) -> tuple[float, ...]:
    """Return one finite nonnegative weight vector normalized with precise summation."""
    try:
        weights = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {name}: weights must be iterable",
            corrective_action="provide finite nonnegative weights that sum to one",
        ) from error
    if expected_length is not None and len(weights) != expected_length:
        raise TrafficlabError(
            f"invalid {name}: weight count must match lag count",
            corrective_action="provide one finite normalized weight for every lag",
        )
    if not weights or any(type(weight) is not float or not math.isfinite(weight) or weight < 0.0 for weight in weights):
        raise TrafficlabError(
            f"invalid {name}: weights must be finite nonnegative floats",
            corrective_action="provide finite nonnegative weights that sum to one",
        )
    typed_weights = tuple(weight for weight in weights if type(weight) is float)
    try:
        weight_sum = math.fsum(typed_weights)
    except OverflowError as error:
        raise TrafficlabError(
            f"invalid {name}: weights cannot be summed safely",
            corrective_action="provide finite nonnegative weights that sum to one",
        ) from error
    if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise TrafficlabError(
            f"invalid {name}: weights must sum to one",
            corrective_action="provide finite nonnegative weights that sum to one",
        )
    return typed_weights


def weighted_acf_discrepancy(
    reference_acf: tuple[float, ...], generated_acf: tuple[float, ...], lag_weights: tuple[float, ...]
) -> float:
    """Return the documented normalized weighted difference between two ACF vectors."""
    try:
        discrepancy = math.fsum(
            weight * abs(reference_value - generated_value) / 2.0
            for reference_value, generated_value, weight in zip(reference_acf, generated_acf, lag_weights, strict=True)
        )
    except OverflowError as error:
        raise TrafficlabError(
            "invalid autocorrelation discrepancy: computation overflowed",
            corrective_action="provide finite ACF values and normalized weights",
        ) from error
    return _clamp_documented_range(discrepancy, lower=0.0, upper=1.0, name="autocorrelation discrepancy")


def _feature_diagnostics(
    reference_values: _NumericSample,
    generated_values: _NumericSample,
    lags: tuple[int, ...],
    lag_weights: tuple[float, ...],
) -> tuple[dict[str, FrozenJsonValue], float]:
    """Calculate complete diagnostics for one feature's two autocorrelation vectors."""
    reference_acf = _sample_autocorrelations(reference_values, lags)
    generated_acf = _sample_autocorrelations(generated_values, lags)
    differences = tuple(
        abs(reference_value - generated_value)
        for reference_value, generated_value in zip(reference_acf, generated_acf, strict=True)
    )
    discrepancy = weighted_acf_discrepancy(reference_acf, generated_acf, lag_weights)
    return (
        {
            "reference_sample_count": len(reference_values),
            "generated_sample_count": len(generated_values),
            "reference_acf": reference_acf,
            "generated_acf": generated_acf,
            "absolute_differences": differences,
            "discrepancy": discrepancy,
        },
        discrepancy,
    )


def _validated_trace(events: Iterable[TraceEvent] | TrafficTrace, *, trace_name: str) -> TrafficTrace:
    """Return one complete nondecreasing canonical trace for this metric."""
    corrective_action = f"provide finite nondecreasing canonical {trace_name} events"
    if type(events) is TrafficTrace:
        if len(events) < 2:
            raise TrafficlabError(
                f"invalid {trace_name} trace: at least two events are required",
                corrective_action=corrective_action,
            )
        return events
    try:
        trace = tuple(events)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {trace_name} trace: events must be an iterable of canonical events",
            corrective_action=corrective_action,
        ) from error
    if len(trace) < 2:
        raise TrafficlabError(
            f"invalid {trace_name} trace: at least two events are required",
            corrective_action=corrective_action,
        )
    previous_timestamp: float | None = None
    for event in trace:
        if type(event) is not TraceEvent:
            raise TrafficlabError(
                f"invalid {trace_name} trace: every event must be a TraceEvent",
                corrective_action=corrective_action,
            )
        try:
            timestamp = event.timestamp
            direction = event.direction
            frame_length = event.frame_length
        except AttributeError as error:
            raise TrafficlabError(
                f"invalid {trace_name} trace: event data is incomplete",
                corrective_action=corrective_action,
            ) from error
        if (
            type(timestamp) is not float
            or not math.isfinite(timestamp)
            or timestamp < 0.0
            or type(direction) is not Direction
            or type(frame_length) is not int
            or frame_length <= 0
        ):
            raise TrafficlabError(
                f"invalid {trace_name} trace: events must have finite timestamps, directions, and positive lengths",
                corrective_action=corrective_action,
            )
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise TrafficlabError(
                f"invalid {trace_name} trace: timestamps must be nondecreasing",
                corrective_action=corrective_action,
            )
        previous_timestamp = timestamp
    return TrafficTrace.from_events(trace)


def _trace_samples(
    events: Iterable[TraceEvent] | TrafficTrace, *, trace_name: str
) -> tuple[NDArray[np.float64], NDArray[np.uint32]]:
    """Validate one trace once and derive its IAT and frame-size feature samples."""
    trace = _validated_trace(events, trace_name=trace_name)
    return trace.iats(), trace.frame_lengths


def _validate_lags_fit_samples(
    lags: tuple[int, ...], samples: dict[str, NDArray[np.float64] | NDArray[np.uint32]]
) -> None:
    """Require every configured lag to fit reference and generated IAT and size samples."""
    for sample_name, values in samples.items():
        if any(lag >= len(values) for lag in lags):
            raise AutocorrelationSamplesInsufficientError(
                f"invalid autocorrelation lags: every lag must be smaller than the {sample_name} sample length",
                corrective_action="provide lags smaller than all reference and generated IAT and size samples",
            )


def autocorrelation_similarity(
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: object,
    lags: Iterable[object],
    lag_weights: Iterable[object],
    iat_weight: object,
    size_weight: object,
) -> SimilarityResult:
    """Compare IAT and frame-size serial dependence at configured positive lags."""
    window = validate_observation_window(W)
    validated_lags = _validated_lags(lags)
    validated_lag_weights = _validated_weights(
        lag_weights, name="autocorrelation lag weights", expected_length=len(validated_lags)
    )
    feature_weights = _validated_weights((iat_weight, size_weight), name="autocorrelation feature weights")
    reference_iats, reference_sizes = _trace_samples(reference, trace_name="reference")
    generated_iats, generated_sizes = _trace_samples(generated, trace_name="generated")
    samples = {
        "reference IAT": reference_iats,
        "generated IAT": generated_iats,
        "reference size": reference_sizes,
        "generated size": generated_sizes,
    }
    _validate_lags_fit_samples(validated_lags, samples)
    iat_diagnostics, iat_discrepancy = _feature_diagnostics(
        reference_iats, generated_iats, validated_lags, validated_lag_weights
    )
    size_diagnostics, size_discrepancy = _feature_diagnostics(
        reference_sizes, generated_sizes, validated_lags, validated_lag_weights
    )
    discrepancy = _clamp_documented_range(
        math.fsum((feature_weights[0] * iat_discrepancy, feature_weights[1] * size_discrepancy)),
        lower=0.0,
        upper=1.0,
        name="autocorrelation discrepancy",
    )
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "lags": validated_lags,
        "lag_weights": validated_lag_weights,
        "feature_weights": {"iat": feature_weights[0], "size": feature_weights[1]},
        "iat": iat_diagnostics,
        "size": size_diagnostics,
        "discrepancy": discrepancy,
    }
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)
