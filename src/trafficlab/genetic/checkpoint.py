"""Strict, deterministic checkpoint and derived-history persistence for genetic fitting."""

from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from random import Random
from typing import Literal, cast

from pydantic import ValidationError

from trafficlab.artifacts import atomic_replace as _atomic_replace
from trafficlab.config import FamilyName, FloatBounds, IntegerBounds, SimilarityConfig
from trafficlab.errors import EvidenceState, FailureAuthority, TrafficlabError
from trafficlab.genetic.coordinates import GeneCoordinate
from trafficlab.genetic.types import (
    METHOD_ORDER,
    Candidate,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
    HistoryRow,
    MethodName,
    MethodTrialResult,
    TerminalReason,
    TrialResult,
    validate_model_diagnostics_for_family,
)
from trafficlab.models.common import ModelDiagnostics, freeze_model_diagnostics
from trafficlab.models.registry import get_family
from trafficlab.scientific_schema import require_current_scientific_schema
from trafficlab.similarity.common import FrozenJsonValue

RNG_ENGINE: Literal["python.random.Random/MT19937"] = "python.random.Random/MT19937"
_RNG_STATE_VERSION = Random().getstate()[0]
_FAMILY_NAMES = frozenset(("markov_renewal", "mmpp", "poisson_empirical"))
_COORDINATE_KINDS = frozenset(("linear", "log", "integer"))
_FAILURE_KINDS = frozenset(
    ("repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score")
)
_FAILURE_KEYS = (
    "kind",
    "seed",
    "detail",
    "stage",
    "affected_evidence",
    "evidence_state",
    "corrective_action",
    "authority",
)
_LEGACY_FAILURE_KEYS = ("kind", "seed", "detail")
_LEGACY_FAILURE_PROVENANCE: dict[str, tuple[str, str, EvidenceState, str, FailureAuthority]] = {
    "repair": ("fit", "candidate genes", "diagnostic_only", "repair the candidate genes", "primary"),
    "fit": ("fit", "candidate model", "diagnostic_only", "repair the candidate model", "primary"),
    "generation": (
        "fit",
        "candidate trace",
        "diagnostic_only",
        "repair the candidate model or generation settings",
        "primary",
    ),
    "incomplete_generation": (
        "fit",
        "candidate trace",
        "diagnostic_only",
        "increase generation limits or repair the candidate model",
        "primary",
    ),
    "similarity_precondition": (
        "fit",
        "candidate similarity",
        "diagnostic_only",
        "repair the candidate model to generate sufficient comparable events",
        "primary",
    ),
    "nonfinite_score": (
        "fit",
        "candidate similarity",
        "diagnostic_only",
        "repair the candidate model or similarity computation",
        "primary",
    ),
}
_DUPLICATE_OUTCOMES = frozenset(("invalid", "duplicate", "exhausted"))
_TERMINAL_REASONS = frozenset(("running", "hard_limit", "early_stop"))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_KEYS = (
    "scientific_artifact_schema",
    "experiment_sha256",
    "reference_sha256",
    "capture_sha256",
    "observation_window_seconds",
    "trial_seeds",
    "families",
    "genetic",
    "similarity",
    "rng",
    "generation",
    "population",
    "history",
    "best",
    "consecutive_stagnation",
    "terminal_reason",
)
_GENETIC_KEYS = (
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
_SIMILARITY_KEYS = (
    "iat_diagnostic_quantile",
    "acf_lags",
    "acf_lag_weights",
    "acf_iat_weight",
    "acf_size_weight",
    "multiscale_widths_seconds",
    "multiscale_scale_weights",
    "multiscale_packet_weight",
    "multiscale_byte_weight",
    "max_direction_bin_cells",
    "method_weights",
)
_METHOD_WEIGHT_KEYS = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
_HISTORY_HEADER = (
    "generation",
    "scope",
    "family",
    "candidate_count",
    "valid_count",
    "best_fitness",
    "mean_fitness",
    "best_birth_generation",
    "best_birth_index",
)


@dataclass(frozen=True, slots=True)
class FamilyCheckpointSpec:
    """Resolved chromosome and operator metadata for one enabled family."""

    name: FamilyName
    gene_order: tuple[str, ...]
    coordinates: tuple[GeneCoordinate, ...]
    crossover_probability: float
    mutation_probability: float
    mutation_scale: float


@dataclass(frozen=True, slots=True)
class GeneticCheckpointSettings:
    """All genetic settings that can alter selection, reproduction, or termination."""

    master_seed: int
    final_seed: int
    population_size: int
    generation_count: int
    tournament_size: int
    elite_count: int
    duplicate_mutation_attempts: int
    early_stopping_generations: int
    early_stopping_tolerance: float
    resume: bool


@dataclass(frozen=True, slots=True)
class RngState:
    """Lossless JSON decomposition of the dedicated CPython MT19937 state."""

    state_version: int
    mt_state: tuple[int, ...]
    index: int
    gauss_next: None


@dataclass(frozen=True, slots=True)
class CheckpointCompatibility:
    """Exact inputs and effective settings that must match before resume."""

    scientific_artifact_schema: int
    experiment_sha256: str
    reference_sha256: str
    capture_sha256: str
    observation_window_seconds: float
    trial_seeds: tuple[int, ...]
    families: tuple[FamilyCheckpointSpec, ...]
    genetic: GeneticCheckpointSettings
    similarity: SimilarityConfig
    python_version: str
    rng_engine: Literal["python.random.Random/MT19937"]


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """One complete evaluated generation and every value needed for exact continuation."""

    compatibility: CheckpointCompatibility
    generation: int
    population: tuple[Candidate, ...]
    history: tuple[HistoryRow, ...]
    rng_state: RngState
    best_identifier: CandidateId
    best_fitness: float
    consecutive_stagnation: int
    terminal_reason: TerminalReason


class CheckpointCorruptionError(TrafficlabError):
    """A malformed or internally inconsistent checkpoint whose bytes must be preserved."""


def _invalid(detail: str) -> CheckpointCorruptionError:
    return CheckpointCorruptionError(
        f"invalid checkpoint: {detail}",
        corrective_action="preserve the checkpoint and resume from a compatible complete generation",
    )


def _compatibility_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"checkpoint {detail} does not match the effective experiment",
        corrective_action="resume with the exact saved experiment and runtime or start a new run directory",
    )


def atomic_replace(path: Path, content: bytes) -> None:
    """Replace rendered validated bytes after proving the persisted temporary copy is exact."""

    def validate(persisted: bytes) -> None:
        if persisted != content:
            raise _invalid("persisted temporary artifact differs from the rendered content")

    _atomic_replace(path, content, validator=validate)


