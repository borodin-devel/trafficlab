"""Closed built-in traffic-model registry and strict fitted-model artifact codec."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import ValidationError

from trafficlab.config import FamilyName, FloatBounds, IntegerBounds, MarkovRenewalConfig, MmppConfig, PoissonConfig
from trafficlab.errors import TrafficlabError
from trafficlab.models.common import FamilyBounds, FittedModel, Gene, Genes, ModelFamily
from trafficlab.models.markov_renewal import MarkovRenewalFamily
from trafficlab.models.mmpp import MmppFamily
from trafficlab.models.poisson import PoissonFamily
from trafficlab.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION, require_current_scientific_schema
from trafficlab.trace import TraceEvent

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_OUTER_KEYS = {
    "version",
    "scientific_artifact_schema",
    "family",
    "genes",
    "fitted",
    "reference_sha256",
    "capture_sha256",
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


@dataclass(frozen=True, slots=True)
class BestModel:
    """One fully self-contained, lineage-bound fitted traffic model."""

    version: Literal[1]
    scientific_artifact_schema: int
    family: FamilyName
    genes: Genes
    fitted: FittedModel
    reference_sha256: str
    capture_sha256: str
    observation_window_seconds: float
    gene_bounds: dict[str, FloatBounds | IntegerBounds]
    estimator_choices: dict[str, str | int | float]
    seed_policy: dict[str, str]

    def __post_init__(self) -> None:
        _validate_best_model(self)


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_hash(value: object, *, name: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise _invalid(
            f"invalid {name} SHA-256 identity",
            corrective_action="provide exactly 64 lowercase hexadecimal SHA-256 characters",
        )
    return value


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


def _validate_best_model(model: BestModel) -> None:
    if type(model.version) is not int or model.version != 1:
        raise _invalid("invalid best-model version", corrective_action="use integer best-model version 1")
    require_current_scientific_schema(model.scientific_artifact_schema, artifact="best model")
    if type(model.family) is not str:
        raise _invalid("invalid best-model family", corrective_action="select one registered model family")
    family = get_family(model.family)
    bounds = _bounds_from_config(family, _config_from_bound_mapping(family, model.gene_bounds))
    genes = _validate_genes(family, model.genes, bounds)
    _validate_hash(model.reference_sha256, name="reference")
    _validate_hash(model.capture_sha256, name="capture")
    _validate_window(model.observation_window_seconds)
    _exact_mapping(model.estimator_choices, dict(family.estimator_choices), name="estimator choices")
    _exact_mapping(model.seed_policy, _SEED_POLICY, name="seed policy")
    try:
        payload = family.dump_fitted(model.fitted)
        reloaded = family.load_fitted(payload, genes=genes, bounds=_config_from_bound_mapping(family, bounds))
    except (TypeError, ValueError, TrafficlabError) as error:
        raise _invalid(
            f"invalid fitted {family.name} model: {error}",
            corrective_action="fit or load parameters consistent with the outer genes and bounds",
        ) from error
    if reloaded != model.fitted:
        raise _invalid(
            f"invalid fitted {family.name} model",
            corrective_action="fit or load parameters consistent with the outer genes and bounds",
        )


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
    reference: Sequence[TraceEvent],
    genes: Sequence[Gene],
    *,
    reference_sha256: str,
    capture_sha256: str,
    W: float,
    bounds: FamilyBounds,
) -> BestModel:
    """Repair, fit, and bind all canonical metadata in one artifact constructor."""
    if REGISTRY.get(family.name) is not family:
        raise _invalid(
            "unknown model family object",
            corrective_action="use a family object returned by get_family",
        )
    _validate_hash(reference_sha256, name="reference")
    _validate_hash(capture_sha256, name="capture")
    window = _validate_window(W)
    bound_mapping = _bounds_from_config(family, bounds)
    repaired = family.repair(genes, bounds, reference)
    fitted = family.fit(reference, repaired, W=window, bounds=bounds)
    return BestModel(
        version=1,
        scientific_artifact_schema=SCIENTIFIC_ARTIFACT_SCHEMA_VERSION,
        family=family.name,
        genes=repaired,
        fitted=fitted,
        reference_sha256=reference_sha256,
        capture_sha256=capture_sha256,
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
            corrective_action="provide exactly the eleven documented version-1 fields",
        )
    document = cast(dict[str, object], raw)
    require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="best model")
    if set(document) != _OUTER_KEYS:
        raise _invalid(
            "invalid best-model outer object",
            corrective_action="provide exactly the eleven documented version-1 fields",
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
    reference_sha256 = _validate_hash(document["reference_sha256"], name="reference")
    capture_sha256 = _validate_hash(document["capture_sha256"], name="capture")
    window = _validate_window(document["observation_window_seconds"])
    estimator_choices = _exact_mapping(
        document["estimator_choices"], dict(family.estimator_choices), name="estimator choices"
    )
    seed_policy = _exact_mapping(document["seed_policy"], _SEED_POLICY, name="seed policy")
    try:
        fitted = family.load_fitted(document["fitted"], genes=genes, bounds=bounds)
    except (TypeError, ValueError, TrafficlabError) as error:
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
        reference_sha256=reference_sha256,
        capture_sha256=capture_sha256,
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
    family = get_family(model.family)
    document: dict[str, object] = {
        "version": model.version,
        "scientific_artifact_schema": model.scientific_artifact_schema,
        "family": model.family,
        "genes": list(model.genes),
        "fitted": family.dump_fitted(model.fitted),
        "reference_sha256": model.reference_sha256,
        "capture_sha256": model.capture_sha256,
        "observation_window_seconds": model.observation_window_seconds,
        "gene_bounds": {
            name: {"lower": bound.lower, "upper": bound.upper} for name, bound in model.gene_bounds.items()
        },
        "estimator_choices": dict(model.estimator_choices),
        "seed_policy": dict(model.seed_policy),
    }
    try:
        return (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid best model for JSON rendering: {error}",
            corrective_action="fit or load a finite canonical best model",
        ) from error
