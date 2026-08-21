"""Traffic comparison diagnostics ownership."""

import math
from collections.abc import Mapping
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    StrictInt,
    Tag,
    model_validator,
)

from trafficlab.comparison.similarity.common import FrozenJsonValue

METHOD_NAMES = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")

WEIGHT_TOLERANCE = 1e-12


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


def require_close(actual: float, expected: float, *, name: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
        raise ValueError(f"{name} is inconsistent with its documented components")


def _require_normalized(values: tuple[float, ...], *, name: str) -> None:
    if not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
        raise ValueError(f"{name} must sum to one")


def _snap_near_integer(quotient: float) -> float:
    nearest = round(quotient)
    if abs(quotient - nearest) <= 4.0 * math.ulp(quotient):
        return float(nearest)
    return quotient


class StrictArtifactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class _DiagnosticModel(StrictArtifactModel):
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


class AcfFeatureDiagnostic(StrictArtifactModel):
    reference_sample_count: PositiveInt
    generated_sample_count: PositiveInt
    reference_acf: FloatTuple
    generated_acf: FloatTuple
    absolute_differences: FloatTuple
    discrepancy: UnitFloat


class AcfFeatureWeights(StrictArtifactModel):
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
                require_close(
                    difference,
                    abs(reference - generated),
                    name=f"autocorrelation diagnostics.{name} difference {index}",
                )
            expected = math.fsum(
                weight * difference / 2.0
                for weight, difference in zip(self.lag_weights, feature.absolute_differences, strict=True)
            )
            require_close(feature.discrepancy, expected, name=f"autocorrelation diagnostics.{name}.discrepancy")
            feature_discrepancies.append(feature.discrepancy)
        expected = math.fsum(
            weight * discrepancy for weight, discrepancy in zip(feature_weights, feature_discrepancies, strict=True)
        )
        require_close(self.discrepancy, expected, name="autocorrelation diagnostics.discrepancy")
        return self


class DirectionValues(StrictArtifactModel):
    outbound: NonnegativeInt
    inbound: NonnegativeInt


class DirectionTotals(StrictArtifactModel):
    packet: DirectionValues
    byte: DirectionValues


class MultiscaleFeatureWeights(StrictArtifactModel):
    packet: UnitFloat
    byte: UnitFloat


class MultiscaleScaleDiagnostic(StrictArtifactModel):
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
            require_close(scale.discrepancy, expected, name=f"multiscale_rate diagnostics.scales[{index}].discrepancy")
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
        require_close(
            self.feature_discrepancies.packet,
            packet_total,
            name="multiscale_rate diagnostics.feature_discrepancies.packet",
        )
        require_close(
            self.feature_discrepancies.byte,
            byte_total,
            name="multiscale_rate diagnostics.feature_discrepancies.byte",
        )
        expected = math.fsum((feature_weights[0] * packet_total, feature_weights[1] * byte_total))
        require_close(self.discrepancy, expected, name="multiscale_rate diagnostics.discrepancy")
        scale_expected = math.fsum(
            weight * value for weight, value in zip(self.scale_weights, scale_discrepancies, strict=True)
        )
        require_close(self.discrepancy, scale_expected, name="multiscale_rate diagnostics.scale-weighted discrepancy")
        return self


def diagnostic_discriminator(value: object) -> str | None:
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
    Discriminator(diagnostic_discriminator),
]