def _exact_object(value: object, keys: Sequence[str], *, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    document = cast(dict[object, object], value)
    if any(type(key) is not str for key in document):
        raise ValueError(f"{name} keys must be strings")
    result = cast(dict[str, object], document)
    expected = set(keys)
    if set(result) != expected:
        missing = sorted(expected - set(result))
        unknown = sorted(set(result) - expected)
        detail: list[str] = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if unknown:
            detail.append(f"unknown {', '.join(unknown)}")
        raise ValueError(f"{name} has {' and '.join(detail)}")
    return result


def _array(value: object, *, name: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"{name} must be an array")
    return cast(list[object], value)


def _string(value: object, *, name: str, nonempty: bool = False) -> str:
    if type(value) is not str or (nonempty and not value):
        qualifier = "nonempty string" if nonempty else "string"
        raise ValueError(f"{name} must be a {qualifier}")
    return value


def _integer(value: object, *, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be an exact integer in {bounds}")
    return value


def _float(value: object, *, name: str, positive: bool = False, bounded: bool = False) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{name} must be an exact finite float")
    if positive and value <= 0.0:
        raise ValueError(f"{name} must be a positive exact finite float")
    if bounded and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be an exact finite float in [0, 1]")
    return value


def _boolean(value: object, *, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _sha256(value: object, *, name: str) -> str:
    result = _string(value, name=name)
    if _SHA256.fullmatch(result) is None:
        raise ValueError(f"{name} must be a 64-character lowercase hexadecimal SHA-256")
    return result


def _family_name(value: object, *, name: str) -> FamilyName:
    result = _string(value, name=name)
    if result not in _FAMILY_NAMES:
        raise ValueError(f"{name} must be a registered family name")
    return result


def _frozen_json(value: object, *, name: str) -> FrozenJsonValue:
    if value is None or type(value) in (str, bool, int):
        return cast(FrozenJsonValue, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} contains a nonfinite float")
        return value
    if type(value) is list:
        return tuple(_frozen_json(item, name=name) for item in cast(list[object], value))
    if type(value) is dict:
        document = cast(dict[object, object], value)
        if any(type(key) is not str for key in document):
            raise ValueError(f"{name} object keys must be strings")
        return {cast(str, key): _frozen_json(item, name=name) for key, item in document.items()}
    raise ValueError(f"{name} contains a non-JSON value")


def _thaw_json(value: FrozenJsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return value


def _duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def _load_json(content: bytes) -> dict[str, object]:
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
        raise _invalid(str(error)) from error


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
    family = _family_name(spec.name, name="family name")
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
    _float(spec.crossover_probability, name=f"crossover probability for family {spec.name}", bounded=True)
    _float(spec.mutation_probability, name=f"mutation probability for family {spec.name}", bounded=True)
    mutation_scale = _float(spec.mutation_scale, name=f"mutation scale for family {spec.name}", bounded=True)
    if mutation_scale <= 0.0:
        raise ValueError(f"mutation scale for family {spec.name} must be positive")


def _validate_genetic(settings: GeneticCheckpointSettings, *, family_count: int, trial_seeds: tuple[int, ...]) -> None:
    if type(settings) is not GeneticCheckpointSettings:
        raise TypeError("genetic settings must be GeneticCheckpointSettings")
    _integer(settings.master_seed, name="genetic master_seed")
    _integer(settings.final_seed, name="genetic final_seed")
    population_size = _integer(settings.population_size, name="genetic population_size", minimum=2)
    generation_count = _integer(settings.generation_count, name="genetic generation_count")
    tournament_size = _integer(settings.tournament_size, name="genetic tournament_size", minimum=2)
    elite_count = _integer(settings.elite_count, name="genetic elite_count", minimum=1)
    _integer(settings.duplicate_mutation_attempts, name="genetic duplicate_mutation_attempts")
    early_limit = _integer(settings.early_stopping_generations, name="genetic early_stopping_generations")
    _float(settings.early_stopping_tolerance, name="genetic early_stopping_tolerance", bounded=True)
    _boolean(settings.resume, name="genetic resume")
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


def _validate_compatibility_shape(value: CheckpointCompatibility, *, require_current_rng_engine: bool = True) -> None:
    if type(value) is not CheckpointCompatibility:
        raise TypeError("compatibility must be CheckpointCompatibility")
    require_current_scientific_schema(value.scientific_artifact_schema, artifact="checkpoint")
    _sha256(value.experiment_sha256, name="experiment_sha256")
    _sha256(value.reference_sha256, name="reference_sha256")
    _sha256(value.capture_sha256, name="capture_sha256")
    _float(value.observation_window_seconds, name="observation_window_seconds", positive=True)
    if type(value.trial_seeds) is not tuple or not value.trial_seeds:
        raise ValueError("trial_seeds must be a nonempty tuple")
    for seed in value.trial_seeds:
        _integer(seed, name="trial seed")
    if len(value.trial_seeds) != len(set(value.trial_seeds)):
        raise ValueError("trial_seeds must be unique")
    if type(value.families) is not tuple or not value.families:
        raise ValueError("families must be a nonempty tuple")
    for family in value.families:
        _validate_family_spec(family)
    family_names = tuple(family.name for family in value.families)
    if family_names != tuple(sorted(family_names)):
        raise ValueError("families must be in lexical order")
    if len(family_names) != len(set(family_names)):
        raise ValueError("families contain a duplicate family name")
    _validate_genetic(value.genetic, family_count=len(value.families), trial_seeds=value.trial_seeds)
    if type(value.similarity) is not SimilarityConfig:
        raise TypeError("similarity must be SimilarityConfig")
    _string(value.python_version, name="python_version", nonempty=True)
    _string(value.rng_engine, name="rng_engine", nonempty=True)
    if require_current_rng_engine and value.rng_engine != RNG_ENGINE:
        raise ValueError(f"rng_engine must be {RNG_ENGINE}")


def _parse_coordinate(value: object, *, family: FamilyName) -> GeneCoordinate:
    document = _exact_object(value, ("name", "kind", "lower", "upper"), name=f"coordinate for family {family}")
    name = _string(document["name"], name=f"coordinate name for family {family}", nonempty=True)
    kind_value = _string(document["kind"], name=f"coordinate kind for family {family}")
    if kind_value not in _COORDINATE_KINDS:
        raise ValueError(f"invalid coordinate kind for family {family}")
    kind = kind_value
    if kind == "integer":
        bounds: FloatBounds | IntegerBounds = IntegerBounds(
            lower=_integer(document["lower"], name=f"coordinate lower for family {family}", minimum=-(2**63)),
            upper=_integer(document["upper"], name=f"coordinate upper for family {family}", minimum=-(2**63)),
        )
    else:
        bounds = FloatBounds(
            lower=_float(document["lower"], name=f"coordinate lower for family {family}"),
            upper=_float(document["upper"], name=f"coordinate upper for family {family}"),
        )
    coordinate = GeneCoordinate(name, kind, bounds)
    _validate_coordinate(coordinate, family=family)
    return coordinate


def _parse_family(value: object) -> FamilyCheckpointSpec:
    document = _exact_object(value, ("name", "gene_order", "coordinates", "operators"), name="family metadata")
    family = _family_name(document["name"], name="family name")
    gene_order = tuple(
        _string(item, name=f"gene order for family {family}", nonempty=True)
        for item in _array(document["gene_order"], name=f"gene order for family {family}")
    )
    coordinates = tuple(
        _parse_coordinate(item, family=family)
        for item in _array(document["coordinates"], name=f"coordinates for family {family}")
    )
    operators = _exact_object(
        document["operators"],
        ("crossover_probability", "mutation_probability", "mutation_scale"),
        name=f"operators for family {family}",
    )
    spec = FamilyCheckpointSpec(
        family,
        gene_order,
        coordinates,
        _float(operators["crossover_probability"], name=f"crossover probability for family {family}", bounded=True),
        _float(operators["mutation_probability"], name=f"mutation probability for family {family}", bounded=True),
        _float(operators["mutation_scale"], name=f"mutation scale for family {family}", bounded=True),
    )
    _validate_family_spec(spec)
    return spec


def _parse_genetic(value: object) -> GeneticCheckpointSettings:
    document = _exact_object(value, _GENETIC_KEYS, name="genetic settings")
    return GeneticCheckpointSettings(
        master_seed=_integer(document["master_seed"], name="genetic master_seed"),
        final_seed=_integer(document["final_seed"], name="genetic final_seed"),
        population_size=_integer(document["population_size"], name="genetic population_size", minimum=2),
        generation_count=_integer(document["generation_count"], name="genetic generation_count"),
        tournament_size=_integer(document["tournament_size"], name="genetic tournament_size", minimum=2),
        elite_count=_integer(document["elite_count"], name="genetic elite_count", minimum=1),
        duplicate_mutation_attempts=_integer(
            document["duplicate_mutation_attempts"], name="genetic duplicate_mutation_attempts"
        ),
        early_stopping_generations=_integer(
            document["early_stopping_generations"], name="genetic early_stopping_generations"
        ),
        early_stopping_tolerance=_float(
            document["early_stopping_tolerance"], name="genetic early_stopping_tolerance", bounded=True
        ),
        resume=_boolean(document["resume"], name="genetic resume"),
    )


def _float_array(value: object, *, name: str) -> list[float]:
    return [_float(item, name=f"{name} item") for item in _array(value, name=name)]


def _integer_array(value: object, *, name: str) -> list[int]:
    return [_integer(item, name=f"{name} item") for item in _array(value, name=name)]


def _parse_similarity(value: object) -> SimilarityConfig:
    document = _exact_object(value, _SIMILARITY_KEYS, name="similarity settings")
    weights = _exact_object(document["method_weights"], _METHOD_WEIGHT_KEYS, name="similarity method_weights")
    strict_data = {
        "iat_diagnostic_quantile": _float(
            document["iat_diagnostic_quantile"], name="similarity iat_diagnostic_quantile"
        ),
        "acf_lags": _integer_array(document["acf_lags"], name="similarity acf_lags"),
        "acf_lag_weights": _float_array(document["acf_lag_weights"], name="similarity acf_lag_weights"),
        "acf_iat_weight": _float(document["acf_iat_weight"], name="similarity acf_iat_weight"),
        "acf_size_weight": _float(document["acf_size_weight"], name="similarity acf_size_weight"),
        "multiscale_widths_seconds": _float_array(
            document["multiscale_widths_seconds"], name="similarity multiscale_widths_seconds"
        ),
        "multiscale_scale_weights": _float_array(
            document["multiscale_scale_weights"], name="similarity multiscale_scale_weights"
        ),
        "multiscale_packet_weight": _float(
            document["multiscale_packet_weight"], name="similarity multiscale_packet_weight"
        ),
        "multiscale_byte_weight": _float(document["multiscale_byte_weight"], name="similarity multiscale_byte_weight"),
        "max_direction_bin_cells": _integer(
            document["max_direction_bin_cells"], name="similarity max_direction_bin_cells", minimum=2
        ),
        "method_weights": {
            name: _float(weights[name], name=f"similarity method weight {name}", bounded=True)
            for name in _METHOD_WEIGHT_KEYS
        },
    }
    try:
        return SimilarityConfig.model_validate(strict_data)
    except ValidationError as error:
        raise ValueError(f"invalid similarity settings: {error}") from error


def _parse_compatibility(document: dict[str, object]) -> CheckpointCompatibility:
    require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="checkpoint")
    compatibility = CheckpointCompatibility(
        scientific_artifact_schema=cast(int, document["scientific_artifact_schema"]),
        experiment_sha256=_sha256(document["experiment_sha256"], name="experiment_sha256"),
        reference_sha256=_sha256(document["reference_sha256"], name="reference_sha256"),
        capture_sha256=_sha256(document["capture_sha256"], name="capture_sha256"),
        observation_window_seconds=_float(
            document["observation_window_seconds"], name="observation_window_seconds", positive=True
        ),
        trial_seeds=tuple(_integer_array(document["trial_seeds"], name="trial_seeds")),
        families=tuple(_parse_family(item) for item in _array(document["families"], name="families")),
        genetic=_parse_genetic(document["genetic"]),
        similarity=_parse_similarity(document["similarity"]),
        python_version=_string(
            _exact_object(document["rng"], ("engine", "python_version", "state"), name="rng")["python_version"],
            name="rng python_version",
            nonempty=True,
        ),
        rng_engine=cast(
            Literal["python.random.Random/MT19937"],
            _string(cast(dict[str, object], document["rng"])["engine"], name="rng engine"),
        ),
    )
    _validate_compatibility_shape(compatibility, require_current_rng_engine=False)
    return compatibility


def validate_compatibility(stored: CheckpointCompatibility, expected: CheckpointCompatibility) -> None:
    """Reject the first compatibility difference in the architecture-defined order."""
    try:
        _validate_compatibility_shape(stored, require_current_rng_engine=False)
        _validate_compatibility_shape(expected)
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error
    if stored.experiment_sha256 != expected.experiment_sha256:
        raise _compatibility_error("experiment snapshot SHA-256")
    for field_name, label in (
        ("reference_sha256", "reference SHA-256"),
        ("capture_sha256", "capture SHA-256"),
        ("observation_window_seconds", "observation window"),
        ("trial_seeds", "trial seeds"),
    ):
        if getattr(stored, field_name) != getattr(expected, field_name):
            raise _compatibility_error(label)
    stored_names = tuple(family.name for family in stored.families)
    expected_names = tuple(family.name for family in expected.families)
    if stored_names != expected_names:
        raise _compatibility_error("lexical family names")
    for stored_family, expected_family in zip(stored.families, expected.families, strict=True):
        name = stored_family.name
        if stored_family.gene_order != expected_family.gene_order:
            raise _compatibility_error(f"gene order for family {name}")
        if stored_family.coordinates != expected_family.coordinates:
            raise _compatibility_error(f"coordinate metadata for family {name}")
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
            raise _compatibility_error(f"operator values for family {name}")
    for field_name in _GENETIC_KEYS:
        if getattr(stored.genetic, field_name) != getattr(expected.genetic, field_name):
            raise _compatibility_error(f"genetic setting {field_name}")
    if stored.similarity != expected.similarity:
        raise _compatibility_error("similarity settings and weights")
    if stored.python_version != expected.python_version:
        raise _compatibility_error("Python version")
    if stored.rng_engine != expected.rng_engine:
        raise _compatibility_error("RNG engine")


def _validate_rng_state(value: RngState) -> None:
    if type(value) is not RngState:
        raise TypeError("rng state must be RngState")
    state_version = _integer(value.state_version, name="rng state_version")
    if state_version != _RNG_STATE_VERSION:
        raise ValueError(f"rng state_version must equal the current Python value {_RNG_STATE_VERSION}")
    if type(value.mt_state) is not tuple or len(value.mt_state) != 624:
        raise ValueError("rng mt_state must contain exactly 624 words")
    for word in value.mt_state:
        _integer(word, name="rng MT word", maximum=2**32 - 1)
    _integer(value.index, name="rng index", maximum=624)
    if value.gauss_next is not None:
        raise ValueError("rng gauss_next must be null")


def encode_rng_state(state: object) -> RngState:
    """Losslessly decompose a `Random.getstate()` tuple into strict immutable values."""
    try:
        if type(state) is not tuple:
            raise ValueError("RNG state must be a three-item tuple")
        state_values = cast(tuple[object, ...], state)
        if len(state_values) != 3:
            raise ValueError("RNG state must be a three-item tuple")
        version, internal, gauss_next = state_values
        if type(internal) is not tuple:
            raise ValueError("RNG internal state must contain 624 words and one index")
        internal_values = cast(tuple[object, ...], internal)
        if len(internal_values) != 625:
            raise ValueError("RNG internal state must contain 624 words and one index")
        result = RngState(
            _integer(version, name="rng state_version"),
            tuple(_integer(word, name="rng MT word", maximum=2**32 - 1) for word in internal_values[:-1]),
            _integer(internal_values[-1], name="rng index", maximum=624),
            cast(None, gauss_next),
        )
        _validate_rng_state(result)
        return result
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error


def decode_rng_state(state: RngState) -> tuple[int, tuple[int, ...], None]:
    """Reconstruct the exact tuple accepted by `Random.setstate()`."""
    try:
        _validate_rng_state(state)
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error
    return (state.state_version, (*state.mt_state, state.index), None)


def _parse_rng(value: object) -> RngState:
    rng = _exact_object(value, ("engine", "python_version", "state"), name="rng")
    state = _exact_object(rng["state"], ("state_version", "mt_state", "index", "gauss_next"), name="rng state")
    if state["gauss_next"] is not None:
        raise ValueError("rng gauss_next must be null")
    result = RngState(
        _integer(state["state_version"], name="rng state_version"),
        tuple(_integer_array(state["mt_state"], name="rng mt_state")),
        _integer(state["index"], name="rng index", maximum=624),
        None,
    )
    _validate_rng_state(result)
    return result


def _parse_identifier(value: object, *, name: str) -> CandidateId:
    items = _array(value, name=name)
    if len(items) != 2:
        raise ValueError(f"{name} must contain exactly two integers")
    return CandidateId(
        _integer(items[0], name=f"{name} birth_generation"),
        _integer(items[1], name=f"{name} birth_index"),
    )


def _parse_method(value: object, *, expected_name: MethodName) -> MethodTrialResult:
    document = _exact_object(value, ("name", "score", "diagnostics"), name=f"{expected_name} method")
    name = _string(document["name"], name="method name")
    if name != expected_name:
        raise ValueError("checkpoint trial methods must be in METHOD_ORDER")
    diagnostics = _frozen_json(document["diagnostics"], name=f"{name} diagnostics")
    if not isinstance(diagnostics, Mapping):
        raise ValueError(f"{name} diagnostics must be an object")
    return MethodTrialResult(
        expected_name,
        _float(document["score"], name=f"{name} score", bounded=True),
        cast(Mapping[str, object], diagnostics),
    )


def _parse_trial(value: object, *, family: FamilyName) -> TrialResult:
    document = _exact_object(
        value,
        ("seed", "aggregate_score", "methods", "model_diagnostics"),
        name="candidate trial",
    )
    method_values = _array(document["methods"], name="trial methods")
    if len(method_values) != len(METHOD_ORDER):
        raise ValueError("trial methods must contain exactly four methods")
    methods = tuple(
        _parse_method(item, expected_name=name) for name, item in zip(METHOD_ORDER, method_values, strict=True)
    )
    try:
        model_diagnostics: ModelDiagnostics = freeze_model_diagnostics(document["model_diagnostics"])
        validate_model_diagnostics_for_family(family, model_diagnostics)
    except (TypeError, ValueError) as error:
        raise ValueError(f"candidate trial model diagnostics are invalid: {error}") from error
    return TrialResult(
        _integer(document["seed"], name="trial seed"),
        _float(document["aggregate_score"], name="trial aggregate_score", bounded=True),
        cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods),
        model_diagnostics,
    )


def _is_legacy_failure_document(value: object) -> bool:
    if type(value) is not dict:
        return False
    document = cast(dict[object, object], value)
    return all(type(key) is str for key in document) and set(document) == set(_LEGACY_FAILURE_KEYS)


def _parse_failure(value: object) -> CandidateFailure:
    legacy = _is_legacy_failure_document(value)
    document = _exact_object(
        value,
        _LEGACY_FAILURE_KEYS if legacy else _FAILURE_KEYS,
        name="candidate invalid diagnostic",
    )
    kind_value = _string(document["kind"], name="candidate failure kind")
    if kind_value not in _FAILURE_KINDS:
        raise ValueError("candidate failure kind is not recognized")
    seed_value = document["seed"]
    seed = None if seed_value is None else _integer(seed_value, name="candidate failure seed")
    detail = _string(document["detail"], name="candidate failure detail", nonempty=True)
    if legacy:
        stage, affected_evidence, evidence_state, corrective_action, authority = _LEGACY_FAILURE_PROVENANCE[kind_value]
        return CandidateFailure(
            kind_value,
            seed,
            detail,
            stage=stage,
            affected_evidence=affected_evidence,
            evidence_state=evidence_state,
            corrective_action=corrective_action,
            authority=authority,
        )
    return CandidateFailure(
        kind_value,
        seed,
        detail,
        stage=_string(document["stage"], name="candidate failure stage", nonempty=True),
        affected_evidence=_string(
            document["affected_evidence"], name="candidate failure affected_evidence", nonempty=True
        ),
        evidence_state=cast(
            EvidenceState,
            _string(document["evidence_state"], name="candidate failure evidence_state", nonempty=True),
        ),
        corrective_action=_string(
            document["corrective_action"], name="candidate failure corrective_action", nonempty=True
        ),
        authority=cast(
            FailureAuthority,
            _string(document["authority"], name="candidate failure authority", nonempty=True),
        ),
    )


def _parse_duplicate(value: object) -> DuplicateDiagnostic:
    document = _exact_object(value, ("attempt", "outcome", "detail"), name="duplicate diagnostic")
    outcome_value = _string(document["outcome"], name="duplicate outcome")
    if outcome_value not in _DUPLICATE_OUTCOMES:
        raise ValueError("duplicate outcome is not recognized")
    return DuplicateDiagnostic(
        _integer(document["attempt"], name="duplicate attempt"),
        outcome_value,
        _string(document["detail"], name="duplicate detail", nonempty=True),
    )


def _parse_gene(value: object, coordinate: GeneCoordinate, *, family: FamilyName) -> float | int:
    if coordinate.kind == "integer":
        gene = _integer(value, name=f"{coordinate.name} gene for family {family}", minimum=-(2**63))
    else:
        gene = _float(value, name=f"{coordinate.name} gene for family {family}")
    if not coordinate.bounds.lower <= gene <= coordinate.bounds.upper:
        raise ValueError(f"{coordinate.name} gene for family {family} is outside its coordinate bounds")
    return gene


def _parse_candidate(value: object, *, families: Mapping[FamilyName, FamilyCheckpointSpec]) -> Candidate:
    document = _exact_object(
        value,
        ("identifier", "family", "genes", "status", "fitness", "trials", "invalid", "duplicate_diagnostics"),
        name="candidate",
    )
    family = _family_name(document["family"], name="candidate family")
    if family not in families:
        raise ValueError(f"candidate family {family} is not enabled")
    genes_value = document["genes"]
    genes: tuple[float | int, ...] | None
    if genes_value is None:
        genes = None
    else:
        gene_values = _array(genes_value, name="candidate genes")
        coordinates = families[family].coordinates
        if len(gene_values) != len(coordinates):
            raise ValueError(f"candidate genes for family {family} have the wrong arity")
        genes = tuple(
            _parse_gene(gene, coordinate, family=family)
            for gene, coordinate in zip(gene_values, coordinates, strict=True)
        )
    status = _string(document["status"], name="candidate status")
    if status not in {"valid", "invalid"}:
        raise ValueError("checkpoint candidate status must be valid or invalid")
    invalid_value = document["invalid"]
    invalid = None if invalid_value is None else _parse_failure(invalid_value)
    return Candidate(
        _parse_identifier(document["identifier"], name="candidate identifier"),
        family,
        genes,
        cast(Literal["valid", "invalid"], status),
        _float(document["fitness"], name="candidate fitness", bounded=True),
        tuple(_parse_trial(item, family=family) for item in _array(document["trials"], name="candidate trials")),
        invalid,
        tuple(
            _parse_duplicate(item)
            for item in _array(document["duplicate_diagnostics"], name="candidate duplicate_diagnostics")
        ),
    )


def _parse_history_row(value: object, *, families: frozenset[FamilyName]) -> HistoryRow:
    document = _exact_object(
        value,
        (
            "generation",
            "scope",
            "family",
            "candidate_count",
            "valid_count",
            "best_fitness",
            "mean_fitness",
            "best_identifier",
        ),
        name="history row",
    )
    scope_value = _string(document["scope"], name="history scope")
    if scope_value not in {"family", "overall"}:
        raise ValueError("history scope must be family or overall")
    family_value = document["family"]
    family: FamilyName | None
    if scope_value == "overall":
        if family_value is not None:
            raise ValueError("overall history family must be null")
        family = None
    else:
        family = _family_name(family_value, name="history family")
        if family not in families:
            raise ValueError(f"history family {family} is not enabled")
    return HistoryRow(
        _integer(document["generation"], name="history generation"),
        cast(Literal["family", "overall"], scope_value),
        family,
        _integer(document["candidate_count"], name="history candidate_count", minimum=1),
        _integer(document["valid_count"], name="history valid_count"),
        _float(document["best_fitness"], name="history best_fitness", bounded=True),
        _float(document["mean_fitness"], name="history mean_fitness", bounded=True),
        _parse_identifier(document["best_identifier"], name="history best_identifier"),
    )


def _method_weights(similarity: SimilarityConfig) -> dict[MethodName, float]:
    weights = similarity.method_weights
    return {
        "autocorrelation": weights.autocorrelation,
        "frame_size_ks": weights.frame_size_ks,
        "iat_ks": weights.iat_ks,
        "multiscale_rate": weights.multiscale_rate,
    }


def _weighted_score(methods: Sequence[MethodTrialResult], similarity: SimilarityConfig) -> float:
    weights = _method_weights(similarity)
    score = math.fsum(weights[method.name] * method.score for method in methods)
    if -1e-12 <= score < 0.0:
        return 0.0
    if 1.0 < score <= 1.0 + 1e-12:
        return 1.0
    return score


def _validate_candidate(
    candidate: Candidate, state: CheckpointState, specs: Mapping[FamilyName, FamilyCheckpointSpec]
) -> None:
    if type(candidate) is not Candidate:
        raise TypeError("population must contain Candidate values")
    if candidate.family not in specs:
        raise ValueError(f"candidate family {candidate.family} is not enabled")
    if candidate.identifier.birth_generation > state.generation:
        raise ValueError("candidate identifier birth generation exceeds checkpoint generation")
    if candidate.status not in {"valid", "invalid"}:
        raise ValueError("checkpoint population contains a pending candidate")
    if candidate.genes is not None:
        coordinates = specs[candidate.family].coordinates
        if len(candidate.genes) != len(coordinates):
            raise ValueError(f"candidate genes for family {candidate.family} have the wrong arity")
        for gene, coordinate in zip(candidate.genes, coordinates, strict=True):
            _parse_gene(gene, coordinate, family=candidate.family)
        if candidate.family == "markov_renewal" and not cast(float, candidate.genes[0]) < cast(
            float, candidate.genes[1]
        ):
            raise ValueError("candidate markov_renewal genes must preserve canonical q1 strictly less than q2")
        if candidate.family == "mmpp" and not cast(float, candidate.genes[2]) < cast(float, candidate.genes[3]):
            raise ValueError("candidate mmpp genes must preserve canonical lambda0 strictly less than lambda1")
    if candidate.status == "valid":
        if candidate.genes is None:
            raise ValueError("valid candidate genes must not be null")
        if candidate.invalid is not None:
            raise ValueError("valid candidate invalid diagnostic must be null")
        if tuple(trial.seed for trial in candidate.trials) != state.compatibility.trial_seeds:
            raise ValueError("valid candidate trials must contain all configured trial seeds in order")
    else:
        if candidate.fitness != 0.0:
            raise ValueError("invalid candidate fitness must be exactly 0.0")
        if candidate.invalid is None:
            raise ValueError("invalid candidate must contain an invalid diagnostic")
    seen_seeds: set[int] = set()
    for trial in candidate.trials:
        if trial.seed in seen_seeds:
            raise ValueError("candidate contains a duplicate trial seed")
        seen_seeds.add(trial.seed)
        expected_aggregate = _weighted_score(trial.methods, state.compatibility.similarity)
        if trial.aggregate_score != expected_aggregate:
            raise ValueError("candidate trial aggregate_score does not equal the recomputed weighted score")
    if candidate.status == "valid":
        expected_fitness = math.fsum(trial.aggregate_score for trial in candidate.trials) / len(candidate.trials)
        if candidate.fitness != expected_fitness:
            raise ValueError("candidate fitness does not equal the recomputed trial mean")


def summarize_generation(
    generation: int,
    population: Sequence[Candidate],
    families: Sequence[FamilyName],
) -> tuple[HistoryRow, ...]:
    """Derive lexical family rows followed by one overall row from an evaluated population."""
    _integer(generation, name="history generation")
    if not population:
        raise _invalid("cannot summarize an empty population")
    family_names = tuple(families)
    if family_names != tuple(sorted(family_names)) or len(family_names) != len(set(family_names)):
        raise _invalid("history families must be unique and lexical")

    def make_row(candidates: tuple[Candidate, ...], family: FamilyName | None) -> HistoryRow:
        if not candidates:
            raise _invalid(f"history family {family} has no candidate")
        best = min(candidates, key=lambda item: (-item.fitness, item.identifier))
        return HistoryRow(
            generation,
            "overall" if family is None else "family",
            family,
            len(candidates),
            sum(candidate.status == "valid" for candidate in candidates),
            best.fitness,
            math.fsum(candidate.fitness for candidate in candidates) / len(candidates),
            best.identifier,
        )

    complete = tuple(population)
    rows = [
        make_row(tuple(candidate for candidate in complete if candidate.family == family), family)
        for family in family_names
    ]
    overall = make_row(complete, None)
    grouped_mean = math.fsum(row.mean_fitness * row.candidate_count for row in rows) / len(complete)
    rows.append(replace(overall, mean_fitness=grouped_mean))
    return tuple(rows)


def _validate_history(state: CheckpointState, family_names: tuple[FamilyName, ...]) -> None:
    block_size = len(family_names) + 1
    expected_length = (state.generation + 1) * block_size
    if len(state.history) != expected_length:
        raise ValueError("history must contain one complete block for every generation")
    for generation in range(state.generation + 1):
        block = state.history[generation * block_size : (generation + 1) * block_size]
        expected_shape = tuple((generation, "family", family) for family in family_names) + (
            (generation, "overall", None),
        )
        if tuple((row.generation, row.scope, row.family) for row in block) != expected_shape:
            raise ValueError("history rows must be ascending lexical family rows followed by overall")
        family_rows = block[:-1]
        overall = block[-1]
        for row in block:
            candidate_count = _integer(row.candidate_count, name="history candidate_count", minimum=1)
            valid_count = _integer(row.valid_count, name="history valid_count")
            best_fitness = _float(row.best_fitness, name="history best_fitness", bounded=True)
            mean_fitness = _float(row.mean_fitness, name="history mean_fitness", bounded=True)
            if valid_count > candidate_count:
                raise ValueError("history valid_count must not exceed candidate_count")
            if valid_count == 0 and (best_fitness != 0.0 or mean_fitness != 0.0):
                raise ValueError("history row with zero valid_count must have zero best_fitness and mean_fitness")
            mean_numerator, mean_denominator = mean_fitness.as_integer_ratio()
            best_numerator, best_denominator = best_fitness.as_integer_ratio()
            if mean_numerator * candidate_count * best_denominator > best_numerator * valid_count * mean_denominator:
                raise ValueError("history mean_fitness is not feasible for valid_count")
            if row.best_identifier.birth_generation > generation:
                raise ValueError("history best identifier birth generation exceeds row generation")
        if sum(row.candidate_count for row in family_rows) != overall.candidate_count:
            raise ValueError("history overall candidate_count does not equal family counts")
        if sum(row.valid_count for row in family_rows) != overall.valid_count:
            raise ValueError("history overall valid_count does not equal family counts")
        if overall.candidate_count != state.compatibility.genetic.population_size:
            raise ValueError("history overall candidate_count does not equal population_size")
        family_best = min(family_rows, key=lambda row: (-row.best_fitness, row.best_identifier))
        if (overall.best_fitness, overall.best_identifier) != (
            family_best.best_fitness,
            family_best.best_identifier,
        ):
            raise ValueError("history overall best does not equal the recomputed family best")
        expected_mean = (
            math.fsum(row.mean_fitness * row.candidate_count for row in family_rows) / overall.candidate_count
        )
        if overall.mean_fitness != expected_mean:
            raise ValueError("history overall mean does not equal the recomputed family mean")
    current = summarize_generation(state.generation, state.population, family_names)
    if state.history[-block_size:] != current:
        raise ValueError("last history block does not equal the current population summary")


def _history_progress(state: CheckpointState, *, block_size: int) -> tuple[CandidateId, float, int]:
    """Recompute the retained winner and exact stagnation counter from overall history rows."""
    overall_rows = state.history[block_size - 1 :: block_size]
    retained_identifier = overall_rows[0].best_identifier
    retained_fitness = overall_rows[0].best_fitness
    consecutive_stagnation = 0
    genetic = state.compatibility.genetic
    for generation, row in enumerate(overall_rows[1:], start=1):
        improvement = row.best_fitness - retained_fitness
        if improvement > 0.0:
            retained_identifier = row.best_identifier
            retained_fitness = row.best_fitness
        consecutive_stagnation = 0 if improvement > genetic.early_stopping_tolerance else consecutive_stagnation + 1
        historical_terminal: TerminalReason
        if generation == genetic.generation_count:
            historical_terminal = "hard_limit"
        elif genetic.early_stopping_generations > 0 and consecutive_stagnation >= genetic.early_stopping_generations:
            historical_terminal = "early_stop"
        else:
            historical_terminal = "running"
        if generation < state.generation and historical_terminal == "early_stop":
            raise ValueError(f"history continues after early_stop at generation {generation}")
    return retained_identifier, retained_fitness, consecutive_stagnation


def _validate_state(state: CheckpointState) -> None:
    if type(state) is not CheckpointState:
        raise TypeError("checkpoint state must be CheckpointState")
    _validate_compatibility_shape(state.compatibility)
    generation = _integer(state.generation, name="generation")
    if generation > state.compatibility.genetic.generation_count:
        raise ValueError("generation exceeds configured generation_count")
    _validate_rng_state(state.rng_state)
    if type(state.population) is not tuple:
        raise TypeError("population must be a tuple")
    if len(state.population) != state.compatibility.genetic.population_size:
        raise ValueError("population must contain exactly population_size candidates")
    family_names: tuple[FamilyName, ...] = tuple(family.name for family in state.compatibility.families)
    specs: dict[FamilyName, FamilyCheckpointSpec] = {family.name: family for family in state.compatibility.families}
    for candidate in state.population:
        _validate_candidate(candidate, state, specs)
    identifiers = tuple(candidate.identifier for candidate in state.population)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("population contains a duplicate candidate identifier")
    if {candidate.family for candidate in state.population} != set(family_names):
        raise ValueError("population must represent every configured family")
    if type(state.history) is not tuple or any(type(row) is not HistoryRow for row in state.history):
        raise TypeError("history must be a tuple of HistoryRow values")
    _validate_history(state, family_names)
    candidates_by_id = {candidate.identifier: candidate for candidate in state.population}
    if state.best_identifier not in candidates_by_id:
        raise ValueError("best identifier must occur in the current population")
    best = candidates_by_id[state.best_identifier]
    if best.fitness != state.best_fitness:
        raise ValueError("best fitness must equal the identified current candidate fitness")
    current_best = min(state.population, key=lambda candidate: (-candidate.fitness, candidate.identifier))
    if (state.best_fitness, state.best_identifier) != (current_best.fitness, current_best.identifier):
        raise ValueError("best must equal the stable current population winner")
    _float(state.best_fitness, name="best fitness", bounded=True)
    retained_identifier, retained_fitness, expected_stagnation = _history_progress(
        state,
        block_size=len(family_names) + 1,
    )
    if (state.best_fitness, state.best_identifier) != (retained_fitness, retained_identifier):
        raise ValueError("best does not equal the retained history winner")
    stagnation = _integer(state.consecutive_stagnation, name="consecutive_stagnation")
    if stagnation > generation:
        raise ValueError("consecutive_stagnation cannot exceed generation")
    if stagnation != expected_stagnation:
        raise ValueError("consecutive_stagnation does not equal the value recomputed from history")
    if state.terminal_reason not in _TERMINAL_REASONS:
        raise ValueError("terminal_reason is not recognized")
    genetic = state.compatibility.genetic
    hard = generation == genetic.generation_count
    early = genetic.early_stopping_generations > 0 and stagnation >= genetic.early_stopping_generations
    if state.terminal_reason == "hard_limit" and not hard:
        raise ValueError("hard_limit requires generation equal to generation_count")
    if state.terminal_reason == "early_stop" and (hard or not early):
        raise ValueError("early_stop requires a pre-limit generation and the configured stagnation count")
    if state.terminal_reason == "running" and (hard or early):
        raise ValueError("running checkpoint already satisfies a terminal condition")


def _coordinate_document(coordinate: GeneCoordinate) -> dict[str, object]:
    return {
        "name": coordinate.name,
        "kind": coordinate.kind,
        "lower": coordinate.bounds.lower,
        "upper": coordinate.bounds.upper,
    }


def _family_document(family: FamilyCheckpointSpec) -> dict[str, object]:
    return {
        "name": family.name,
        "gene_order": list(family.gene_order),
        "coordinates": [_coordinate_document(coordinate) for coordinate in family.coordinates],
        "operators": {
            "crossover_probability": family.crossover_probability,
            "mutation_probability": family.mutation_probability,
            "mutation_scale": family.mutation_scale,
        },
    }


def _genetic_document(genetic: GeneticCheckpointSettings) -> dict[str, object]:
    return {name: getattr(genetic, name) for name in _GENETIC_KEYS}


def _similarity_document(similarity: SimilarityConfig) -> dict[str, object]:
    return similarity.model_dump(mode="python")


def _method_document(method: MethodTrialResult) -> dict[str, object]:
    return {"name": method.name, "score": method.score, "diagnostics": _thaw_json(method.diagnostics)}


def _trial_document(trial: TrialResult) -> dict[str, object]:
    return {
        "seed": trial.seed,
        "aggregate_score": trial.aggregate_score,
        "methods": [_method_document(method) for method in trial.methods],
        "model_diagnostics": dict(trial.model_diagnostics),
    }


def _identifier_document(identifier: CandidateId) -> list[int]:
    return [identifier.birth_generation, identifier.birth_index]


def _candidate_document(candidate: Candidate) -> dict[str, object]:
    invalid = candidate.invalid
    return {
        "identifier": _identifier_document(candidate.identifier),
        "family": candidate.family,
        "genes": None if candidate.genes is None else list(candidate.genes),
        "status": candidate.status,
        "fitness": candidate.fitness,
        "trials": [_trial_document(trial) for trial in candidate.trials],
        "invalid": None
        if invalid is None
        else {
            "kind": invalid.kind,
            "seed": invalid.seed,
            "detail": invalid.detail,
            "stage": invalid.stage,
            "affected_evidence": invalid.affected_evidence,
            "evidence_state": invalid.evidence_state,
            "corrective_action": invalid.corrective_action,
            "authority": invalid.authority,
        },
        "duplicate_diagnostics": [
            {"attempt": item.attempt, "outcome": item.outcome, "detail": item.detail}
            for item in candidate.duplicate_diagnostics
        ],
    }


def _history_document(row: HistoryRow) -> dict[str, object]:
    return {
        "generation": row.generation,
        "scope": row.scope,
        "family": row.family,
        "candidate_count": row.candidate_count,
        "valid_count": row.valid_count,
        "best_fitness": row.best_fitness,
        "mean_fitness": row.mean_fitness,
        "best_identifier": _identifier_document(row.best_identifier),
    }


def _checkpoint_document(state: CheckpointState) -> dict[str, object]:
    compatibility = state.compatibility
    rng = state.rng_state
    return {
        "scientific_artifact_schema": compatibility.scientific_artifact_schema,
        "experiment_sha256": compatibility.experiment_sha256,
        "reference_sha256": compatibility.reference_sha256,
        "capture_sha256": compatibility.capture_sha256,
        "observation_window_seconds": compatibility.observation_window_seconds,
        "trial_seeds": list(compatibility.trial_seeds),
        "families": [_family_document(family) for family in compatibility.families],
        "genetic": _genetic_document(compatibility.genetic),
        "similarity": _similarity_document(compatibility.similarity),
        "rng": {
            "engine": compatibility.rng_engine,
            "python_version": compatibility.python_version,
            "state": {
                "state_version": rng.state_version,
                "mt_state": list(rng.mt_state),
                "index": rng.index,
                "gauss_next": rng.gauss_next,
            },
        },
        "generation": state.generation,
        "population": [_candidate_document(candidate) for candidate in state.population],
        "history": [_history_document(row) for row in state.history],
        "best": {"identifier": _identifier_document(state.best_identifier), "fitness": state.best_fitness},
        "consecutive_stagnation": state.consecutive_stagnation,
        "terminal_reason": state.terminal_reason,
    }


def render_checkpoint(state: CheckpointState) -> bytes:
    """Render one validated checkpoint as sorted compact finite JSON with a trailing newline."""
    try:
        _validate_state(state)
        text = json.dumps(_checkpoint_document(state), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error
    return f"{text}\n".encode()


def parse_checkpoint(content: bytes, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Parse strict checkpoint bytes and reject incompatibility before RNG/state reconstruction."""
    if type(content) is not bytes:
        raise TypeError("checkpoint content must be bytes")
    document = _load_json(content)
    try:
        require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="checkpoint")
        document = _exact_object(document, _ROOT_KEYS, name="checkpoint root")
        experiment_sha256 = _sha256(document["experiment_sha256"], name="experiment_sha256")
        _validate_compatibility_shape(compatibility)
        if experiment_sha256 != compatibility.experiment_sha256:
            raise _compatibility_error("experiment snapshot SHA-256")
        stored_compatibility = _parse_compatibility(document)
        validate_compatibility(stored_compatibility, compatibility)
        family_names: frozenset[FamilyName] = frozenset(family.name for family in stored_compatibility.families)
        families: dict[FamilyName, FamilyCheckpointSpec] = {
            family.name: family for family in stored_compatibility.families
        }
        best = _exact_object(document["best"], ("identifier", "fitness"), name="best")
        terminal_value = _string(document["terminal_reason"], name="terminal_reason")
        if terminal_value not in _TERMINAL_REASONS:
            raise ValueError("terminal_reason is not recognized")
        state = CheckpointState(
            stored_compatibility,
            _integer(document["generation"], name="generation"),
            tuple(
                _parse_candidate(item, families=families) for item in _array(document["population"], name="population")
            ),
            tuple(
                _parse_history_row(item, families=family_names) for item in _array(document["history"], name="history")
            ),
            _parse_rng(document["rng"]),
            _parse_identifier(best["identifier"], name="best identifier"),
            _float(best["fitness"], name="best fitness", bounded=True),
            _integer(document["consecutive_stagnation"], name="consecutive_stagnation"),
            terminal_value,
        )
        _validate_state(state)
        if render_checkpoint(state) != content:
            population = _array(document["population"], name="population")
            accepts_legacy = any(
                type(candidate) is dict
                and _is_legacy_failure_document(cast(dict[str, object], candidate).get("invalid"))
                for candidate in population
            )
            legacy_content = (
                f"{json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)}\n".encode()
            )
            if not accepts_legacy or legacy_content != content:
                raise ValueError(
                    "checkpoint JSON must use the canonical sorted compact encoding with one final newline"
                )
        return state
    except TrafficlabError:
        raise
    except (TypeError, ValueError, ValidationError) as error:
        raise _invalid(str(error)) from error


def publish_checkpoint(path: Path, state: CheckpointState) -> None:
    """Atomically replace the canonical checkpoint with one complete validated generation."""
    content = render_checkpoint(state)
    atomic_replace(path, content)


def load_checkpoint(path: Path, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Read and validate a compatible authoritative checkpoint without changing it."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read checkpoint {path}: {error}",
            corrective_action="verify checkpoint.json is readable before resuming",
        ) from error
    state = parse_checkpoint(content, compatibility)
    validate_compatibility(state.compatibility, compatibility)
    return state


def _parse_decimal(value: str, *, name: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a canonical nonnegative decimal integer")
    result = int(value)
    if str(result) != value:
        raise ValueError(f"{name} must be a canonical nonnegative decimal integer")
    return result


def _parse_repr_float(value: str, *, name: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a finite Python float repr") from error
    if not math.isfinite(result) or repr(result) != value or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite Python float repr in [0, 1]")
    return result


def _parse_history_csv(content: bytes, family_names: frozenset[FamilyName]) -> tuple[HistoryRow, ...]:
    try:
        text = content.decode("utf-8")
        rows = list(csv.reader(StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError(f"history CSV is invalid: {error}") from error
    if not rows or tuple(rows[0]) != _HISTORY_HEADER:
        raise ValueError("history CSV has the wrong header")
    parsed: list[HistoryRow] = []
    for fields in rows[1:]:
        if len(fields) != len(_HISTORY_HEADER):
            raise ValueError("history CSV row has the wrong field count")
        generation, scope, family_field, candidate_count, valid_count, best, mean, birth_generation, birth_index = (
            fields
        )
        if scope not in {"family", "overall"}:
            raise ValueError("history CSV scope must be family or overall")
        if scope == "overall":
            if family_field:
                raise ValueError("overall history CSV family must be empty")
            family = None
        else:
            family = _family_name(family_field, name="history CSV family")
            if family not in family_names:
                raise ValueError("history CSV family is not enabled")
        parsed.append(
            HistoryRow(
                _parse_decimal(generation, name="history CSV generation"),
                cast(Literal["family", "overall"], scope),
                family,
                _parse_decimal(candidate_count, name="history CSV candidate_count"),
                _parse_decimal(valid_count, name="history CSV valid_count"),
                _parse_repr_float(best, name="history CSV best_fitness"),
                _parse_repr_float(mean, name="history CSV mean_fitness"),
                CandidateId(
                    _parse_decimal(birth_generation, name="history CSV best_birth_generation"),
                    _parse_decimal(birth_index, name="history CSV best_birth_index"),
                ),
            )
        )
    return tuple(parsed)


def render_history_csv(state: CheckpointState) -> bytes:
    """Render and reparse the exact CSV projection derived solely from checkpoint history."""
    try:
        _validate_state(state)
        stream = StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_HISTORY_HEADER)
        for row in state.history:
            writer.writerow(
                (
                    str(row.generation),
                    row.scope,
                    "" if row.family is None else row.family,
                    str(row.candidate_count),
                    str(row.valid_count),
                    repr(row.best_fitness),
                    repr(row.mean_fitness),
                    str(row.best_identifier.birth_generation),
                    str(row.best_identifier.birth_index),
                )
            )
        content = stream.getvalue().encode("utf-8")
        family_names: frozenset[FamilyName] = frozenset(family.name for family in state.compatibility.families)
        if _parse_history_csv(content, family_names) != state.history:
            raise ValueError("history CSV did not reconstruct the exact checkpoint rows")
        return content
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error


def publish_history_csv(path: Path, state: CheckpointState) -> None:
    """Atomically replace derived history after validating its exact scalar reconstruction."""
    content = render_history_csv(state)
    atomic_replace(path, content)


def publish_generation(run_directory: Path, state: CheckpointState) -> None:
    """Publish authoritative checkpoint first and derived history second."""
    publish_checkpoint(run_directory / "checkpoint.json", state)
    publish_history_csv(run_directory / "ga_history.csv", state)


def load_generation(run_directory: Path, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Load authoritative checkpoint and repair only a missing or stale derived history projection."""
    state = load_checkpoint(run_directory / "checkpoint.json", compatibility)
    expected = render_history_csv(state)
    history_path = run_directory / "ga_history.csv"
    try:
        existing = history_path.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise TrafficlabError(
            f"could not read derived history {history_path}: {error}",
            corrective_action="verify ga_history.csv is readable before resuming",
        ) from error
    if existing != expected:
        publish_history_csv(history_path, state)
    return state
