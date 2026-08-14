"""Authoritative offline trace comparison and reliable similarity artifact publication."""

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Self, cast

from trafficlab.artifacts import append_run_log
from trafficlab.config import SimilarityConfig
from trafficlab.config_io import load_experiment
from trafficlab.errors import TrafficlabError
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.similarity.autocorrelation import autocorrelation_similarity
from trafficlab.similarity.common import FrozenJsonValue, JsonValue, SimilarityResult
from trafficlab.similarity.ks import frame_size_ks, iat_ks
from trafficlab.similarity.multiscale import multiscale_rate_similarity
from trafficlab.trace import TraceEvent, align_generated, normalize_reference, parse_capture_metadata

_METHOD_NAMES = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
_INPUT_NAMES = ("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_WEIGHT_TOLERANCE = 1e-12


def _strict_float(value: object, *, name: str, positive: bool = False) -> float:
    if type(value) is not float or not math.isfinite(value):
        qualifier = "finite positive float" if positive else "finite float"
        raise ValueError(f"{name} must be a {qualifier}")
    if positive and value <= 0.0:
        raise ValueError(f"{name} must be a finite positive float")
    return value


def _bounded_float(value: object, *, name: str) -> float:
    result = _strict_float(value, name=name)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite float in [0, 1]")
    return result


def _bounded_weighted_score(value: float) -> float:
    """Clamp only the weight-sum tolerance already accepted by configuration."""
    if -_WEIGHT_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _WEIGHT_TOLERANCE:
        return 1.0
    return _bounded_float(value, name="aggregate_score")


def _exact_keys(value: object, expected: tuple[str, ...], *, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{name} must be a JSON object")
    document = cast(dict[object, object], value)
    if any(type(key) is not str for key in document):
        raise ValueError(f"{name} keys must be strings")
    string_document = cast(dict[str, object], document)
    if set(string_document) != set(expected):
        raise ValueError(f"{name} must contain exactly: {', '.join(expected)}")
    return string_document


def _strict_int(value: object, *, name: str, positive: bool = False) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if positive and value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if not positive and value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _ranged_float(value: object, *, name: str, lower: float, upper: float) -> float:
    result = _strict_float(value, name=name)
    if not lower <= result <= upper:
        raise ValueError(f"{name} must be a finite float in [{lower}, {upper}]")
    return result


def _float_list(value: object, *, name: str, lower: float, upper: float) -> tuple[float, ...]:
    if type(value) is not list:
        raise ValueError(f"{name} must be a JSON list")
    items = cast(list[object], value)
    return tuple(_ranged_float(item, name=f"{name} item", lower=lower, upper=upper) for item in items)


def _normalized_weights(value: object, *, name: str, expected_length: int) -> tuple[float, ...]:
    weights = _float_list(value, name=name, lower=0.0, upper=1.0)
    if len(weights) != expected_length:
        raise ValueError(f"{name} length must be {expected_length}")
    if not math.isclose(math.fsum(weights), 1.0, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(f"{name} must sum to one")
    return weights


def _require_close(actual: float, expected: float, *, name: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(f"{name} is inconsistent with its documented components")


def _validate_score_discrepancy(score: float, discrepancy: float, *, method_name: str) -> None:
    _require_close(score, 1.0 - discrepancy, name=f"{method_name} score")


def _validate_frame_size_diagnostics(value: object, *, score: float) -> dict[str, object]:
    name = "frame_size_ks diagnostics"
    document = _exact_keys(
        value,
        (
            "observation_window_seconds",
            "distance",
            "reference_count",
            "generated_count",
            "reference_minimum_length",
            "reference_maximum_length",
            "generated_minimum_length",
            "generated_maximum_length",
        ),
        name=name,
    )
    _strict_float(document["observation_window_seconds"], name=f"{name}.observation_window_seconds", positive=True)
    distance = _bounded_float(document["distance"], name=f"{name}.distance")
    _strict_int(document["reference_count"], name=f"{name}.reference_count", positive=True)
    _strict_int(document["generated_count"], name=f"{name}.generated_count", positive=True)
    reference_minimum = _strict_int(
        document["reference_minimum_length"], name=f"{name}.reference_minimum_length", positive=True
    )
    reference_maximum = _strict_int(
        document["reference_maximum_length"], name=f"{name}.reference_maximum_length", positive=True
    )
    generated_minimum = _strict_int(
        document["generated_minimum_length"], name=f"{name}.generated_minimum_length", positive=True
    )
    generated_maximum = _strict_int(
        document["generated_maximum_length"], name=f"{name}.generated_maximum_length", positive=True
    )
    if reference_minimum > reference_maximum or generated_minimum > generated_maximum:
        raise ValueError(f"{name} minimum lengths must not exceed maximum lengths")
    _validate_score_discrepancy(score, distance, method_name="frame_size_ks")
    return document


def _validate_iat_diagnostics(value: object, *, score: float) -> dict[str, object]:
    name = "iat_ks diagnostics"
    document = _exact_keys(
        value,
        (
            "observation_window_seconds",
            "distance",
            "diagnostic_quantile",
            "reference_iat_count",
            "generated_iat_count",
            "reference_zero_iat_count",
            "generated_zero_iat_count",
            "reference_median_iat_seconds",
            "generated_median_iat_seconds",
            "reference_quantile_iat_seconds",
            "generated_quantile_iat_seconds",
        ),
        name=name,
    )
    _strict_float(document["observation_window_seconds"], name=f"{name}.observation_window_seconds", positive=True)
    distance = _bounded_float(document["distance"], name=f"{name}.distance")
    quantile = _strict_float(document["diagnostic_quantile"], name=f"{name}.diagnostic_quantile")
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"{name}.diagnostic_quantile must be strictly between zero and one")
    reference_count = _strict_int(document["reference_iat_count"], name=f"{name}.reference_iat_count", positive=True)
    generated_count = _strict_int(document["generated_iat_count"], name=f"{name}.generated_iat_count", positive=True)
    reference_zero_count = _strict_int(document["reference_zero_iat_count"], name=f"{name}.reference_zero_iat_count")
    generated_zero_count = _strict_int(document["generated_zero_iat_count"], name=f"{name}.generated_zero_iat_count")
    if reference_zero_count > reference_count or generated_zero_count > generated_count:
        raise ValueError(f"{name} zero-IAT counts must not exceed their sample counts")
    for field in (
        "reference_median_iat_seconds",
        "generated_median_iat_seconds",
        "reference_quantile_iat_seconds",
        "generated_quantile_iat_seconds",
    ):
        _ranged_float(document[field], name=f"{name}.{field}", lower=0.0, upper=math.inf)
    _validate_score_discrepancy(score, distance, method_name="iat_ks")
    return document


def _validate_acf_feature(
    value: object,
    *,
    name: str,
    lags: tuple[int, ...],
    lag_weights: tuple[float, ...],
) -> float:
    document = _exact_keys(
        value,
        (
            "reference_sample_count",
            "generated_sample_count",
            "reference_acf",
            "generated_acf",
            "absolute_differences",
            "discrepancy",
        ),
        name=name,
    )
    reference_count = _strict_int(
        document["reference_sample_count"], name=f"{name}.reference_sample_count", positive=True
    )
    generated_count = _strict_int(
        document["generated_sample_count"], name=f"{name}.generated_sample_count", positive=True
    )
    if any(lag >= reference_count or lag >= generated_count for lag in lags):
        raise ValueError(f"{name} sample counts must exceed every configured lag")
    reference_acf = _float_list(document["reference_acf"], name=f"{name}.reference_acf", lower=-1.0, upper=1.0)
    generated_acf = _float_list(document["generated_acf"], name=f"{name}.generated_acf", lower=-1.0, upper=1.0)
    differences = _float_list(
        document["absolute_differences"], name=f"{name}.absolute_differences", lower=0.0, upper=2.0
    )
    if not len(reference_acf) == len(generated_acf) == len(differences) == len(lags):
        raise ValueError(f"{name} ACF vectors must match the configured lag count")
    for index, (reference_value, generated_value, difference) in enumerate(
        zip(reference_acf, generated_acf, differences, strict=True)
    ):
        _require_close(difference, abs(reference_value - generated_value), name=f"{name} difference {index}")
    discrepancy = _bounded_float(document["discrepancy"], name=f"{name}.discrepancy")
    expected = math.fsum(weight * difference / 2.0 for weight, difference in zip(lag_weights, differences, strict=True))
    _require_close(discrepancy, expected, name=f"{name}.discrepancy")
    return discrepancy


def _validate_autocorrelation_diagnostics(value: object, *, score: float) -> dict[str, object]:
    name = "autocorrelation diagnostics"
    document = _exact_keys(
        value,
        (
            "observation_window_seconds",
            "lags",
            "lag_weights",
            "feature_weights",
            "iat",
            "size",
            "discrepancy",
        ),
        name=name,
    )
    _strict_float(document["observation_window_seconds"], name=f"{name}.observation_window_seconds", positive=True)
    if type(document["lags"]) is not list:
        raise ValueError(f"{name}.lags must be a JSON list")
    lag_items = cast(list[object], document["lags"])
    lags = tuple(_strict_int(lag, name=f"{name}.lags item", positive=True) for lag in lag_items)
    if not lags or len(lags) != len(set(lags)):
        raise ValueError(f"{name}.lags must contain unique positive integers")
    lag_weights = _normalized_weights(document["lag_weights"], name=f"{name}.lag_weights", expected_length=len(lags))
    feature_weights_document = _exact_keys(document["feature_weights"], ("iat", "size"), name=f"{name}.feature_weights")
    feature_weights = tuple(
        _bounded_float(feature_weights_document[feature], name=f"{name}.feature_weights.{feature}")
        for feature in ("iat", "size")
    )
    if not math.isclose(math.fsum(feature_weights), 1.0, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(f"{name}.feature_weights must sum to one")
    iat_discrepancy = _validate_acf_feature(document["iat"], name=f"{name}.iat", lags=lags, lag_weights=lag_weights)
    size_discrepancy = _validate_acf_feature(document["size"], name=f"{name}.size", lags=lags, lag_weights=lag_weights)
    discrepancy = _bounded_float(document["discrepancy"], name=f"{name}.discrepancy")
    expected = math.fsum((feature_weights[0] * iat_discrepancy, feature_weights[1] * size_discrepancy))
    _require_close(discrepancy, expected, name=f"{name}.discrepancy")
    _validate_score_discrepancy(score, discrepancy, method_name="autocorrelation")
    return document


def _validate_direction_totals(value: object, *, name: str) -> tuple[int, int, int, int]:
    document = _exact_keys(value, ("packet", "byte"), name=name)
    totals: list[int] = []
    for feature in ("packet", "byte"):
        feature_document = _exact_keys(document[feature], ("outbound", "inbound"), name=f"{name}.{feature}")
        totals.extend(
            _strict_int(feature_document[direction], name=f"{name}.{feature}.{direction}")
            for direction in ("outbound", "inbound")
        )
    return cast(tuple[int, int, int, int], tuple(totals))


def _snap_near_integer(quotient: float) -> float:
    nearest = round(quotient)
    if abs(quotient - nearest) <= 4.0 * math.ulp(quotient):
        return float(nearest)
    return quotient


def _validate_multiscale_diagnostics(value: object, *, score: float) -> dict[str, object]:
    name = "multiscale_rate diagnostics"
    document = _exact_keys(
        value,
        (
            "observation_window_seconds",
            "widths",
            "scale_weights",
            "feature_weights",
            "direction_bin_cell_counts",
            "total_direction_bin_cells",
            "scales",
            "scale_discrepancies",
            "feature_discrepancies",
            "discrepancy",
        ),
        name=name,
    )
    window = _strict_float(
        document["observation_window_seconds"], name=f"{name}.observation_window_seconds", positive=True
    )
    widths = _float_list(document["widths"], name=f"{name}.widths", lower=0.0, upper=window)
    if (
        not widths
        or any(width <= 0.0 for width in widths)
        or any(current <= previous for previous, current in zip(widths, widths[1:], strict=False))
    ):
        raise ValueError(f"{name}.widths must be nonempty, positive, and strictly increasing")
    scale_weights = _normalized_weights(
        document["scale_weights"], name=f"{name}.scale_weights", expected_length=len(widths)
    )
    feature_weights_document = _exact_keys(
        document["feature_weights"], ("packet", "byte"), name=f"{name}.feature_weights"
    )
    feature_weights = tuple(
        _bounded_float(feature_weights_document[feature], name=f"{name}.feature_weights.{feature}")
        for feature in ("packet", "byte")
    )
    if not math.isclose(math.fsum(feature_weights), 1.0, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(f"{name}.feature_weights must sum to one")
    if type(document["direction_bin_cell_counts"]) is not list:
        raise ValueError(f"{name}.direction_bin_cell_counts must be a JSON list")
    direction_count_items = cast(list[object], document["direction_bin_cell_counts"])
    direction_counts = tuple(
        _strict_int(count, name=f"{name}.direction_bin_cell_counts item", positive=True)
        for count in direction_count_items
    )
    expected_counts: list[int] = []
    for width in widths:
        quotient = window / width
        if not math.isfinite(quotient):
            raise ValueError(f"{name}: W divided by a width must be finite")
        expected_counts.append(2 * math.ceil(_snap_near_integer(quotient)))
    if direction_counts != tuple(expected_counts):
        raise ValueError(f"{name}.direction_bin_cell_counts are inconsistent with widths and W")
    total_direction_cells = _strict_int(
        document["total_direction_bin_cells"], name=f"{name}.total_direction_bin_cells", positive=True
    )
    if total_direction_cells != sum(direction_counts):
        raise ValueError(f"{name}.total_direction_bin_cells must equal the direction cell count sum")
    if type(document["scales"]) is not list:
        raise ValueError(f"{name}.scales must be a JSON list matching the width count")
    scales = cast(list[object], document["scales"])
    if len(scales) != len(widths):
        raise ValueError(f"{name}.scales must be a JSON list matching the width count")

    packet_discrepancies: list[float] = []
    byte_discrepancies: list[float] = []
    scale_discrepancies: list[float] = []
    reference_totals: tuple[int, int, int, int] | None = None
    generated_totals: tuple[int, int, int, int] | None = None
    for index, scale_value in enumerate(scales):
        scale_name = f"{name}.scales[{index}]"
        scale = _exact_keys(
            scale_value,
            (
                "width_seconds",
                "bins_per_direction",
                "direction_bin_cell_count",
                "reference_totals",
                "generated_totals",
                "feature_discrepancies",
                "discrepancy",
            ),
            name=scale_name,
        )
        width = _strict_float(scale["width_seconds"], name=f"{scale_name}.width_seconds", positive=True)
        if width != widths[index]:
            raise ValueError(f"{scale_name}.width_seconds must equal its configured width")
        bins = _strict_int(scale["bins_per_direction"], name=f"{scale_name}.bins_per_direction", positive=True)
        if bins * 2 != direction_counts[index]:
            raise ValueError(f"{scale_name}.bins_per_direction is inconsistent with its direction cell count")
        direction_count = _strict_int(
            scale["direction_bin_cell_count"], name=f"{scale_name}.direction_bin_cell_count", positive=True
        )
        if direction_count != direction_counts[index]:
            raise ValueError(f"{scale_name}.direction_bin_cell_count must equal its configured count")
        current_reference_totals = _validate_direction_totals(
            scale["reference_totals"], name=f"{scale_name}.reference_totals"
        )
        current_generated_totals = _validate_direction_totals(
            scale["generated_totals"], name=f"{scale_name}.generated_totals"
        )
        if reference_totals is None:
            reference_totals = current_reference_totals
            generated_totals = current_generated_totals
        elif current_reference_totals != reference_totals or current_generated_totals != generated_totals:
            raise ValueError(f"{scale_name} packet and byte totals must be consistent across scales")
        feature_document = _exact_keys(
            scale["feature_discrepancies"], ("packet", "byte"), name=f"{scale_name}.feature_discrepancies"
        )
        packet_discrepancy = _bounded_float(
            feature_document["packet"], name=f"{scale_name}.feature_discrepancies.packet"
        )
        byte_discrepancy = _bounded_float(feature_document["byte"], name=f"{scale_name}.feature_discrepancies.byte")
        scale_discrepancy = _bounded_float(scale["discrepancy"], name=f"{scale_name}.discrepancy")
        _require_close(
            scale_discrepancy,
            math.fsum((feature_weights[0] * packet_discrepancy, feature_weights[1] * byte_discrepancy)),
            name=f"{scale_name}.discrepancy",
        )
        packet_discrepancies.append(packet_discrepancy)
        byte_discrepancies.append(byte_discrepancy)
        scale_discrepancies.append(scale_discrepancy)

    retained_scale_discrepancies = _float_list(
        document["scale_discrepancies"], name=f"{name}.scale_discrepancies", lower=0.0, upper=1.0
    )
    if retained_scale_discrepancies != tuple(scale_discrepancies):
        raise ValueError(f"{name}.scale_discrepancies must match every retained scale")
    feature_document = _exact_keys(
        document["feature_discrepancies"], ("packet", "byte"), name=f"{name}.feature_discrepancies"
    )
    packet_total = _bounded_float(feature_document["packet"], name=f"{name}.feature_discrepancies.packet")
    byte_total = _bounded_float(feature_document["byte"], name=f"{name}.feature_discrepancies.byte")
    _require_close(
        packet_total,
        math.fsum(weight * item for weight, item in zip(scale_weights, packet_discrepancies, strict=True)),
        name=f"{name}.feature_discrepancies.packet",
    )
    _require_close(
        byte_total,
        math.fsum(weight * item for weight, item in zip(scale_weights, byte_discrepancies, strict=True)),
        name=f"{name}.feature_discrepancies.byte",
    )
    discrepancy = _bounded_float(document["discrepancy"], name=f"{name}.discrepancy")
    _require_close(
        discrepancy,
        math.fsum((feature_weights[0] * packet_total, feature_weights[1] * byte_total)),
        name=f"{name}.discrepancy",
    )
    _require_close(
        discrepancy,
        math.fsum(weight * item for weight, item in zip(scale_weights, scale_discrepancies, strict=True)),
        name=f"{name}.scale-weighted discrepancy",
    )
    _validate_score_discrepancy(score, discrepancy, method_name="multiscale_rate")
    return document


def _validate_method_diagnostics(method_name: str, value: object, *, score: float) -> dict[str, object]:
    if method_name == "frame_size_ks":
        return _validate_frame_size_diagnostics(value, score=score)
    if method_name == "iat_ks":
        return _validate_iat_diagnostics(value, score=score)
    if method_name == "autocorrelation":
        return _validate_autocorrelation_diagnostics(value, score=score)
    if method_name == "multiscale_rate":
        return _validate_multiscale_diagnostics(value, score=score)
    raise ValueError(f"unsupported comparison method {method_name!r}")


@dataclass(frozen=True, slots=True, init=False)
class MethodComparison:
    """One configured method's immutable score, weight, and retained diagnostics."""

    score: float
    weight: float
    diagnostics: Mapping[str, FrozenJsonValue]

    def __init__(self, score: float, weight: float, diagnostics: Mapping[str, object]) -> None:
        bounded_score = _bounded_float(score, name="method score")
        bounded_weight = _bounded_float(weight, name="method weight")
        frozen = SimilarityResult(bounded_score, diagnostics).diagnostics
        object.__setattr__(self, "score", bounded_score)
        object.__setattr__(self, "weight", bounded_weight)
        object.__setattr__(self, "diagnostics", frozen)

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a fresh ordinary JSON representation."""
        similarity = SimilarityResult(self.score, self.diagnostics).as_dict()
        return {
            "diagnostics": similarity["diagnostics"],
            "score": self.score,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, method_name: str, value: object) -> Self:
        """Strictly validate one method object from parsed JSON."""
        document = _exact_keys(value, ("diagnostics", "score", "weight"), name="method result")
        score = _bounded_float(document["score"], name="method score")
        diagnostics = _validate_method_diagnostics(method_name, document["diagnostics"], score=score)
        return cls(
            score,
            _bounded_float(document["weight"], name="method weight"),
            diagnostics,
        )


@dataclass(frozen=True, slots=True, init=False)
class ComparisonResult:
    """One deeply immutable comparison result, optionally carrying artifact identities."""

    aggregate_score: float
    observation_window_seconds: float
    methods: Mapping[str, MethodComparison]
    input_sha256: Mapping[str, str] | None

    def __init__(
        self,
        aggregate_score: float,
        observation_window_seconds: float,
        methods: Mapping[str, MethodComparison],
        input_sha256: Mapping[str, str] | None,
    ) -> None:
        aggregate = _bounded_float(aggregate_score, name="aggregate_score")
        window = _strict_float(
            observation_window_seconds,
            name="observation_window_seconds",
            positive=True,
        )
        if set(methods) != set(_METHOD_NAMES):
            raise ValueError(f"methods must contain exactly: {', '.join(_METHOD_NAMES)}")
        ordered_methods: dict[str, MethodComparison] = {}
        for name in _METHOD_NAMES:
            method = methods[name]
            if type(method) is not MethodComparison:
                raise ValueError("every method must be a MethodComparison")
            diagnostic_window = method.diagnostics.get("observation_window_seconds")
            if type(diagnostic_window) is not float or not math.isfinite(diagnostic_window) or diagnostic_window <= 0.0:
                raise ValueError("every method diagnostic observation window must be a finite positive float")
            if diagnostic_window != window:
                raise ValueError("every method diagnostic must contain the shared observation window")
            ordered_methods[name] = method
        weight_sum = math.fsum(method.weight for method in ordered_methods.values())
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
            raise ValueError("method weights must sum to one")
        weighted_score = math.fsum(method.weight * method.score for method in ordered_methods.values())
        if not math.isclose(weighted_score, aggregate, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
            raise ValueError("aggregate_score must equal the exact configured weighted sum")

        frozen_inputs: Mapping[str, str] | None = None
        if input_sha256 is not None:
            if set(input_sha256) != set(_INPUT_NAMES):
                raise ValueError(f"input_sha256 must contain exactly: {', '.join(_INPUT_NAMES)}")
            ordered_inputs: dict[str, str] = {}
            for name in _INPUT_NAMES:
                identity = input_sha256[name]
                if type(identity) is not str or _SHA256_PATTERN.fullmatch(identity) is None:
                    raise ValueError(f"input_sha256.{name} must be a lowercase SHA-256 hexadecimal digest")
                ordered_inputs[name] = identity
            frozen_inputs = MappingProxyType(ordered_inputs)

        object.__setattr__(self, "aggregate_score", aggregate)
        object.__setattr__(self, "observation_window_seconds", window)
        object.__setattr__(self, "methods", MappingProxyType(ordered_methods))
        object.__setattr__(self, "input_sha256", frozen_inputs)

    def with_input_sha256(self, identities: Mapping[str, str]) -> Self:
        """Return the same scientific result with exact file and settings identities."""
        return type(self)(
            self.aggregate_score,
            self.observation_window_seconds,
            self.methods,
            identities,
        )

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the exact publishable JSON shape as fresh mutable values."""
        if self.input_sha256 is None:
            raise ValueError("input SHA-256 identities are required for a similarity artifact")
        return {
            "aggregate_score": self.aggregate_score,
            "input_sha256": dict(self.input_sha256),
            "methods": {name: method.as_dict() for name, method in self.methods.items()},
            "observation_window_seconds": self.observation_window_seconds,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Strictly validate the documented similarity artifact object."""
        document = _exact_keys(
            value,
            ("aggregate_score", "input_sha256", "methods", "observation_window_seconds"),
            name="comparison result",
        )
        methods_document = _exact_keys(document["methods"], _METHOD_NAMES, name="methods")
        methods = {name: MethodComparison.from_dict(name, methods_document[name]) for name in _METHOD_NAMES}
        inputs_document = _exact_keys(document["input_sha256"], _INPUT_NAMES, name="input_sha256")
        if any(type(identity) is not str for identity in inputs_document.values()):
            raise ValueError("input SHA-256 identities must be strings")
        return cls(
            _bounded_float(document["aggregate_score"], name="aggregate_score"),
            _strict_float(
                document["observation_window_seconds"],
                name="observation_window_seconds",
                positive=True,
            ),
            methods,
            cast(dict[str, str], inputs_document),
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def parse_comparison_result(content: bytes) -> ComparisonResult:
    """Parse strict UTF-8 JSON bytes into the immutable result type."""
    try:
        text = content.decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid similarity JSON: {error}") from error
    return ComparisonResult.from_dict(document)


def _canonical_comparison_bytes(result: ComparisonResult) -> bytes:
    return (json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def render_comparison_result(result: ComparisonResult) -> bytes:
    """Render one complete result as deterministic sorted compact JSON."""
    return _canonical_comparison_bytes(result)


def load_comparison_result(path: Path) -> ComparisonResult:
    """Load and strictly validate one similarity artifact."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read similarity artifact {path}: {error}",
            corrective_action="verify similarity.json exists and is readable",
        ) from error
    try:
        return parse_comparison_result(content)
    except ValueError as error:
        raise TrafficlabError(
            f"invalid similarity artifact {path}: {error}",
            corrective_action="rerun comparison to publish a valid similarity.json",
        ) from error


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 identity of exact bytes."""
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 identity of one exact file without loading it all at once."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise TrafficlabError(
            f"could not hash comparison input {path}: {error}",
            corrective_action=f"verify {path.name} exists and is readable",
        ) from error
    return digest.hexdigest()


def _read_comparison_input(path: Path, *, kind: str, corrective_action: str) -> bytes:
    """Read one comparison input exactly once with its artifact-specific error."""
    try:
        return path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read {kind} {path}: {error}",
            corrective_action=corrective_action,
        ) from error


def similarity_settings_sha256(settings: SimilarityConfig) -> str:
    """Hash only the effective similarity settings as sorted compact JSON."""
    content = json.dumps(
        settings.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(content)


def compare_traces(
    reference: Iterable[TraceEvent],
    generated: Iterable[TraceEvent],
    W: float,
    settings: SimilarityConfig,
) -> ComparisonResult:
    """Evaluate all four configured metrics over exactly one observation window."""
    reference_trace = tuple(reference)
    generated_trace = tuple(generated)
    component_results = {
        "frame_size_ks": frame_size_ks(reference_trace, generated_trace, W),
        "iat_ks": iat_ks(reference_trace, generated_trace, W, settings.iat_diagnostic_quantile),
        "autocorrelation": autocorrelation_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.acf_lags,
            settings.acf_lag_weights,
            settings.acf_iat_weight,
            settings.acf_size_weight,
        ),
        "multiscale_rate": multiscale_rate_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.multiscale_widths_seconds,
            settings.multiscale_scale_weights,
            settings.multiscale_packet_weight,
            settings.multiscale_byte_weight,
            settings.max_direction_bin_cells,
        ),
    }
    configured_weights = settings.method_weights.model_dump()
    try:
        methods = {
            name: MethodComparison(
                component_results[name].score, configured_weights[name], component_results[name].diagnostics
            )
            for name in _METHOD_NAMES
        }
        aggregate = _bounded_weighted_score(math.fsum(method.weight * method.score for method in methods.values()))
        return ComparisonResult(aggregate, W, methods, None)
    except ValueError as error:
        raise TrafficlabError(
            f"invalid comparison result: {error}",
            corrective_action="report the comparison result assembly defect",
        ) from error


class _PublicationError(TrafficlabError):
    """Internal marker used only to distinguish publication logging detail."""


type _EntryIdentity = tuple[int, int, int, int]


def _entry_identity(destination: Path) -> _EntryIdentity | None:
    try:
        status = destination.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _publication_error(error: Exception, destination: Path, cleanup_error: BaseException | None) -> _PublicationError:
    if isinstance(error, FileExistsError) and str(error).startswith("similarity artifact changed"):
        detail = f"{error}: {destination}"
        action = "preserve the replacement and retry comparison in a stable run directory"
    elif isinstance(error, FileExistsError):
        detail = f"similarity artifact already exists: {destination}"
        action = "preserve the existing result or start a new run directory"
    else:
        detail = f"could not publish similarity artifact {destination}: {error}"
        action = "verify the run directory is writable and has available space"
    if cleanup_error is not None:
        detail = f"{detail}; cleanup incomplete: could not remove owned temporary file: {cleanup_error}"
    return _PublicationError(detail, corrective_action=action)


def _existing_result_is_reusable(destination: Path, expected_content: bytes, *, missing_ok: bool) -> bool:
    """Read and strictly validate one existing publication candidate exactly once."""
    identity = _entry_identity(destination)
    try:
        existing_content = destination.read_bytes()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    try:
        existing = parse_comparison_result(existing_content)
        canonical_content = _canonical_comparison_bytes(existing)
    except ValueError as error:
        raise FileExistsError(f"existing similarity artifact is not reusable: {error}") from error
    if existing_content != canonical_content or canonical_content != expected_content:
        raise FileExistsError("existing similarity artifact differs from the expected canonical result")
    if _entry_identity(destination) != identity:
        raise FileExistsError("similarity artifact changed during exact reuse validation")
    return True


def _publish_comparison_result(destination: Path, result: ComparisonResult) -> bool:
    """Fsync and exclusively publish, or strictly reuse, one canonical result."""
    temporary_path: Path | None = None
    created_by_call = False
    expected_error: OSError | ValueError | TrafficlabError | None = None
    unexpected_error: BaseException | None = None
    try:
        expected_content = _canonical_comparison_bytes(result)
        content = render_comparison_result(result)
        if content != expected_content:
            raise ValueError("rendered similarity artifact does not match the canonical evaluated result")
        if _existing_result_is_reusable(destination, expected_content, missing_ok=True):
            created_by_call = False
        else:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            persisted = load_comparison_result(temporary_path)
            persisted_bytes = temporary_path.read_bytes()
            persisted_content = _canonical_comparison_bytes(persisted)
            if persisted_bytes != persisted_content or persisted_content != expected_content:
                raise ValueError("temporary similarity artifact did not round-trip to the evaluated result")
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                _existing_result_is_reusable(destination, expected_content, missing_ok=False)
                created_by_call = False
            else:
                created_by_call = True
    except (OSError, ValueError, TrafficlabError) as error:
        expected_error = error
    except BaseException as error:
        unexpected_error = error

    cleanup_error: BaseException | None = None
    if temporary_path is not None:
        try:
            os.unlink(temporary_path)
        except BaseException as error:
            cleanup_error = error

    if unexpected_error is not None:
        if cleanup_error is not None:
            unexpected_error.add_note(f"owned temporary file cleanup also failed: {cleanup_error}")
        raise unexpected_error
    if expected_error is not None:
        raise _publication_error(expected_error, destination, cleanup_error) from expected_error
    if cleanup_error is not None:
        if isinstance(cleanup_error, OSError):
            publication_state = "published" if created_by_call else "not published"
            detail = (
                f"similarity artifact was {publication_state} at {destination}, "
                f"but owned temporary file cleanup failed: {cleanup_error}"
            )
            raise _PublicationError(
                detail,
                corrective_action=(
                    "preserve the published result and remove the reported temporary file if it is still owned"
                ),
            ) from cleanup_error
        raise cleanup_error
    return created_by_call


def _append_failure(run_directory: Path, primary: TrafficlabError, *, failure_kind: str) -> None:
    try:
        append_run_log(
            run_directory,
            {
                "detail": str(primary),
                "event": "comparison_failed",
                "failure_kind": failure_kind,
                "stage": "compare",
            },
        )
    except TrafficlabError as logging_error:
        raise TrafficlabError(
            f"{primary}; additionally could not append comparison failure to run.log: {logging_error}",
            corrective_action=primary.corrective_action,
            exit_code=primary.exit_code,
        ) from primary


def compare_experiment(experiment_path: Path) -> ComparisonResult:
    """Compare one existing run using its matching authoritative configuration snapshot."""
    caller_config = load_experiment(experiment_path)
    run_directory = caller_config.run.directory
    output_path = run_directory / "similarity.json"
    try:
        snapshot_config = load_experiment(run_directory / "experiment.toml")
        if caller_config != snapshot_config:
            raise TrafficlabError(
                f"caller configuration {experiment_path} does not match the authoritative run snapshot",
                corrective_action="use the exact experiment configuration that created this run",
            )
        metadata_path = run_directory / "capture.json"
        reference_path = run_directory / "reference.pcapng"
        generated_path = run_directory / "generated.pcapng"
        metadata_content = _read_comparison_input(
            metadata_path,
            kind="capture metadata",
            corrective_action="verify capture.json exists and is readable",
        )
        metadata = parse_capture_metadata(metadata_content, source=metadata_path)
        reference_content = _read_comparison_input(
            reference_path,
            kind="PCAPNG",
            corrective_action="verify the PCAPNG exists and is readable",
        )
        reference_events = parse_pcapng_bytes(reference_content, metadata, source=reference_path)
        generated_content = _read_comparison_input(
            generated_path,
            kind="PCAPNG",
            corrective_action="verify the PCAPNG exists and is readable",
        )
        generated_events = parse_pcapng_bytes(generated_content, metadata, source=generated_path)
        reference, window = normalize_reference(reference_events)
        generated = align_generated(generated_events, window)
        result = compare_traces(reference, generated, window, snapshot_config.similarity).with_input_sha256(
            {
                "capture_json": sha256_bytes(metadata_content),
                "generated_pcapng": sha256_bytes(generated_content),
                "reference_pcapng": sha256_bytes(reference_content),
                "similarity_settings": similarity_settings_sha256(snapshot_config.similarity),
            }
        )
        created_by_call = _publish_comparison_result(output_path, result)
    except TrafficlabError as error:
        failure_kind = "publication" if isinstance(error, _PublicationError) else "evaluation_or_input"
        _append_failure(run_directory, error, failure_kind=failure_kind)
        raise

    try:
        append_run_log(
            run_directory,
            {
                "aggregate_score": result.aggregate_score,
                "event": "comparison_succeeded",
                "observation_window_seconds": result.observation_window_seconds,
                "path": str(output_path),
                "reused": not created_by_call,
                "stage": "compare",
            },
        )
    except TrafficlabError as logging_error:
        raise TrafficlabError(
            f"comparison result was published at {output_path}, but success logging failed: {logging_error}",
            corrective_action=logging_error.corrective_action,
        ) from logging_error
    return result
