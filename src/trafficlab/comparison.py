"""Authoritative offline trace comparison and reliable similarity artifact publication."""

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    StrictInt,
    StrictStr,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from trafficlab.artifacts import append_run_log, fsync_published_artifact
from trafficlab.compatibility import ContentIdentity, identify_bytes, identify_file, require_compatible
from trafficlab.config import SimilarityConfig
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.errors import (
    FailureOutcome,
    TrafficlabError,
    append_failure_outcome,
    attach_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.generation import reproduce_generated_pcapng
from trafficlab.models.registry import load_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.scientific_schema import ScientificArtifactSchemaError
from trafficlab.similarity.autocorrelation import (
    AutocorrelationSamplesInsufficientError,
    autocorrelation_similarity,
)
from trafficlab.similarity.common import FrozenJsonValue, JsonValue, SimilarityResult
from trafficlab.similarity.ks import frame_size_ks, iat_ks
from trafficlab.similarity.multiscale import multiscale_rate_similarity
from trafficlab.trace import TraceEvent, TrafficTrace, align_generated, normalize_reference, parse_capture_metadata

_METHOD_NAMES = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
_INPUT_NAMES = ("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings")
_WEIGHT_TOLERANCE = 1e-12


def _exact_float_input(value: object) -> object:
    if type(value) is not float:
        raise ValueError("value must be an exact float")
    return value


def _tuple_input(value: object) -> object:
    if type(value) is list:
        return tuple(cast(list[object], value))
    return value


type ExactFloat = Annotated[float, BeforeValidator(_exact_float_input)]
type PositiveFloat = Annotated[ExactFloat, Field(gt=0.0)]
type NonnegativeFloat = Annotated[ExactFloat, Field(ge=0.0)]
type UnitFloat = Annotated[ExactFloat, Field(ge=0.0, le=1.0)]
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
type FloatTuple = Annotated[tuple[ExactFloat, ...], BeforeValidator(_tuple_input)]
type IntTuple = Annotated[tuple[StrictInt, ...], BeforeValidator(_tuple_input)]
type MethodName = Literal["autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate"]


def _require_close(actual: float, expected: float, *, name: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(f"{name} is inconsistent with its documented components")


def _require_normalized(values: tuple[float, ...], *, name: str) -> None:
    if not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(f"{name} must sum to one")


def _snap_near_integer(quotient: float) -> float:
    nearest = round(quotient)
    if abs(quotient - nearest) <= 4.0 * math.ulp(quotient):
        return float(nearest)
    return quotient


class _StrictArtifactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class _DiagnosticModel(_StrictArtifactModel):
    """A typed diagnostic record that retains the existing mapping interface."""

    def __getitem__(self, key: str) -> FrozenJsonValue:
        return cast(FrozenJsonValue, getattr(self, key))

    def get(self, key: str) -> FrozenJsonValue | None:
        return cast(FrozenJsonValue | None, getattr(self, key, None))

    def __contains__(self, key: object) -> bool:
        return type(key) is str and key in type(self).model_fields


class FrameSizeDiagnostic(_DiagnosticModel):
    observation_window_seconds: PositiveFloat
    distance: UnitFloat
    reference_count: PositiveInt
    generated_count: PositiveInt
    reference_minimum_length: PositiveInt
    reference_maximum_length: PositiveInt
    generated_minimum_length: PositiveInt
    generated_maximum_length: PositiveInt

    @model_validator(mode="after")
    def minima_do_not_exceed_maxima(self) -> Self:
        if (
            self.reference_minimum_length > self.reference_maximum_length
            or self.generated_minimum_length > self.generated_maximum_length
        ):
            raise ValueError("frame_size_ks diagnostics minimum lengths must not exceed maximum lengths")
        return self


class IatDiagnostic(_DiagnosticModel):
    observation_window_seconds: PositiveFloat
    distance: UnitFloat
    diagnostic_quantile: Annotated[ExactFloat, Field(gt=0.0, lt=1.0)]
    reference_iat_count: PositiveInt
    generated_iat_count: PositiveInt
    reference_zero_iat_count: NonnegativeInt
    generated_zero_iat_count: NonnegativeInt
    reference_median_iat_seconds: NonnegativeFloat
    generated_median_iat_seconds: NonnegativeFloat
    reference_quantile_iat_seconds: NonnegativeFloat
    generated_quantile_iat_seconds: NonnegativeFloat

    @model_validator(mode="after")
    def zero_counts_do_not_exceed_samples(self) -> Self:
        if (
            self.reference_zero_iat_count > self.reference_iat_count
            or self.generated_zero_iat_count > self.generated_iat_count
        ):
            raise ValueError("iat_ks diagnostics zero-IAT counts must not exceed their sample counts")
        return self


class AcfFeatureDiagnostic(_StrictArtifactModel):
    reference_sample_count: PositiveInt
    generated_sample_count: PositiveInt
    reference_acf: FloatTuple
    generated_acf: FloatTuple
    absolute_differences: FloatTuple
    discrepancy: UnitFloat


class AcfFeatureWeights(_StrictArtifactModel):
    iat: UnitFloat
    size: UnitFloat


class AutocorrelationDiagnostic(_DiagnosticModel):
    observation_window_seconds: PositiveFloat
    lags: IntTuple
    lag_weights: FloatTuple
    feature_weights: AcfFeatureWeights
    iat: AcfFeatureDiagnostic
    size: AcfFeatureDiagnostic
    discrepancy: UnitFloat

    @model_validator(mode="after")
    def validate_acf_arithmetic(self) -> Self:
        if not self.lags or any(lag <= 0 for lag in self.lags) or len(self.lags) != len(set(self.lags)):
            raise ValueError("autocorrelation diagnostics.lags must contain unique positive integers")
        if len(self.lag_weights) != len(self.lags):
            raise ValueError("autocorrelation diagnostics.lag_weights length must match lags")
        if any(not 0.0 <= weight <= 1.0 for weight in self.lag_weights):
            raise ValueError("autocorrelation diagnostics.lag_weights must be in [0, 1]")
        _require_normalized(self.lag_weights, name="autocorrelation diagnostics.lag_weights")
        feature_weights = (self.feature_weights.iat, self.feature_weights.size)
        _require_normalized(feature_weights, name="autocorrelation diagnostics.feature_weights")
        feature_discrepancies: list[float] = []
        for name, feature in (("iat", self.iat), ("size", self.size)):
            if any(lag >= feature.reference_sample_count or lag >= feature.generated_sample_count for lag in self.lags):
                raise ValueError(f"autocorrelation diagnostics.{name} sample counts must exceed every configured lag")
            if not (
                len(feature.reference_acf)
                == len(feature.generated_acf)
                == len(feature.absolute_differences)
                == len(self.lags)
            ):
                raise ValueError(f"autocorrelation diagnostics.{name} ACF vectors must match the configured lag count")
            if any(not -1.0 <= value <= 1.0 for value in (*feature.reference_acf, *feature.generated_acf)):
                raise ValueError(f"autocorrelation diagnostics.{name} ACF values must be in [-1, 1]")
            if any(not 0.0 <= value <= 2.0 for value in feature.absolute_differences):
                raise ValueError(f"autocorrelation diagnostics.{name} differences must be in [0, 2]")
            for index, (reference, generated, difference) in enumerate(
                zip(feature.reference_acf, feature.generated_acf, feature.absolute_differences, strict=True)
            ):
                _require_close(
                    difference,
                    abs(reference - generated),
                    name=f"autocorrelation diagnostics.{name} difference {index}",
                )
            expected = math.fsum(
                weight * difference / 2.0
                for weight, difference in zip(self.lag_weights, feature.absolute_differences, strict=True)
            )
            _require_close(feature.discrepancy, expected, name=f"autocorrelation diagnostics.{name}.discrepancy")
            feature_discrepancies.append(feature.discrepancy)
        expected = math.fsum(
            weight * discrepancy for weight, discrepancy in zip(feature_weights, feature_discrepancies, strict=True)
        )
        _require_close(self.discrepancy, expected, name="autocorrelation diagnostics.discrepancy")
        return self


class DirectionValues(_StrictArtifactModel):
    outbound: NonnegativeInt
    inbound: NonnegativeInt


class DirectionTotals(_StrictArtifactModel):
    packet: DirectionValues
    byte: DirectionValues


class MultiscaleFeatureWeights(_StrictArtifactModel):
    packet: UnitFloat
    byte: UnitFloat


class MultiscaleScaleDiagnostic(_StrictArtifactModel):
    width_seconds: PositiveFloat
    bins_per_direction: PositiveInt
    direction_bin_cell_count: PositiveInt
    reference_totals: DirectionTotals
    generated_totals: DirectionTotals
    feature_discrepancies: MultiscaleFeatureWeights
    discrepancy: UnitFloat


class MultiscaleDiagnostic(_DiagnosticModel):
    observation_window_seconds: PositiveFloat
    widths: FloatTuple
    scale_weights: FloatTuple
    feature_weights: MultiscaleFeatureWeights
    direction_bin_cell_counts: IntTuple
    total_direction_bin_cells: PositiveInt
    scales: Annotated[tuple[MultiscaleScaleDiagnostic, ...], BeforeValidator(_tuple_input)]
    scale_discrepancies: FloatTuple
    feature_discrepancies: MultiscaleFeatureWeights
    discrepancy: UnitFloat

    @model_validator(mode="after")
    def validate_scale_arithmetic(self) -> Self:
        if (
            not self.widths
            or any(width <= 0.0 or width > self.observation_window_seconds for width in self.widths)
            or any(current <= previous for previous, current in zip(self.widths, self.widths[1:], strict=False))
        ):
            raise ValueError("multiscale_rate diagnostics.widths must be positive and strictly increasing within W")
        if len(self.scale_weights) != len(self.widths):
            raise ValueError("multiscale_rate diagnostics.scale_weights length must match widths")
        if any(not 0.0 <= weight <= 1.0 for weight in self.scale_weights):
            raise ValueError("multiscale_rate diagnostics.scale_weights must be in [0, 1]")
        _require_normalized(self.scale_weights, name="multiscale_rate diagnostics.scale_weights")
        feature_weights = (self.feature_weights.packet, self.feature_weights.byte)
        _require_normalized(feature_weights, name="multiscale_rate diagnostics.feature_weights")
        expected_counts: list[int] = []
        for width in self.widths:
            quotient = self.observation_window_seconds / width
            if not math.isfinite(quotient):
                raise ValueError("multiscale_rate diagnostics: W divided by a width must be finite")
            expected_counts.append(2 * math.ceil(_snap_near_integer(quotient)))
        if any(count <= 0 for count in self.direction_bin_cell_counts):
            raise ValueError("multiscale_rate diagnostics direction cell counts must be positive")
        if self.direction_bin_cell_counts != tuple(expected_counts):
            raise ValueError("multiscale_rate diagnostics.direction_bin_cell_counts are inconsistent with widths and W")
        if self.total_direction_bin_cells != sum(self.direction_bin_cell_counts):
            raise ValueError(
                "multiscale_rate diagnostics.total_direction_bin_cells must equal the direction cell count sum"
            )
        if len(self.scales) != len(self.widths):
            raise ValueError("multiscale_rate diagnostics.scales must match the width count")

        packet_discrepancies: list[float] = []
        byte_discrepancies: list[float] = []
        scale_discrepancies: list[float] = []
        reference_totals: DirectionTotals | None = None
        generated_totals: DirectionTotals | None = None
        for index, scale in enumerate(self.scales):
            if scale.width_seconds != self.widths[index]:
                raise ValueError(f"multiscale_rate diagnostics.scales[{index}].width_seconds must equal its width")
            if scale.bins_per_direction * 2 != self.direction_bin_cell_counts[index]:
                raise ValueError(f"multiscale_rate diagnostics.scales[{index}].bins_per_direction is inconsistent")
            if scale.direction_bin_cell_count != self.direction_bin_cell_counts[index]:
                raise ValueError(
                    f"multiscale_rate diagnostics.scales[{index}].direction_bin_cell_count is inconsistent"
                )
            if reference_totals is None:
                reference_totals = scale.reference_totals
                generated_totals = scale.generated_totals
            elif scale.reference_totals != reference_totals or scale.generated_totals != generated_totals:
                raise ValueError("multiscale_rate diagnostics packet and byte totals must be consistent across scales")
            packet = scale.feature_discrepancies.packet
            byte = scale.feature_discrepancies.byte
            expected = math.fsum((feature_weights[0] * packet, feature_weights[1] * byte))
            _require_close(scale.discrepancy, expected, name=f"multiscale_rate diagnostics.scales[{index}].discrepancy")
            packet_discrepancies.append(packet)
            byte_discrepancies.append(byte)
            scale_discrepancies.append(scale.discrepancy)
        if self.scale_discrepancies != tuple(scale_discrepancies):
            raise ValueError("multiscale_rate diagnostics.scale_discrepancies must match retained scales")
        packet_total = math.fsum(
            weight * value for weight, value in zip(self.scale_weights, packet_discrepancies, strict=True)
        )
        byte_total = math.fsum(
            weight * value for weight, value in zip(self.scale_weights, byte_discrepancies, strict=True)
        )
        _require_close(
            self.feature_discrepancies.packet,
            packet_total,
            name="multiscale_rate diagnostics.feature_discrepancies.packet",
        )
        _require_close(
            self.feature_discrepancies.byte,
            byte_total,
            name="multiscale_rate diagnostics.feature_discrepancies.byte",
        )
        expected = math.fsum((feature_weights[0] * packet_total, feature_weights[1] * byte_total))
        _require_close(self.discrepancy, expected, name="multiscale_rate diagnostics.discrepancy")
        scale_expected = math.fsum(
            weight * value for weight, value in zip(self.scale_weights, scale_discrepancies, strict=True)
        )
        _require_close(self.discrepancy, scale_expected, name="multiscale_rate diagnostics.scale-weighted discrepancy")
        return self


def _diagnostic_discriminator(value: object) -> str | None:
    if isinstance(value, FrameSizeDiagnostic) or isinstance(value, IatDiagnostic):
        return "iat_ks" if isinstance(value, IatDiagnostic) else "frame_size_ks"
    if isinstance(value, AutocorrelationDiagnostic):
        return "autocorrelation"
    if isinstance(value, MultiscaleDiagnostic):
        return "multiscale_rate"
    if isinstance(value, Mapping):
        if "lags" in value:
            return "autocorrelation"
        if "widths" in value:
            return "multiscale_rate"
        if "diagnostic_quantile" in value:
            return "iat_ks"
        if "distance" in value:
            return "frame_size_ks"
    return None


type MethodDiagnostic = Annotated[
    Annotated[AutocorrelationDiagnostic, Tag("autocorrelation")]
    | Annotated[FrameSizeDiagnostic, Tag("frame_size_ks")]
    | Annotated[IatDiagnostic, Tag("iat_ks")]
    | Annotated[MultiscaleDiagnostic, Tag("multiscale_rate")],
    Discriminator(_diagnostic_discriminator),
]


def _bounded_weighted_score(value: float) -> float:
    """Clamp only the weight-sum tolerance already accepted by configuration."""
    if -_WEIGHT_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _WEIGHT_TOLERANCE:
        return 1.0
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("aggregate_score must be a finite float in [0, 1]")
    return value


class MethodComparison(_StrictArtifactModel):
    """One configured method's immutable score, weight, and retained diagnostics."""

    score: UnitFloat
    weight: UnitFloat
    diagnostics: MethodDiagnostic

    @field_validator("diagnostics", mode="before")
    @classmethod
    def thaw_diagnostics(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return SimilarityResult(0.0, cast(Mapping[str, object], value)).as_dict()["diagnostics"]

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        diagnostic_tag = _diagnostic_discriminator(self.diagnostics)
        if diagnostic_tag is None:
            raise ValueError("diagnostics must identify one supported method")
        discrepancy = (
            self.diagnostics.distance
            if isinstance(self.diagnostics, (FrameSizeDiagnostic, IatDiagnostic))
            else self.diagnostics.discrepancy
        )
        _require_close(self.score, 1.0 - discrepancy, name=f"{diagnostic_tag} score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a fresh ordinary JSON representation."""
        return {
            "diagnostics": cast(dict[str, JsonValue], self.diagnostics.model_dump(mode="json")),
            "score": self.score,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, method_name: str, value: object) -> Self:
        """Strictly validate one method object from parsed JSON."""
        if method_name not in _METHOD_NAMES:
            raise ValueError(f"unsupported comparison method {method_name!r}")
        prepared: object = value
        if type(value) is dict:
            fields = dict(cast(dict[str, object], value))
            if "method" in fields:
                fields["_persisted_method"] = fields.pop("method")
            prepared = fields
        try:
            result = cls.model_validate(prepared)
            if _diagnostic_discriminator(result.diagnostics) != method_name:
                raise ValueError(f"{method_name} diagnostics use the wrong method discriminator")
            return result
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            if field.endswith("observation_window_seconds"):
                raise ValueError(
                    "every method diagnostic observation window must be a finite positive float"
                ) from error
            if field.endswith("reference_count") and first["type"] == "int_type":
                raise ValueError("reference_count must be an integer") from error
            raise ValueError(f"invalid method result {field}: {first['msg']}") from error


class ComparisonMethods(_StrictArtifactModel):
    autocorrelation: MethodComparison
    frame_size_ks: MethodComparison
    iat_ks: MethodComparison
    multiscale_rate: MethodComparison

    @field_validator("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate", mode="before")
    @classmethod
    def methods_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value

    @model_validator(mode="after")
    def mapping_keys_match_diagnostics(self) -> Self:
        for name, method in self.items():
            if _diagnostic_discriminator(method.diagnostics) != name:
                raise ValueError(f"{name} diagnostics use the wrong method discriminator")
        return self

    def __getitem__(self, name: str) -> MethodComparison:
        if name not in _METHOD_NAMES:
            raise KeyError(name)
        return cast(MethodComparison, getattr(self, name))

    def keys(self) -> tuple[str, ...]:
        return _METHOD_NAMES

    def items(self) -> tuple[tuple[str, MethodComparison], ...]:
        return tuple((name, self[name]) for name in _METHOD_NAMES)

    def values(self) -> tuple[MethodComparison, ...]:
        return tuple(self[name] for name in _METHOD_NAMES)


class ContentIdentityPayload(_StrictArtifactModel):
    size: NonnegativeInt
    sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class ComparisonInputIdentities(_StrictArtifactModel):
    capture_json: ContentIdentityPayload
    generated_pcapng: ContentIdentityPayload
    reference_pcapng: ContentIdentityPayload
    similarity_settings: ContentIdentityPayload

    @field_validator("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings", mode="before")
    @classmethod
    def identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        if type(value) is ContentIdentity:
            return value.as_dict()
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value

    def __getitem__(self, name: str) -> ContentIdentityPayload:
        if name not in _INPUT_NAMES:
            raise KeyError(name)
        return cast(ContentIdentityPayload, getattr(self, name))

    def keys(self) -> tuple[str, ...]:
        return _INPUT_NAMES

    def items(self) -> tuple[tuple[str, ContentIdentityPayload], ...]:
        return tuple((name, self[name]) for name in _INPUT_NAMES)

    def as_content_identities(self) -> dict[str, ContentIdentity]:
        return {name: ContentIdentity(size=identity.size, sha256=identity.sha256) for name, identity in self.items()}


class ComparisonResult(_StrictArtifactModel):
    """One deeply immutable comparison result, optionally carrying artifact identities."""

    # Construction copies nested method and identity mappings before exposing
    # them.  Callers can safely retain this object as publication evidence even
    # if the dictionaries used to build it are later mutated.

    aggregate_score: UnitFloat
    observation_window_seconds: PositiveFloat
    methods: ComparisonMethods
    input_identities: ComparisonInputIdentities | None

    @field_validator("methods", mode="before")
    @classmethod
    def methods_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, ComparisonMethods):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            methods = cast(Mapping[str, object], value)
            return {
                name: method.model_dump(mode="python") if isinstance(method, BaseModel) else method
                for name, method in methods.items()
            }
        return value

    @field_validator("input_identities", mode="before")
    @classmethod
    def input_identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, ComparisonInputIdentities):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            identities = cast(Mapping[str, object], value)
            return {
                name: identity.as_dict()
                if type(identity) is ContentIdentity
                else identity.model_dump(mode="python")
                if isinstance(identity, BaseModel)
                else identity
                for name, identity in identities.items()
            }
        return value

    @model_validator(mode="after")
    def validate_local_arithmetic(self) -> Self:
        for _name, method in self.methods.items():
            diagnostic_window = method.diagnostics.get("observation_window_seconds")
            if diagnostic_window != self.observation_window_seconds:
                raise ValueError("every method diagnostic must contain the shared observation window")
        weight_sum = math.fsum(method.weight for method in self.methods.values())
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
            raise ValueError("method weights must sum to one")
        weighted_score = math.fsum(method.weight * method.score for method in self.methods.values())
        if not math.isclose(weighted_score, self.aggregate_score, rel_tol=0.0, abs_tol=_WEIGHT_TOLERANCE):
            raise ValueError("aggregate_score must equal the exact configured weighted sum")
        return self

    @property
    def input_sha256(self) -> Mapping[str, str] | None:
        """Expose digests for diagnostics that do not need byte counts."""
        if self.input_identities is None:
            return None
        return MappingProxyType({name: identity.sha256 for name, identity in self.input_identities.items()})

    def with_input_identities(
        self,
        identities: Mapping[str, ContentIdentity | ContentIdentityPayload] | ComparisonInputIdentities,
    ) -> Self:
        """Return the same scientific result with exact file and settings identities."""
        return type(self).model_validate(
            {
                "aggregate_score": self.aggregate_score,
                "observation_window_seconds": self.observation_window_seconds,
                "methods": self.methods,
                "input_identities": identities,
            }
        )

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the exact publishable JSON shape as fresh mutable values."""
        if self.input_identities is None:
            raise ValueError("input content identities are required for a similarity artifact")
        dumped = self.model_dump(mode="json")
        return {
            "aggregate_score": cast(float, dumped["aggregate_score"]),
            "input_identities": cast(dict[str, JsonValue], dumped["input_identities"]),
            "methods": {name: method.as_dict() for name, method in self.methods.items()},
            "observation_window_seconds": cast(float, dumped["observation_window_seconds"]),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Strictly validate the documented similarity artifact object."""
        try:
            return cls.model_validate(value)
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            if field.endswith("observation_window_seconds"):
                raise ValueError(
                    "every method diagnostic observation window must be a finite positive float"
                ) from error
            if field.endswith("reference_count") and first["type"] == "int_type":
                raise ValueError("reference_count must be an integer") from error
            raise ValueError(f"invalid comparison result {field}: {first['msg']}") from error


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
    raw_methods = cast(object, result.methods)
    if isinstance(raw_methods, ComparisonMethods):
        method_values: tuple[tuple[str, object], ...] = cast(tuple[tuple[str, object], ...], raw_methods.items())
    elif isinstance(raw_methods, Mapping):
        method_values = tuple(cast(Mapping[str, object], raw_methods).items())
    else:
        raise ValueError("comparison methods must be a canonical methods object")
    input_values: object = result.input_identities
    if isinstance(result.input_identities, ComparisonInputIdentities):
        input_values = result.input_identities.model_dump(mode="python")
    validated = ComparisonResult.model_validate(
        {
            "aggregate_score": result.aggregate_score,
            "observation_window_seconds": result.observation_window_seconds,
            "methods": {
                name: method.model_dump(mode="python") if isinstance(method, BaseModel) else method
                for name, method in method_values
            },
            "input_identities": input_values,
        }
    )
    content = (json.dumps(validated.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode(
        "utf-8"
    )
    reparsed = ComparisonResult.from_dict(json.loads(content.decode("utf-8")))
    if reparsed != validated:
        raise ValueError("canonical similarity rendering changed the validated comparison result")
    return content


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
    except FileNotFoundError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                f"could not read {kind} {path}: {error}",
                corrective_action=corrective_action,
            ),
            kind="artifact_missing",
            stage="compare",
            affected_evidence=path.name,
            evidence_state="not_published",
        ) from error
    except OSError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                f"could not read {kind} {path}: {error}",
                corrective_action=corrective_action,
            ),
            kind="artifact_corrupt",
            stage="compare",
            affected_evidence=path.name,
            evidence_state="preserved",
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


def similarity_settings_identity(settings: SimilarityConfig) -> ContentIdentity:
    """Identify the exact canonical effective similarity settings bytes."""
    content = json.dumps(
        settings.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return identify_bytes(content)


def compare_traces(
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: float,
    settings: SimilarityConfig,
) -> ComparisonResult:
    """Evaluate all four configured metrics over exactly one observation window."""
    reference_trace = reference if type(reference) is TrafficTrace else TrafficTrace.from_events(reference)
    generated_trace = generated if type(generated) is TrafficTrace else TrafficTrace.from_events(generated)
    frame_size = frame_size_ks(reference_trace, generated_trace, W)
    interarrival = iat_ks(reference_trace, generated_trace, W, settings.iat_diagnostic_quantile)
    try:
        autocorrelation = autocorrelation_similarity(
            reference_trace,
            generated_trace,
            W,
            settings.acf_lags,
            settings.acf_lag_weights,
            settings.acf_iat_weight,
            settings.acf_size_weight,
        )
    except AutocorrelationSamplesInsufficientError as error:
        raise TrafficlabError(
            "autocorrelation requires more samples",
            corrective_action="correct samples or settings",
        ) from error
    component_results = {
        "frame_size_ks": frame_size,
        "iat_ks": interarrival,
        "autocorrelation": autocorrelation,
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
            name: MethodComparison.model_validate(
                {
                    "score": component_results[name].score,
                    "weight": configured_weights[name],
                    "diagnostics": component_results[name].diagnostics,
                }
            )
            for name in _METHOD_NAMES
        }
        aggregate = _bounded_weighted_score(math.fsum(method.weight * method.score for method in methods.values()))
        return ComparisonResult.model_validate(
            {
                "aggregate_score": aggregate,
                "observation_window_seconds": W,
                "methods": methods,
                "input_identities": None,
            }
        )
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
    elif isinstance(error, TrafficlabError):
        detail = str(error)
        action = error.corrective_action
    else:
        detail = f"could not publish similarity artifact {destination}: {error}"
        action = "verify the run directory is writable and has available space"
    if cleanup_error is not None:
        detail = f"{detail}; cleanup incomplete: could not remove owned temporary file: {cleanup_error}"
    if isinstance(error, OSError) and not isinstance(error, FileExistsError):
        outcome_detail = "similarity.json durability check failed"
        outcome_action = "correct storage and rerun compare"
    else:
        outcome_detail = detail
        outcome_action = action
    if isinstance(error, TrafficlabError) and error.failure_outcomes:
        outcomes = error.failure_outcomes
    else:
        outcome = FailureOutcome(
            kind="publication_collision" if isinstance(error, FileExistsError) else "publication_failed",
            stage="compare",
            detail=outcome_detail,
            affected_evidence="similarity.json",
            evidence_state="preserved" if isinstance(error, FileExistsError) else "not_published",
            corrective_action=outcome_action,
            authority="primary",
        )
        outcomes = (outcome,)
    if cleanup_error is not None:
        outcomes = (
            *outcomes,
            FailureOutcome(
                kind="cleanup_failed",
                stage="compare",
                detail=f"owned temporary file cleanup failed: {cleanup_error}",
                affected_evidence="inventory",
                evidence_state="possibly_remaining",
                corrective_action="remove the owned temporary file after preserving diagnostics",
                authority="secondary",
            ),
        )
    return _PublicationError(detail, corrective_action=action, failure_outcomes=outcomes)


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
            fsync_published_artifact(
                destination,
                stage="compare",
                affected_evidence="similarity.json",
            )
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
                failure_outcomes=(
                    FailureOutcome(
                        kind="publication_failed",
                        stage="compare",
                        detail=detail,
                        affected_evidence="similarity.json",
                        evidence_state="preserved",
                        corrective_action=(
                            "preserve the published result and remove the reported temporary file if it is still owned"
                        ),
                        authority="primary",
                    ),
                    FailureOutcome(
                        kind="cleanup_failed",
                        stage="compare",
                        detail=f"owned temporary file cleanup failed: {cleanup_error}",
                        affected_evidence="inventory",
                        evidence_state="possibly_remaining",
                        corrective_action="remove the owned temporary file after preserving diagnostics",
                        authority="secondary",
                    ),
                ),
            ) from cleanup_error
        raise cleanup_error
    return created_by_call


def _append_failure(run_directory: Path, primary: TrafficlabError, *, failure_kind: str) -> None:
    outcome = primary.failure_outcome
    if outcome is None:
        outcome_kind = "publication_failed" if failure_kind == "publication" else "metric_infeasible"
        outcome = failure_outcome_from_error(
            primary,
            kind=outcome_kind,
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="not_published",
        )
        primary.failure_outcomes = (outcome,)
        primary.failure_outcome = outcome
    try:
        record: dict[str, object] = {
            "detail": str(primary),
            "event": "comparison_failed",
            "failure_kind": failure_kind,
            "failure_outcome": outcome.as_dict(),
            "stage": "compare",
        }
        if primary.failure_outcomes[1:]:
            record["secondary_outcomes"] = [item.as_dict() for item in primary.failure_outcomes[1:]]
        append_run_log(run_directory, record)
    except TrafficlabError as logging_error:
        append_failure_outcome(
            primary,
            failure_outcome_from_error(
                logging_error,
                kind="publication_failed",
                stage="compare",
                affected_evidence="run.log",
                evidence_state="not_published",
                authority="secondary",
            ),
        )
        primary.args = (f"{primary}; additionally could not append comparison failure to run.log: {logging_error}",)


def compare_experiment(experiment_path: Path) -> ComparisonResult:
    """Compare one existing run using its matching authoritative configuration snapshot."""
    caller_config = load_experiment(experiment_path)
    run_directory = caller_config.run.directory
    output_path = run_directory / "similarity.json"
    try:
        snapshot_config = load_experiment(run_directory / "experiment.toml")
        if caller_config != snapshot_config:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"caller configuration {experiment_path} does not match the authoritative run snapshot",
                    corrective_action="use the exact experiment configuration that created this run",
                ),
                kind="artifact_foreign",
                stage="compare",
                affected_evidence="experiment.toml",
                evidence_state="preserved",
            )
        metadata_path = run_directory / "capture.json"
        reference_path = run_directory / "reference.pcapng"
        generated_path = run_directory / "generated.pcapng"
        metadata_content = _read_comparison_input(
            metadata_path,
            kind="capture metadata",
            corrective_action="verify capture.json exists and is readable",
        )
        try:
            metadata = parse_capture_metadata(metadata_content, source=metadata_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="capture.json",
                evidence_state="preserved",
            ) from error
        reference_content = _read_comparison_input(
            reference_path,
            kind="PCAPNG",
            corrective_action="verify the PCAPNG exists and is readable",
        )
        try:
            reference_events = parse_pcapng_bytes(reference_content, metadata, source=reference_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="reference.pcapng",
                evidence_state="preserved",
            ) from error
        model_path = run_directory / "best_model.json"
        model_content = _read_comparison_input(
            model_path,
            kind="best model",
            corrective_action="verify best_model.json is readable",
        )
        model_identity = identify_bytes(model_content)
        try:
            best = load_best_model(model_content, source=model_path)
        except ScientificArtifactSchemaError as error:
            raise attach_failure_outcome(
                error,
                kind="scientific_semantics_incompatible",
                stage="compare",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        try:
            require_compatible(
                {
                    "reference identity": best.reference_identity,
                    "capture identity": best.capture_identity,
                    "final seed": best.final_seed,
                    "final generation limits": best.final_limits,
                },
                {
                    "reference identity": identify_bytes(reference_content),
                    "capture identity": identify_bytes(metadata_content),
                    "final seed": snapshot_config.run.final_seed,
                    "final generation limits": snapshot_config.generation.final,
                },
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"best_model.json is incompatible with current comparison inputs: {error}",
                    corrective_action="restore the exact fitted model and matching reference, capture, final seed, and limits",
                ),
                kind="artifact_foreign",
                stage="compare",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        try:
            _, _, expected_generated_content = reproduce_generated_pcapng(best, metadata, clock=lambda: 0.0)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="generation_incomplete",
                stage="compare",
                affected_evidence="generated.pcapng",
                evidence_state="not_published",
            ) from error
        generated_content = _read_comparison_input(
            generated_path,
            kind="PCAPNG",
            corrective_action="verify the PCAPNG exists and is readable",
        )
        if generated_content != expected_generated_content:
            raise TrafficlabError(
                "generated.pcapng is foreign",
                corrective_action="regenerate from the current fitted model",
                failure_outcome=FailureOutcome(
                    kind="artifact_foreign",
                    stage="compare",
                    detail="generated.pcapng is foreign",
                    affected_evidence="generated.pcapng",
                    evidence_state="preserved",
                    corrective_action="regenerate from the current fitted model",
                    authority="primary",
                ),
            )
        try:
            generated_events = parse_pcapng_bytes(generated_content, metadata, source=generated_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="generated.pcapng",
                evidence_state="preserved",
            ) from error
        try:
            reference, window = normalize_reference(reference_events)
            generated = align_generated(generated_events, window)
            result = compare_traces(reference, generated, window, snapshot_config.similarity).with_input_identities(
                {
                    "capture_json": identify_bytes(metadata_content),
                    "generated_pcapng": identify_bytes(generated_content),
                    "reference_pcapng": identify_bytes(reference_content),
                    "similarity_settings": similarity_settings_identity(snapshot_config.similarity),
                }
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="metric_infeasible",
                stage="compare",
                affected_evidence="similarity.json",
                evidence_state="not_published",
            ) from error
        authoritative_inputs = [
            (
                "experiment.toml",
                run_directory / "experiment.toml",
                identify_bytes(render_effective_config(snapshot_config)),
            ),
            ("capture.json", metadata_path, identify_bytes(metadata_content)),
            ("reference.pcapng", reference_path, identify_bytes(reference_content)),
            ("generated.pcapng", generated_path, identify_bytes(generated_content)),
        ]
        authoritative_inputs.append(("best_model.json", model_path, model_identity))
        for evidence, source_path, expected_identity in authoritative_inputs:
            try:
                require_compatible({evidence: expected_identity}, {evidence: identify_file(source_path)})
            except TrafficlabError as error:
                raise attach_failure_outcome(
                    TrafficlabError(
                        f"{evidence} changed during compare",
                        corrective_action="restore the exact comparison inputs and rerun compare",
                    ),
                    kind="artifact_changed",
                    stage="compare",
                    affected_evidence=evidence,
                    evidence_state="preserved",
                ) from error
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
        error = TrafficlabError(
            f"comparison result was published at {output_path}, but success logging failed: {logging_error}",
            corrective_action=logging_error.corrective_action,
        )
        raise attach_failure_outcome(
            error,
            kind="publication_failed",
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="preserved",
        ) from logging_error
    return result
