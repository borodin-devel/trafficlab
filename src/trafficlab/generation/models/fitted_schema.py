"""Strict wire payload schemas for fitted traffic-model artifacts."""

from __future__ import annotations

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


def _exact_float_input(value: object) -> object:
    if type(value) is not float:
        raise ValueError("value must be an exact float")
    return value


def tuple_input(value: object) -> object:
    if type(value) is list:
        return tuple(cast(list[object], value))
    return value


type ExactFloat = Annotated[float, BeforeValidator(_exact_float_input)]
type PositiveFloat = Annotated[ExactFloat, Field(gt=0.0)]
type NonnegativeFloat = Annotated[ExactFloat, Field(ge=0.0)]
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
type FloatVector = Annotated[tuple[ExactFloat, ...], BeforeValidator(tuple_input)]
type FloatMatrix = Annotated[tuple[FloatVector, ...], BeforeValidator(tuple_input)]
type FloatCube = Annotated[tuple[FloatMatrix, ...], BeforeValidator(tuple_input)]
type IntVector = Annotated[tuple[StrictInt, ...], BeforeValidator(tuple_input)]
type DirectionName = Literal["outbound", "inbound"]
type TimingTierName = Literal["transition", "source", "global"]
type TimingTierVector = Annotated[tuple[TimingTierName, ...], BeforeValidator(tuple_input)]
type TimingTierMatrix = Annotated[tuple[TimingTierVector, ...], BeforeValidator(tuple_input)]


class _StrictWireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class MarkPayload(_StrictWireModel):
    direction: DirectionName
    frame_length: Annotated[StrictInt, Field(ge=14, le=2**32 - 1)]
    count: PositiveInt


type MarkPayloads = Annotated[tuple[MarkPayload, ...], BeforeValidator(tuple_input)]


class PoissonPayload(_StrictWireModel):
    base_rate: PositiveFloat
    rate: PositiveFloat
    marks: MarkPayloads

    @model_validator(mode="after")
    def marks_are_nonempty_and_unique(self) -> Self:
        if not self.marks:
            raise ValueError("marks must not be empty")
        if len({(mark.direction, mark.frame_length) for mark in self.marks}) != len(self.marks):
            raise ValueError("marks must be unique")
        return self


class MmppPayload(_StrictWireModel):
    q01: PositiveFloat
    q10: PositiveFloat
    lambda0: PositiveFloat
    lambda1: PositiveFloat
    marks: MarkPayloads

    @model_validator(mode="after")
    def rates_and_marks_are_ordered(self) -> Self:
        if self.lambda0 >= self.lambda1:
            raise ValueError("lambda0 must be strictly less than lambda1")
        if not self.marks:
            raise ValueError("marks must not be empty")
        if len({(mark.direction, mark.frame_length) for mark in self.marks}) != len(self.marks):
            raise ValueError("marks must be unique")
        return self


class MarkovStatePayload(_StrictWireModel):
    direction: DirectionName
    frame_lengths: IntVector
    size_bin: Annotated[StrictInt, Field(ge=0, le=2)]
    source_iats: FloatVector


type MarkovStatePayloads = Annotated[tuple[MarkovStatePayload, ...], BeforeValidator(tuple_input)]


class TimingUsageCountsPayload(_StrictWireModel):
    global_: NonnegativeInt = Field(alias="global")
    source: NonnegativeInt
    transition: NonnegativeInt


class MarkovTimingPayload(_StrictWireModel):
    reference_usage_counts: TimingUsageCountsPayload
    transition_tiers: TimingTierMatrix
    unobserved_rows: IntVector


class MarkovRenewalPayload(_StrictWireModel):
    alpha: NonnegativeFloat
    conditional_iats: FloatCube
    global_iats: FloatVector
    minimum_support: PositiveInt
    states: MarkovStatePayloads
    thresholds: Annotated[tuple[ExactFloat, ExactFloat], BeforeValidator(tuple_input)]
    time_scale: PositiveFloat
    timing_diagnostics: MarkovTimingPayload
    transition_rows: FloatMatrix


def _family_payload_discriminator(value: object) -> str | None:
    if isinstance(value, PoissonPayload):
        return "poisson_empirical"
    if isinstance(value, MarkovRenewalPayload):
        return "markov_renewal"
    if isinstance(value, MmppPayload):
        return "mmpp"
    if isinstance(value, Mapping):
        if "base_rate" in value:
            return "poisson_empirical"
        if "transition_rows" in value:
            return "markov_renewal"
        if "q01" in value:
            return "mmpp"
    return None


type FamilyPayload = Annotated[
    Annotated[PoissonPayload, Tag("poisson_empirical")]
    | Annotated[MarkovRenewalPayload, Tag("markov_renewal")]
    | Annotated[MmppPayload, Tag("mmpp")],
    Discriminator(_family_payload_discriminator),
]


def validate_family_payload(value: object) -> FamilyPayload:
    discriminator = _family_payload_discriminator(value)
    if discriminator == "poisson_empirical":
        return PoissonPayload.model_validate(value)
    if discriminator == "markov_renewal":
        return MarkovRenewalPayload.model_validate(value)
    if discriminator == "mmpp":
        return MmppPayload.model_validate(value)
    raise ValueError("fitted payload does not identify one registered family")
