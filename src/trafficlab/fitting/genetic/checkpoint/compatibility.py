"""Checkpoint compatibility, strict parsing, and PCG64 state authority."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

import numpy as np
from pydantic import ValidationError

from trafficlab.artifacts.io import atomic_replace as _atomic_replace
from trafficlab.common.compatibility import ContentIdentity, require_compatible
from trafficlab.common.config import FamilyName, FloatBounds, GenerationLimits, IntegerBounds, SimilarityConfig
from trafficlab.common.errors import FailureOutcome, TrafficlabError
from trafficlab.common.scientific_schema import require_current_scientific_schema
from trafficlab.comparison.similarity.common import FrozenJsonValue
from trafficlab.fitting.genetic.checkpoint.schema import (
    CheckpointCompatibility,
    FamilyCheckpointSpec,
    GeneticCheckpointSettings,
    RngState,
)
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.fitting.genetic.population import validate_family_priority
from trafficlab.generation.models.common import make_rng
from trafficlab.generation.models.registry import (
    get_family,
)

RNG_ENGINE: Literal["numpy.random.Generator/PCG64"] = "numpy.random.Generator/PCG64"
_FAMILY_NAMES = frozenset(("markov_renewal", "mmpp", "nhpp", "poisson_empirical"))
_COORDINATE_KINDS = frozenset(("linear", "log", "integer"))
GENETIC_KEYS = (
    "master_seed",
    "final_seed",
    "population_size",
    "generation_count",
    "tournament_size",
    "elite_count",
    "duplicate_mutation_attempts",
    "early_stopping_generations",
    "early_stopping_tolerance",
    "resume",
)


class CheckpointCorruptionError(TrafficlabError):
    """A malformed or internally inconsistent checkpoint whose bytes must be preserved."""


def invalid_checkpoint(detail: str) -> CheckpointCorruptionError:
    return CheckpointCorruptionError(
        f"invalid checkpoint: {detail}",
        corrective_action="preserve the checkpoint and resume from a compatible complete generation",
    )


def validation_error_detail(error: ValidationError) -> str:
    """Return stable Pydantic diagnostics without persisted input values or documentation URLs."""
    details: list[str] = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(component) for component in item["loc"])
        details.append(f"{location}: {item['msg']} [{item['type']}]")
    return "; ".join(details)


def is_rng_engine_identifier(value: object) -> bool:
    """Return whether a named RNG engine uses nonempty ASCII slash-separated segments."""
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.]+(?:/[A-Za-z0-9_.]+)+", value) is not None


def compatibility_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"checkpoint {detail} does not match the effective experiment",
        corrective_action="resume with the exact saved experiment and runtime or start a new run directory",
    )


def atomic_replace(path: Path, content: bytes) -> None:
    """Replace rendered validated bytes after proving the persisted temporary copy is exact."""

    # Validation reads the temporary sibling back from disk before rename.  A
    # successful return therefore means the atomic replacement published the
    # exact rendered bytes, not merely that the preceding write call succeeded.

    def validate(persisted: bytes) -> None:
        if persisted != content:
            raise invalid_checkpoint("persisted temporary artifact differs from the rendered content")

    _atomic_replace(path, content, validator=validate)


def _string(value: object, *, name: str, nonempty: bool = False) -> str:
    if type(value) is not str or (nonempty and not value):
        qualifier = "nonempty string" if nonempty else "string"
        raise ValueError(f"{name} must be a {qualifier}")
    return value


def parse_integer(value: object, *, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be an exact integer in {bounds}")
    return value


def parse_float(value: object, *, name: str, positive: bool = False, bounded: bool = False) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{name} must be an exact finite float")
    if positive and value <= 0.0:
        raise ValueError(f"{name} must be a positive exact finite float")
    if bounded and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be an exact finite float in [0, 1]")
    return value


def parse_family_name(value: object, *, name: str) -> FamilyName:
    result = _string(value, name=name)
    if result not in _FAMILY_NAMES:
        raise ValueError(f"{name} must be a registered family name")
    return result


def thaw_json(value: FrozenJsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [thaw_json(item) for item in value]
    return value


def _duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def load_json_object(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_free_object,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"nonfinite JSON number {token}")),
        )
        if type(value) is not dict:
            raise ValueError("checkpoint root must be an object")
        return cast(dict[str, object], value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise invalid_checkpoint(str(error)) from error


def _validate_coordinate(coordinate: GeneCoordinate, *, family: FamilyName) -> None:
    if type(coordinate) is not GeneCoordinate:
        raise ValueError(f"coordinates for family {family} must contain GeneCoordinate values")
    _string(coordinate.name, name=f"coordinate name for family {family}", nonempty=True)
    if coordinate.kind not in _COORDINATE_KINDS:
        raise ValueError(f"invalid coordinate kind for family {family}")
    if coordinate.kind == "integer":
        if type(coordinate.bounds) is not IntegerBounds:
            raise ValueError(f"integer coordinate bounds for family {family} must be exact integers")
    elif type(coordinate.bounds) is not FloatBounds:
        raise ValueError(f"continuous coordinate bounds for family {family} must be exact floats")
    if coordinate.kind == "log" and coordinate.bounds.lower <= 0:
        raise ValueError(f"log coordinate lower bound for family {family} must be positive")


def _validate_family_spec(spec: FamilyCheckpointSpec) -> None:
    if type(spec) is not FamilyCheckpointSpec:
        raise TypeError("families must contain FamilyCheckpointSpec values")
    family = parse_family_name(spec.name, name="family name")
    if type(spec.gene_order) is not tuple or not spec.gene_order:
        raise ValueError(f"gene order for family {spec.name} must be a nonempty tuple")
    if any(type(name) is not str or not name for name in spec.gene_order):
        raise ValueError(f"gene order for family {spec.name} must contain nonempty strings")
    if len(spec.gene_order) != len(set(spec.gene_order)):
        raise ValueError(f"duplicate gene name for family {spec.name}")
    if spec.gene_order != get_family(family).gene_names:
        raise ValueError(f"gene order for family {spec.name} must equal the exact registered gene order")
    if type(spec.coordinates) is not tuple:
        raise TypeError(f"coordinates for family {spec.name} must be a tuple")
    for coordinate in spec.coordinates:
        _validate_coordinate(coordinate, family=spec.name)
    if tuple(coordinate.name for coordinate in spec.coordinates) != spec.gene_order:
        raise ValueError(f"coordinate order for family {spec.name} must equal gene order")
    parse_float(spec.crossover_probability, name=f"crossover probability for family {spec.name}", bounded=True)
    parse_float(spec.mutation_probability, name=f"mutation probability for family {spec.name}", bounded=True)
    mutation_scale = parse_float(spec.mutation_scale, name=f"mutation scale for family {spec.name}", bounded=True)
    if mutation_scale <= 0.0:
        raise ValueError(f"mutation scale for family {spec.name} must be positive")


def _validate_genetic(settings: GeneticCheckpointSettings, *, family_count: int, trial_seeds: tuple[int, ...]) -> None:
    if type(settings) is not GeneticCheckpointSettings:
        raise TypeError("genetic settings must be GeneticCheckpointSettings")
    parse_integer(settings.master_seed, name="genetic master_seed")
    parse_integer(settings.final_seed, name="genetic final_seed")
    population_size = parse_integer(settings.population_size, name="genetic population_size", minimum=2)
    generation_count = parse_integer(settings.generation_count, name="genetic generation_count")
    tournament_size = parse_integer(settings.tournament_size, name="genetic tournament_size", minimum=2)
    elite_count = parse_integer(settings.elite_count, name="genetic elite_count", minimum=1)
    parse_integer(settings.duplicate_mutation_attempts, name="genetic duplicate_mutation_attempts")
    early_limit = parse_integer(settings.early_stopping_generations, name="genetic early_stopping_generations")
    parse_float(settings.early_stopping_tolerance, name="genetic early_stopping_tolerance", bounded=True)
    if type(settings.resume) is not bool:
        raise ValueError("genetic resume must be a boolean")
    if tournament_size > population_size:
        raise ValueError("genetic tournament_size must not exceed population_size")
    if elite_count >= population_size:
        raise ValueError("genetic elite_count must be less than population_size")
    if population_size < elite_count + family_count:
        raise ValueError("genetic population_size must include elites and every family")
    if early_limit > generation_count:
        raise ValueError("genetic early_stopping_generations must not exceed generation_count")
    if settings.final_seed in trial_seeds:
        raise ValueError("genetic final_seed must not be a selection trial seed")


def validate_compatibility_shape(value: CheckpointCompatibility, *, require_current_rng_engine: bool = True) -> None:
    if type(value) is not CheckpointCompatibility:
        raise TypeError("compatibility must be CheckpointCompatibility")
    require_current_scientific_schema(value.scientific_artifact_schema, artifact="checkpoint")
    for name, identity in (
        ("experiment", value.experiment_identity),
        ("reference", value.reference_identity),
        ("capture", value.capture_identity),
    ):
        if type(identity) is not ContentIdentity:
            raise TypeError(f"{name}_identity must be a ContentIdentity")
    parse_float(value.observation_window_seconds, name="observation_window_seconds", positive=True)
    if type(value.trial_seeds) is not tuple or not value.trial_seeds:
        raise ValueError("trial_seeds must be a nonempty tuple")
    for seed in value.trial_seeds:
        parse_integer(seed, name="trial seed")
    if len(value.trial_seeds) != len(set(value.trial_seeds)):
        raise ValueError("trial_seeds must be unique")
    if type(value.trial_limits) is not GenerationLimits:
        raise TypeError("trial_limits must be GenerationLimits")
    if type(value.families) is not tuple or not value.families:
        raise ValueError("families must be a nonempty tuple")
    for family in value.families:
        _validate_family_spec(family)
    family_names = tuple(family.name for family in value.families)
    if family_names != tuple(sorted(family_names)):
        raise ValueError("families must be in lexical order")
    if len(family_names) != len(set(family_names)):
        raise ValueError("families contain a duplicate family name")
    validate_family_priority(value.family_priority, enabled_families=family_names)
    _validate_genetic(value.genetic, family_count=len(value.families), trial_seeds=value.trial_seeds)
    if type(value.similarity) is not SimilarityConfig:
        raise TypeError("similarity must be SimilarityConfig")
    _string(value.python_version, name="python_version", nonempty=True)
    _string(value.rng_engine, name="rng_engine", nonempty=True)
    if require_current_rng_engine and value.rng_engine != RNG_ENGINE:
        raise ValueError(f"rng_engine must be {RNG_ENGINE}")


def validate_compatibility(stored: CheckpointCompatibility, expected: CheckpointCompatibility) -> None:
    """Reject the first compatibility difference in the architecture-defined order."""
    try:
        validate_compatibility_shape(stored, require_current_rng_engine=False)
        validate_compatibility_shape(expected)
    except (TypeError, ValueError) as error:
        raise invalid_checkpoint(str(error)) from error
    ordered_expected: dict[str, object] = {
        "experiment snapshot SHA-256/size identity": expected.experiment_identity,
        "reference SHA-256/size identity": expected.reference_identity,
        "capture SHA-256/size identity": expected.capture_identity,
        "observation window": expected.observation_window_seconds,
        "trial seeds": expected.trial_seeds,
        "trial generation limits": expected.trial_limits,
    }
    ordered_stored: dict[str, object] = {
        "experiment snapshot SHA-256/size identity": stored.experiment_identity,
        "reference SHA-256/size identity": stored.reference_identity,
        "capture SHA-256/size identity": stored.capture_identity,
        "observation window": stored.observation_window_seconds,
        "trial seeds": stored.trial_seeds,
        "trial generation limits": stored.trial_limits,
    }
    try:
        require_compatible(ordered_expected, ordered_stored)
    except TrafficlabError as error:
        if stored.reference_identity != expected.reference_identity:
            raise TrafficlabError(
                f"checkpoint is incompatible: reference SHA-256/size identity differs: {error}",
                corrective_action="recreate the capture pair in a new matching run",
                failure_outcome=FailureOutcome(
                    kind="artifact_changed",
                    stage="fit",
                    detail="reference.pcapng changed during fit resume",
                    affected_evidence="reference.pcapng",
                    evidence_state="preserved",
                    corrective_action="recreate the capture pair in a new matching run",
                    authority="primary",
                ),
            ) from error
        raise compatibility_error(str(error)) from error
    stored_names = tuple(family.name for family in stored.families)
    expected_names = tuple(family.name for family in expected.families)
    if stored_names != expected_names:
        raise compatibility_error("lexical family names")
    if stored.family_priority != expected.family_priority:
        raise compatibility_error("family priority")
    for stored_family, expected_family in zip(stored.families, expected.families, strict=True):
        name = stored_family.name
        if stored_family.gene_order != expected_family.gene_order:
            raise compatibility_error(f"gene order for family {name}")
        if stored_family.coordinates != expected_family.coordinates:
            raise compatibility_error(f"coordinate metadata for family {name}")
        stored_operators = (
            stored_family.crossover_probability,
            stored_family.mutation_probability,
            stored_family.mutation_scale,
        )
        expected_operators = (
            expected_family.crossover_probability,
            expected_family.mutation_probability,
            expected_family.mutation_scale,
        )
        if stored_operators != expected_operators:
            raise compatibility_error(f"operator values for family {name}")
    for field_name in GENETIC_KEYS:
        if getattr(stored.genetic, field_name) != getattr(expected.genetic, field_name):
            raise compatibility_error(f"genetic setting {field_name}")
    if stored.similarity != expected.similarity:
        raise compatibility_error("similarity settings and weights")
    if stored.python_version != expected.python_version:
        raise compatibility_error("Python version")
    if stored.rng_engine != expected.rng_engine:
        raise compatibility_error("RNG engine")


def validate_rng_state(value: RngState) -> None:
    if type(value) is not RngState:
        raise TypeError("rng state must be RngState")
    RngState.model_validate(value.model_dump(mode="python"))


def encode_rng_state(rng: object) -> RngState:
    """Validate and detach the exact JSON-compatible state of one PCG64 generator."""
    try:
        if type(rng) is not np.random.Generator or type(rng.bit_generator) is not np.random.PCG64:
            raise ValueError("RNG must be numpy.random.Generator with PCG64")
        result = RngState.model_validate(rng.bit_generator.state)
        validate_rng_state(result)
        return result
    except (TypeError, ValueError, ValidationError) as error:
        if isinstance(error, ValidationError):
            raise invalid_checkpoint(validation_error_detail(error)) from error
        raise invalid_checkpoint(str(error)) from error


def decode_rng_state(state: RngState) -> np.random.Generator:
    """Restore one explicit PCG64 generator from its exact validated state."""
    try:
        validate_rng_state(state)
        validated = RngState.model_validate(state.model_dump(mode="python"))
        rng = make_rng(0)
        rng.bit_generator.state = validated.model_dump(mode="python")
        return rng
    except ValidationError as error:
        raise invalid_checkpoint(validation_error_detail(error)) from error
    except (TypeError, ValueError) as error:
        raise invalid_checkpoint(str(error)) from error
