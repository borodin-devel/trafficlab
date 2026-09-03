"""Traffic comparison diagnostics ownership."""

import math
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
)

FITNESS_METHOD_NAMES = (
    "autocorrelation",
    "frame_size_ks",
    "iat_ks",
    "multiscale_rate",
    "cramer_von_mises",
    "anderson_darling",
    "jensen_shannon",
    "approximate_mmd",
)

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

type MethodName = Literal[
    "autocorrelation",
    "frame_size_ks",
    "iat_ks",
    "multiscale_rate",
    "cramer_von_mises",
    "anderson_darling",
    "jensen_shannon",
    "approximate_mmd",
]


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


require_normalized = _require_normalized
snap_near_integer = _snap_near_integer
tuple_input = _tuple_input


class StrictArtifactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )
