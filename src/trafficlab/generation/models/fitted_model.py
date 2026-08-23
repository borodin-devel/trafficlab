"""Construction, validation, and codec operations for fitted-model artifacts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import FamilyName, FloatBounds, GenerationLimits, IntegerBounds
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.json import render_json_document
from trafficlab.common.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION, require_current_scientific_schema
from trafficlab.generation.models.common import FamilyBounds, FittedModel, Gene, Genes, ModelFamily, ReferenceTrace
from trafficlab.generation.models.fitted_schema import FamilyPayload, tuple_input, validate_family_payload
from trafficlab.generation.models.registry import (
    MARKOV_RENEWAL_FAMILY,
    MMPP_FAMILY,
    REGISTRY,
    bounds_from_config,
    build_bounds,
    config_from_bound_mapping,
    family_name,
    get_family,
    invalid_best_model,
)

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
    "empirical": "choice_scalar_index",
    "exponential": "exponential_scale_inverse_rate",
    "generator": "numpy.random.Generator/PCG64",
    "weighted": "random_cumulative",
}


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
    scientific_artifact_schema: Literal[4]
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
        return tuple_input(value)

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


def _validate_identity(value: object, *, name: str) -> ContentIdentity:
    if type(value) is not ContentIdentity:
        raise invalid_best_model(
            f"invalid best-model {name} identity",
            corrective_action=f"provide the exact nested size and SHA-256 identity for the {name} artifact",
        )
    return value


def _parse_identity(value: object, *, name: str) -> ContentIdentity:
    try:
        return ContentIdentity.from_dict(value, name=f"best-model {name}")
    except (TypeError, ValueError) as error:
        raise invalid_best_model(
            f"invalid best-model {name} identity: {error}",
            corrective_action=f"restore the exact nested size and SHA-256 identity for the {name} artifact",
        ) from error


def _validate_final_seed(value: object) -> int:
    if type(value) is not int or value < 0:
        raise invalid_best_model(
            "invalid best-model final seed",
            corrective_action="provide one exact nonnegative integer final_seed",
        )
    return value


def invalid_best_model_final_limits() -> TrafficlabError:
    return invalid_best_model(
        "invalid best-model final limits",
        corrective_action=(
            "provide exactly positive integer max_packets and max_output_bytes plus a finite positive float "
            "max_wall_seconds"
        ),
    )


def _validate_final_limits(value: object) -> GenerationLimits:
    if type(value) is not GenerationLimits:
        raise invalid_best_model_final_limits()
    return value


def _parse_final_limits(value: object) -> GenerationLimits:
    if type(value) is not dict:
        raise invalid_best_model_final_limits()
    document = cast(dict[object, object], value)
    if set(document) != {"max_packets", "max_output_bytes", "max_wall_seconds"}:
        raise invalid_best_model_final_limits()
    max_packets = document["max_packets"]
    max_output_bytes = document["max_output_bytes"]
    max_wall_seconds = document["max_wall_seconds"]
    if type(max_packets) is not int or max_packets <= 0:
        raise invalid_best_model_final_limits()
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise invalid_best_model_final_limits()
    if type(max_wall_seconds) is not float or max_wall_seconds <= 0.0:
        raise invalid_best_model_final_limits()
    return GenerationLimits(
        max_packets=max_packets,
        max_output_bytes=max_output_bytes,
        max_wall_seconds=max_wall_seconds,
    )


def _validate_window(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise invalid_best_model(
            "invalid best-model observation window",
            corrective_action="provide a finite positive float observation_window_seconds",
        )
    return value


def _validate_genes(family: ModelFamily, value: object, gene_bounds: dict[str, FloatBounds | IntegerBounds]) -> Genes:
    if type(value) is not tuple:
        raise invalid_best_model(
            f"invalid {family.name} genes",
            corrective_action="provide the exact canonical immutable gene tuple for the registered family",
        )
    raw = cast(tuple[object, ...], value)
    if len(raw) != len(family.gene_names):
        raise invalid_best_model(
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
            raise invalid_best_model(
                f"invalid {family.name} genes",
                corrective_action="use exact finite canonical genes within every named bound",
            )
    if family is MARKOV_RENEWAL_FAMILY and not cast(float, raw[0]) < cast(float, raw[1]):
        raise invalid_best_model(
            "invalid markov_renewal genes",
            corrective_action="persist repaired q1 strictly less than q2",
        )
    if family is MMPP_FAMILY and not cast(float, raw[2]) < cast(float, raw[3]):
        raise invalid_best_model(
            "invalid mmpp genes",
            corrective_action="persist repaired lambda0 strictly less than lambda1",
        )
    return cast(Genes, tuple(raw))


def _parse_genes(family: ModelFamily, value: object, gene_bounds: dict[str, FloatBounds | IntegerBounds]) -> Genes:
    if type(value) is not list:
        raise invalid_best_model(
            f"invalid {family.name} genes",
            corrective_action="provide the exact canonical JSON gene array for the registered family",
        )
    return _validate_genes(family, tuple(cast(list[object], value)), gene_bounds)


def _exact_mapping(value: object, expected: Mapping[str, str | int | float], *, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise invalid_best_model(
            f"invalid best-model {name}",
            corrective_action=f"restore the registered family's exact {name}",
        )
    actual = cast(dict[object, object], value)
    if set(actual) != set(expected) or any(
        type(actual[key]) is not type(expected[key]) or actual[key] != expected[key] for key in expected
    ):
        raise invalid_best_model(
            f"invalid best-model {name}",
            corrective_action=f"restore the registered family's exact {name}",
        )
    return cast(dict[str, Any], dict(actual))


def _payload_from_runtime(family: ModelFamily, fitted: FittedModel) -> FamilyPayload:
    """Validate one registered runtime model as its canonical fitted wire payload."""
    return validate_family_payload(family.dump_fitted(fitted))


def runtime_fitted_model(model: BestModel) -> FittedModel:
    """Reconstruct the registered runtime model from one validated fitted wire payload."""
    family = get_family(model.family)
    bounds = config_from_bound_mapping(family, model.gene_bounds)
    genes = _validate_genes(family, model.genes, bounds_from_config(family, bounds))
    return family.load_fitted(
        model.fitted.model_dump(mode="json", by_alias=True),
        genes=genes,
        bounds=bounds,
    )


def _validate_best_model(model: BestModel) -> None:
    if type(model.version) is not int or model.version != 1:
        raise invalid_best_model("invalid best-model version", corrective_action="use integer best-model version 1")
    require_current_scientific_schema(model.scientific_artifact_schema, artifact="best model")
    if type(model.family) is not str:
        raise invalid_best_model("invalid best-model family", corrective_action="select one registered model family")
    family = get_family(model.family)
    bounds = bounds_from_config(family, config_from_bound_mapping(family, model.gene_bounds))
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
            bounds=config_from_bound_mapping(family, bounds),
        )
    except (TypeError, ValueError, TrafficlabError) as error:
        raise invalid_best_model(
            f"invalid fitted {family.name} model: {error}",
            corrective_action="fit or load parameters consistent with the outer genes and bounds",
        ) from error
    _payload_from_runtime(family, reloaded)


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
        raise invalid_best_model(
            "unknown model family object",
            corrective_action="use a family object returned by get_family",
        )
    reference_identity = _validate_identity(reference_identity, name="reference")
    capture_identity = _validate_identity(capture_identity, name="capture")
    final_seed = _validate_final_seed(final_seed)
    final_limits = _validate_final_limits(final_limits)
    window = _validate_window(W)
    bound_mapping = bounds_from_config(family, bounds)
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
        raise invalid_best_model(
            f"best model {source} is not valid UTF-8: {error}",
            corrective_action="save best_model.json as valid UTF-8",
        ) from error
    try:
        raw = cast(
            object,
            json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite_constant),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise invalid_best_model(
            f"invalid JSON in best model {source}: {error}",
            corrective_action="correct the strict JSON syntax and duplicate keys",
        ) from error
    if type(raw) is not dict:
        raise invalid_best_model(
            "invalid best-model outer object",
            corrective_action="provide exactly the documented version-1 fields",
        )
    document = cast(dict[str, object], raw)
    require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="best model")
    if render_json_document(document) != content:
        raise invalid_best_model(
            "best model is not canonical JSON",
            corrective_action="render best_model.json as sorted two-space-indented JSON with one trailing newline",
        )
    if set(document) != _OUTER_KEYS:
        raise invalid_best_model(
            "invalid best-model outer object",
            corrective_action="provide exactly the documented version-1 fields",
        )
    version = document["version"]
    if type(version) is not int or version != 1:
        raise invalid_best_model("invalid best-model version", corrective_action="use integer best-model version 1")
    family_value = document["family"]
    if type(family_value) is not str:
        raise invalid_best_model("invalid best-model family", corrective_action="select one registered model family")
    family = get_family(family_value)
    bounds, bound_mapping = build_bounds(family, document["gene_bounds"])
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
        fitted = validate_family_payload(document["fitted"])
        family.load_fitted(fitted.model_dump(mode="json", by_alias=True), genes=genes, bounds=bounds)
    except (TypeError, ValueError, ValidationError, TrafficlabError) as error:
        raise invalid_best_model(
            f"invalid fitted {family.name} model: {error}",
            corrective_action="restore fitted parameters consistent with the outer genes and bounds",
        ) from error
    return BestModel(
        version=1,
        scientific_artifact_schema=cast(Literal[4], document["scientific_artifact_schema"]),
        family=family_name(family_value),
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
    """Render one revalidated best model as canonical readable UTF-8 JSON."""
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
        content = render_json_document(document)
    except (TypeError, ValueError) as error:
        raise invalid_best_model(
            f"invalid best model for JSON rendering: {error}",
            corrective_action="fit or load a finite canonical best model",
        ) from error
    loaded = load_best_model(content, source=Path("best_model.json"))
    if loaded != model:
        raise invalid_best_model(
            "invalid best model for JSON rendering: canonical round trip changed the model",
            corrective_action="fit or load a finite canonical best model",
        )
    return content
