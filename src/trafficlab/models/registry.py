"""Closed built-in traffic-model registry and strict fitted-model artifact codec."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
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
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from trafficlab.compatibility import ContentIdentity
from trafficlab.config import (
    FamilyName,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovRenewalConfig,
    MmppConfig,
    PoissonConfig,
)
from trafficlab.errors import TrafficlabError
from trafficlab.models.common import FamilyBounds, FittedModel, Gene, Genes, ModelFamily, ReferenceTrace
from trafficlab.models.markov_renewal import MarkovRenewalFamily
from trafficlab.models.mmpp import MmppFamily
from trafficlab.models.poisson import PoissonFamily
from trafficlab.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION, require_current_scientific_schema

_OUTER_KEYS = {
    "version",
    "scientific_artifact_schema",
    "family",
    "genes",
    "fitted",
    "reference_identity",
    "capture_identity",
    "final_seed",
    "final_limits",
    "observation_window_seconds",
    "gene_bounds",
    "estimator_choices",
    "seed_policy",
}
_SEED_POLICY = {
    "empirical": "randrange",
    "exponential": "expovariate",
    "generator": "random.Random",
    "weighted": "random_cumulative",
}

POISSON_FAMILY = PoissonFamily()
MARKOV_RENEWAL_FAMILY = MarkovRenewalFamily()
MMPP_FAMILY = MmppFamily()

REGISTRY: MappingProxyType[str, ModelFamily] = MappingProxyType(
    {
        "poisson_empirical": POISSON_FAMILY,
        "markov_renewal": MARKOV_RENEWAL_FAMILY,
        "mmpp": MMPP_FAMILY,
    }
)


def get_family(name: str) -> ModelFamily:
    """Return one of the three built-in families and reject extension names."""
    try:
        return REGISTRY[name]
    except KeyError as error:
        raise TrafficlabError(
            f"unknown model family {name!r}",
            corrective_action="select poisson_empirical, markov_renewal, or mmpp",
        ) from error


def _family_name(name: str) -> FamilyName:
    """Narrow a name after closed-registry validation."""
    get_family(name)
    return cast(FamilyName, name)


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
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
type FloatVector = Annotated[tuple[ExactFloat, ...], BeforeValidator(_tuple_input)]
type FloatMatrix = Annotated[tuple[FloatVector, ...], BeforeValidator(_tuple_input)]
type FloatCube = Annotated[tuple[FloatMatrix, ...], BeforeValidator(_tuple_input)]
type IntVector = Annotated[tuple[StrictInt, ...], BeforeValidator(_tuple_input)]
type DirectionName = Literal["outbound", "inbound"]
type TimingTierName = Literal["transition", "source", "global"]
type TimingTierVector = Annotated[tuple[TimingTierName, ...], BeforeValidator(_tuple_input)]
type TimingTierMatrix = Annotated[tuple[TimingTierVector, ...], BeforeValidator(_tuple_input)]


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


type MarkPayloads = Annotated[tuple[MarkPayload, ...], BeforeValidator(_tuple_input)]


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


type MarkovStatePayloads = Annotated[tuple[MarkovStatePayload, ...], BeforeValidator(_tuple_input)]


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
    thresholds: Annotated[tuple[ExactFloat, ExactFloat], BeforeValidator(_tuple_input)]
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


def _validate_family_payload(value: object) -> FamilyPayload:
    discriminator = _family_payload_discriminator(value)
    if discriminator == "poisson_empirical":
        return PoissonPayload.model_validate(value)
    if discriminator == "markov_renewal":
        return MarkovRenewalPayload.model_validate(value)
    if discriminator == "mmpp":
        return MmppPayload.model_validate(value)
    raise ValueError("fitted payload does not identify one registered family")


class BestModel(BaseModel):
    """One fully self-contained, lineage-bound fitted traffic model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    version: Literal[1]
    scientific_artifact_schema: int
    family: FamilyName
    genes: Genes
    fitted: FamilyPayload
    reference_identity: ContentIdentity
    capture_identity: ContentIdentity
    final_seed: int
    final_limits: GenerationLimits
    observation_window_seconds: float
    gene_bounds: dict[str, IntegerBounds | FloatBounds]
    estimator_choices: dict[str, str | int | float]
    seed_policy: dict[str, str]

    @field_validator("version", mode="before")
    @classmethod
    def version_is_exact_integer_one(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("version must be exact integer 1")
        return value

    @field_validator("genes", mode="before")
    @classmethod
    def genes_use_immutable_runtime_shape(cls, value: object) -> object:
        return _tuple_input(value)

    @field_validator("reference_identity", "capture_identity", mode="before")
    @classmethod
    def identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        raw = value.as_dict() if type(value) is ContentIdentity else value
        return ContentIdentity.from_dict(raw)

    @field_validator("final_limits", mode="before")
    @classmethod
    def final_limits_are_reconstructed_from_primitives(cls, value: object) -> object:
        raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        return _parse_final_limits(raw)

    @field_validator("gene_bounds", mode="before")
    @classmethod
    def gene_bounds_are_reconstructed_from_primitives(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        bounds = cast(Mapping[str, object], value)
        return {
            name: bound.model_dump(mode="python") if isinstance(bound, BaseModel) else bound
            for name, bound in bounds.items()
        }

    @field_validator("fitted", mode="before")
    @classmethod
    def fitted_payload_is_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python", by_alias=True)
        return value

    @model_validator(mode="after")
    def validate_local_consistency(self) -> Self:
        _validate_best_model(self)
        return self

    @property
    def reference_sha256(self) -> str:
        """Expose the reference digest for callers that do not need its byte count."""
        return self.reference_identity.sha256

    @property
    def capture_sha256(self) -> str:
        """Expose the capture-metadata digest for existing comparison callers."""
        return self.capture_identity.sha256


def rebuild_best_model(model: BestModel, **changes: object) -> BestModel:
    """Reconstruct and fully revalidate a best model with explicit field changes."""
    values = model.model_dump(mode="python", by_alias=True)
    values.update(
        {
            name: value.model_dump(mode="python", by_alias=True)
            if isinstance(value, BaseModel)
            else value.as_dict()
            if type(value) is ContentIdentity
            else value
            for name, value in changes.items()
        }
    )
    return type(model).model_validate(values)


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_identity(value: object, *, name: str) -> ContentIdentity:
    if type(value) is not ContentIdentity:
        raise _invalid(
            f"invalid best-model {name} identity",
            corrective_action=f"provide the exact nested size and SHA-256 identity for the {name} artifact",
        )
    return value


def _parse_identity(value: object, *, name: str) -> ContentIdentity:
    try:
        return ContentIdentity.from_dict(value, name=f"best-model {name}")
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid best-model {name} identity: {error}",
            corrective_action=f"restore the exact nested size and SHA-256 identity for the {name} artifact",
        ) from error


def _validate_final_seed(value: object) -> int:
    if type(value) is not int or value < 0:
        raise _invalid(
            "invalid best-model final seed",
            corrective_action="provide one exact nonnegative integer final_seed",
        )
    return value


def _invalid_final_limits() -> TrafficlabError:
    return _invalid(
        "invalid best-model final limits",
        corrective_action=(
            "provide exactly positive integer max_packets and max_output_bytes plus a finite positive float "
            "max_wall_seconds"
        ),
    )


def _validate_final_limits(value: object) -> GenerationLimits:
    if type(value) is not GenerationLimits:
        raise _invalid_final_limits()
    return value


def _parse_final_limits(value: object) -> GenerationLimits:
    if type(value) is not dict:
        raise _invalid_final_limits()
    document = cast(dict[object, object], value)
    if set(document) != {"max_packets", "max_output_bytes", "max_wall_seconds"}:
        raise _invalid_final_limits()
    max_packets = document["max_packets"]
    max_output_bytes = document["max_output_bytes"]
    max_wall_seconds = document["max_wall_seconds"]
    if type(max_packets) is not int or max_packets <= 0:
        raise _invalid_final_limits()
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise _invalid_final_limits()
    if type(max_wall_seconds) is not float or max_wall_seconds <= 0.0:
        raise _invalid_final_limits()
    return GenerationLimits(
        max_packets=max_packets,
        max_output_bytes=max_output_bytes,
        max_wall_seconds=max_wall_seconds,
    )


def _validate_window(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise _invalid(
            "invalid best-model observation window",
            corrective_action="provide a finite positive float observation_window_seconds",
        )
    return value


def _is_integer_coordinate(family: ModelFamily, name: str) -> bool:
    return family is MARKOV_RENEWAL_FAMILY and name == "r"


def _bounds_from_config(family: ModelFamily, bounds: FamilyBounds) -> dict[str, FloatBounds | IntegerBounds]:
    if type(bounds) is not family.bounds_type:
        raise _invalid(
            f"invalid {family.name} gene bounds",
            corrective_action="provide the registered family's exact configured bounds type",
        )
    result: dict[str, FloatBounds | IntegerBounds] = {}
    for name in family.gene_names:
        value = getattr(bounds, name, None)
        expected_type = IntegerBounds if _is_integer_coordinate(family, name) else FloatBounds
        if type(value) is not expected_type:
            raise _invalid(
                f"invalid {family.name} gene bounds",
                corrective_action="provide every canonical named bound with its exact numeric type",
            )
        result[name] = value
    return result


def _build_bounds(family: ModelFamily, value: object) -> tuple[FamilyBounds, dict[str, FloatBounds | IntegerBounds]]:
    if type(value) is not dict:
        raise _invalid(
            f"invalid {family.name} gene bounds",
            corrective_action="provide one exact lower/upper object for every canonical gene name",
        )
    raw_bounds = cast(dict[object, object], value)
    if set(raw_bounds) != set(family.gene_names):
        raise _invalid(
            f"invalid {family.name} gene bounds",
            corrective_action="provide one exact lower/upper object for every canonical gene name",
        )

    parsed: dict[str, FloatBounds | IntegerBounds] = {}
    for name in family.gene_names:
        item = raw_bounds[name]
        if type(item) is not dict or set(cast(dict[object, object], item)) != {"lower", "upper"}:
            raise _invalid(
                f"invalid {family.name} gene bounds",
                corrective_action="provide exactly lower and upper for every canonical gene bound",
            )
        fields = cast(dict[str, object], item)
        bound_type: type[FloatBounds] | type[IntegerBounds]
        bound_type = IntegerBounds if _is_integer_coordinate(family, name) else FloatBounds
        scalar_type = int if bound_type is IntegerBounds else float
        if type(fields["lower"]) is not scalar_type or type(fields["upper"]) is not scalar_type:
            raise _invalid(
                f"invalid {family.name} gene bounds",
                corrective_action="use exact integer or float lower and upper values for each coordinate",
            )
        try:
            parsed[name] = bound_type(lower=fields["lower"], upper=fields["upper"])  # type: ignore[arg-type]
        except ValidationError as error:
            raise _invalid(
                f"invalid {family.name} gene bounds: {error}",
                corrective_action="provide finite ordered bounds satisfying the registered family constraints",
            ) from error

    try:
        if family is POISSON_FAMILY:
            config: FamilyBounds = PoissonConfig(c_lambda=cast(FloatBounds, parsed["c_lambda"]))
        elif family is MARKOV_RENEWAL_FAMILY:
            config = MarkovRenewalConfig(
                q1=cast(FloatBounds, parsed["q1"]),
                q2=cast(FloatBounds, parsed["q2"]),
                alpha=cast(FloatBounds, parsed["alpha"]),
                r=cast(IntegerBounds, parsed["r"]),
                c_t=cast(FloatBounds, parsed["c_t"]),
            )
        else:
            config = MmppConfig(
                q01=cast(FloatBounds, parsed["q01"]),
                q10=cast(FloatBounds, parsed["q10"]),
                lambda0=cast(FloatBounds, parsed["lambda0"]),
                lambda1=cast(FloatBounds, parsed["lambda1"]),
            )
    except ValidationError as error:
        raise _invalid(
            f"invalid {family.name} gene bounds: {error}",
            corrective_action="provide finite ordered bounds satisfying the registered family constraints",
        ) from error
    return (config, parsed)


def _validate_genes(family: ModelFamily, value: object, gene_bounds: dict[str, FloatBounds | IntegerBounds]) -> Genes:
    if type(value) is not tuple:
        raise _invalid(
            f"invalid {family.name} genes",
            corrective_action="provide the exact canonical immutable gene tuple for the registered family",
        )
    raw = cast(tuple[object, ...], value)
    if len(raw) != len(family.gene_names):
        raise _invalid(
            f"invalid {family.name} genes",
            corrective_action="provide the exact canonical gene array for the registered family",
        )
    for name, item in zip(family.gene_names, raw, strict=True):
        expected = int if type(gene_bounds[name]) is IntegerBounds else float
        bound = gene_bounds[name]
        if (
            type(item) is not expected
            or (type(item) is float and not math.isfinite(item))
            or not bound.lower <= item <= bound.upper  # type: ignore[operator]
        ):
            raise _invalid(
                f"invalid {family.name} genes",
                corrective_action="use exact finite canonical genes within every named bound",
            )
    if family is MARKOV_RENEWAL_FAMILY and not cast(float, raw[0]) < cast(float, raw[1]):
        raise _invalid(
            "invalid markov_renewal genes",
            corrective_action="persist repaired q1 strictly less than q2",
        )
    if family is MMPP_FAMILY and not cast(float, raw[2]) < cast(float, raw[3]):
        raise _invalid(
            "invalid mmpp genes",
            corrective_action="persist repaired lambda0 strictly less than lambda1",
        )
    return cast(Genes, tuple(raw))


def _parse_genes(family: ModelFamily, value: object, gene_bounds: dict[str, FloatBounds | IntegerBounds]) -> Genes:
    if type(value) is not list:
        raise _invalid(
            f"invalid {family.name} genes",
            corrective_action="provide the exact canonical JSON gene array for the registered family",
        )
    return _validate_genes(family, tuple(cast(list[object], value)), gene_bounds)


def _exact_mapping(value: object, expected: Mapping[str, str | int | float], *, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _invalid(
            f"invalid best-model {name}",
            corrective_action=f"restore the registered family's exact {name}",
        )
    actual = cast(dict[object, object], value)
    if set(actual) != set(expected) or any(
        type(actual[key]) is not type(expected[key]) or actual[key] != expected[key] for key in expected
    ):
        raise _invalid(
            f"invalid best-model {name}",
            corrective_action=f"restore the registered family's exact {name}",
        )
    return cast(dict[str, Any], dict(actual))


def _payload_from_runtime(family: ModelFamily, fitted: FittedModel) -> FamilyPayload:
    """Validate one registered runtime model as its canonical fitted wire payload."""
    return _validate_family_payload(family.dump_fitted(fitted))


def runtime_fitted_model(model: BestModel) -> FittedModel:
    """Reconstruct the registered runtime model from one validated fitted wire payload."""
    family = get_family(model.family)
    bounds = _config_from_bound_mapping(family, model.gene_bounds)
    genes = _validate_genes(family, model.genes, _bounds_from_config(family, bounds))
    return family.load_fitted(
        model.fitted.model_dump(mode="json", by_alias=True),
        genes=genes,
        bounds=bounds,
    )


def _validate_best_model(model: BestModel) -> None:
    if type(model.version) is not int or model.version != 1:
        raise _invalid("invalid best-model version", corrective_action="use integer best-model version 1")
    require_current_scientific_schema(model.scientific_artifact_schema, artifact="best model")
    if type(model.family) is not str:
        raise _invalid("invalid best-model family", corrective_action="select one registered model family")
    family = get_family(model.family)
    bounds = _bounds_from_config(family, _config_from_bound_mapping(family, model.gene_bounds))
    genes = _validate_genes(family, model.genes, bounds)
    _validate_identity(model.reference_identity, name="reference")
    _validate_identity(model.capture_identity, name="capture")
    _validate_final_seed(model.final_seed)
    _validate_final_limits(model.final_limits)
    _validate_window(model.observation_window_seconds)
    _exact_mapping(model.estimator_choices, dict(family.estimator_choices), name="estimator choices")
    _exact_mapping(model.seed_policy, _SEED_POLICY, name="seed policy")
    try:
        reloaded = family.load_fitted(
            model.fitted.model_dump(mode="json", by_alias=True),
            genes=genes,
            bounds=_config_from_bound_mapping(family, bounds),
        )
    except (TypeError, ValueError, TrafficlabError) as error:
        raise _invalid(
            f"invalid fitted {family.name} model: {error}",
            corrective_action="fit or load parameters consistent with the outer genes and bounds",
        ) from error
    _payload_from_runtime(family, reloaded)


def _config_from_bound_mapping(family: ModelFamily, bounds: dict[str, FloatBounds | IntegerBounds]) -> FamilyBounds:
    if type(bounds) is not dict or set(bounds) != set(family.gene_names):
        raise _invalid(
            f"invalid {family.name} gene bounds",
            corrective_action="provide one exact bound for every canonical gene",
        )
    if any(
        type(bounds[name]) is not (IntegerBounds if _is_integer_coordinate(family, name) else FloatBounds)
        for name in family.gene_names
    ):
        raise _invalid(
            f"invalid {family.name} gene bounds",
            corrective_action="provide exact FloatBounds values and an exact IntegerBounds Markov r value",
        )
    try:
        if family is POISSON_FAMILY:
            return PoissonConfig(c_lambda=cast(FloatBounds, bounds["c_lambda"]))
        if family is MARKOV_RENEWAL_FAMILY:
            return MarkovRenewalConfig(
                q1=cast(FloatBounds, bounds["q1"]),
                q2=cast(FloatBounds, bounds["q2"]),
                alpha=cast(FloatBounds, bounds["alpha"]),
                r=cast(IntegerBounds, bounds["r"]),
                c_t=cast(FloatBounds, bounds["c_t"]),
            )
        return MmppConfig(
            q01=cast(FloatBounds, bounds["q01"]),
            q10=cast(FloatBounds, bounds["q10"]),
            lambda0=cast(FloatBounds, bounds["lambda0"]),
            lambda1=cast(FloatBounds, bounds["lambda1"]),
        )
    except ValidationError as error:
        raise _invalid(
            f"invalid {family.name} gene bounds: {error}",
            corrective_action="provide finite ordered bounds satisfying the registered family constraints",
        ) from error


def make_best_model(
    family: ModelFamily,
    reference: ReferenceTrace,
    genes: Sequence[Gene],
    *,
    reference_identity: ContentIdentity,
    capture_identity: ContentIdentity,
    final_seed: int,
    final_limits: GenerationLimits,
    W: float,
    bounds: FamilyBounds,
) -> BestModel:
    """Repair, fit, and bind all canonical metadata in one artifact constructor."""
    if REGISTRY.get(family.name) is not family:
        raise _invalid(
            "unknown model family object",
            corrective_action="use a family object returned by get_family",
        )
    reference_identity = _validate_identity(reference_identity, name="reference")
    capture_identity = _validate_identity(capture_identity, name="capture")
    final_seed = _validate_final_seed(final_seed)
    final_limits = _validate_final_limits(final_limits)
    window = _validate_window(W)
    bound_mapping = _bounds_from_config(family, bounds)
    repaired = family.repair(genes, bounds, reference)
    fitted = family.fit(reference, repaired, W=window, bounds=bounds)
    return BestModel(
        version=1,
        scientific_artifact_schema=SCIENTIFIC_ARTIFACT_SCHEMA_VERSION,
        family=family.name,
        genes=repaired,
        fitted=_payload_from_runtime(family, fitted),
        reference_identity=reference_identity,
        capture_identity=capture_identity,
        final_seed=final_seed,
        final_limits=final_limits,
        observation_window_seconds=window,
        gene_bounds=dict(bound_mapping),
        estimator_choices=dict(family.estimator_choices),
        seed_policy=dict(_SEED_POLICY),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> object:
    raise ValueError(f"nonfinite JSON number: {value}")


def load_best_model(content: bytes, *, source: Path) -> BestModel:
    """Decode and revalidate one exact version-1 fitted-model document."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid(
            f"best model {source} is not valid UTF-8: {error}",
            corrective_action="save best_model.json as valid UTF-8",
        ) from error
    try:
        raw = cast(
            object,
            json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite_constant),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise _invalid(
            f"invalid JSON in best model {source}: {error}",
            corrective_action="correct the strict JSON syntax and duplicate keys",
        ) from error
    if type(raw) is not dict:
        raise _invalid(
            "invalid best-model outer object",
            corrective_action="provide exactly the documented version-1 fields",
        )
    document = cast(dict[str, object], raw)
    require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="best model")
    if set(document) != _OUTER_KEYS:
        raise _invalid(
            "invalid best-model outer object",
            corrective_action="provide exactly the documented version-1 fields",
        )
    version = document["version"]
    if type(version) is not int or version != 1:
        raise _invalid("invalid best-model version", corrective_action="use integer best-model version 1")
    family_value = document["family"]
    if type(family_value) is not str:
        raise _invalid("invalid best-model family", corrective_action="select one registered model family")
    family = get_family(family_value)
    bounds, bound_mapping = _build_bounds(family, document["gene_bounds"])
    genes = _parse_genes(family, document["genes"], bound_mapping)
    reference_identity = _parse_identity(document["reference_identity"], name="reference")
    capture_identity = _parse_identity(document["capture_identity"], name="capture")
    final_seed = _validate_final_seed(document["final_seed"])
    final_limits = _parse_final_limits(document["final_limits"])
    window = _validate_window(document["observation_window_seconds"])
    estimator_choices = _exact_mapping(
        document["estimator_choices"], dict(family.estimator_choices), name="estimator choices"
    )
    seed_policy = _exact_mapping(document["seed_policy"], _SEED_POLICY, name="seed policy")
    try:
        fitted = _validate_family_payload(document["fitted"])
        family.load_fitted(fitted.model_dump(mode="json", by_alias=True), genes=genes, bounds=bounds)
    except (TypeError, ValueError, ValidationError, TrafficlabError) as error:
        raise _invalid(
            f"invalid fitted {family.name} model: {error}",
            corrective_action="restore fitted parameters consistent with the outer genes and bounds",
        ) from error
    return BestModel(
        version=1,
        scientific_artifact_schema=cast(int, document["scientific_artifact_schema"]),
        family=_family_name(family_value),
        genes=genes,
        fitted=fitted,
        reference_identity=reference_identity,
        capture_identity=capture_identity,
        final_seed=final_seed,
        final_limits=final_limits,
        observation_window_seconds=window,
        gene_bounds=dict(bound_mapping),
        estimator_choices=cast(dict[str, str | int | float], estimator_choices),
        seed_policy=cast(dict[str, str], seed_policy),
    )


def render_best_model(model: BestModel) -> bytes:
    """Render one revalidated best model as canonical compact UTF-8 JSON."""
    if type(model) is not BestModel:
        raise TypeError("model must be a BestModel")
    _validate_best_model(model)
    dumped = model.model_dump(mode="json")
    document: dict[str, object] = {
        "version": dumped["version"],
        "scientific_artifact_schema": dumped["scientific_artifact_schema"],
        "family": dumped["family"],
        "genes": dumped["genes"],
        "fitted": model.fitted.model_dump(mode="json", by_alias=True),
        "reference_identity": dumped["reference_identity"],
        "capture_identity": dumped["capture_identity"],
        "final_seed": dumped["final_seed"],
        "final_limits": dumped["final_limits"],
        "observation_window_seconds": dumped["observation_window_seconds"],
        "gene_bounds": dumped["gene_bounds"],
        "estimator_choices": dumped["estimator_choices"],
        "seed_policy": dumped["seed_policy"],
    }
    try:
        content = (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid best model for JSON rendering: {error}",
            corrective_action="fit or load a finite canonical best model",
        ) from error
    loaded = load_best_model(content, source=Path("best_model.json"))
    if loaded != model:
        raise _invalid(
            "invalid best model for JSON rendering: canonical round trip changed the model",
            corrective_action="fit or load a finite canonical best model",
        )
    return content
