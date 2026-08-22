"""Canonical checkpoint JSON conversion and persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import FloatBounds, IntegerBounds, SimilarityConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scientific_schema import require_current_scientific_schema
from trafficlab.comparison.similarity.common import FrozenJsonValue
from trafficlab.fitting.genetic.checkpoint.compatibility import (
    GENETIC_KEYS,
    atomic_replace,
    compatibility_error,
    invalid_checkpoint,
    is_rng_engine_identifier,
    load_json_object,
    thaw_json,
    validate_compatibility,
    validate_compatibility_shape,
    validation_error_detail,
)
from trafficlab.fitting.genetic.checkpoint.schema import (
    CandidateIdentifierRecord,
    CandidateRecord,
    CheckpointArtifact,
    CheckpointCompatibility,
    CheckpointState,
    CoordinateRecord,
    FamilyCheckpointRecord,
    FamilyCheckpointSpec,
    GeneticCheckpointSettings,
    HistoryRecord,
)
from trafficlab.fitting.genetic.checkpoint.state import validate_state
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateId,
    HistoryRow,
    MethodTrialResult,
    TrialResult,
)


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
    return {name: getattr(genetic, name) for name in GENETIC_KEYS}


def _similarity_document(similarity: SimilarityConfig) -> dict[str, object]:
    return similarity.model_dump(mode="python")


def _method_document(method: MethodTrialResult) -> dict[str, object]:
    return {
        "name": method.name,
        "score": method.score,
        "diagnostics": thaw_json(cast(FrozenJsonValue, method.diagnostics)),
    }


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
        "experiment_identity": compatibility.experiment_identity.as_dict(),
        "reference_identity": compatibility.reference_identity.as_dict(),
        "capture_identity": compatibility.capture_identity.as_dict(),
        "observation_window_seconds": compatibility.observation_window_seconds,
        "trial_seeds": list(compatibility.trial_seeds),
        "trial_limits": compatibility.trial_limits.model_dump(mode="python"),
        "families": [_family_document(family) for family in compatibility.families],
        "family_priority": list(state.family_priority),
        "genetic": _genetic_document(compatibility.genetic),
        "similarity": _similarity_document(compatibility.similarity),
        "rng": {
            "engine": compatibility.rng_engine,
            "python_version": compatibility.python_version,
            "state": {
                "bit_generator": rng.bit_generator,
                "state": {"state": rng.state.state, "inc": rng.state.inc},
                "has_uint32": rng.has_uint32,
                "uinteger": rng.uinteger,
            },
        },
        "generation": state.generation,
        "population": [_candidate_document(candidate) for candidate in state.population],
        "history": [_history_document(row) for row in state.history],
        "best": {"identifier": _identifier_document(state.best_identifier), "fitness": state.best_fitness},
        "consecutive_stagnation": state.consecutive_stagnation,
        "terminal_reason": state.terminal_reason,
    }


def _coordinate_from_record(record: CoordinateRecord) -> GeneCoordinate:
    bounds: FloatBounds | IntegerBounds
    if record.kind == "integer":
        bounds = IntegerBounds(lower=record.lower, upper=record.upper)
    else:
        bounds = FloatBounds(lower=record.lower, upper=record.upper)
    return GeneCoordinate(record.name, record.kind, bounds)


def _family_from_record(record: FamilyCheckpointRecord) -> FamilyCheckpointSpec:
    operators = record.operators
    return FamilyCheckpointSpec(
        name=record.name,
        gene_order=record.gene_order,
        coordinates=tuple(_coordinate_from_record(coordinate) for coordinate in record.coordinates),
        crossover_probability=operators.crossover_probability,
        mutation_probability=operators.mutation_probability,
        mutation_scale=operators.mutation_scale,
    )


def _identifier_from_record(record: CandidateIdentifierRecord) -> CandidateId:
    return CandidateId(birth_generation=record[0], birth_index=record[1])


def _candidate_from_record(record: CandidateRecord) -> Candidate:
    invalid = (
        None if record.invalid is None else CandidateFailure.model_validate(record.invalid.model_dump(mode="python"))
    )
    return Candidate(
        identifier=_identifier_from_record(record.identifier),
        family=record.family,
        genes=record.genes,
        status=record.status,
        fitness=record.fitness,
        trials=record.trials,
        invalid=invalid,
        duplicate_diagnostics=record.duplicate_diagnostics,
    )


def _history_from_record(record: HistoryRecord) -> HistoryRow:
    return HistoryRow(
        generation=record.generation,
        scope=record.scope,
        family=record.family,
        candidate_count=record.candidate_count,
        valid_count=record.valid_count,
        best_fitness=record.best_fitness,
        mean_fitness=record.mean_fitness,
        best_identifier=_identifier_from_record(record.best_identifier),
    )


def _compatibility_from_artifact(artifact: CheckpointArtifact) -> CheckpointCompatibility:
    return CheckpointCompatibility(
        scientific_artifact_schema=artifact.scientific_artifact_schema,
        experiment_identity=artifact.experiment_identity.to_runtime(),
        reference_identity=artifact.reference_identity.to_runtime(),
        capture_identity=artifact.capture_identity.to_runtime(),
        observation_window_seconds=artifact.observation_window_seconds,
        trial_seeds=artifact.trial_seeds,
        trial_limits=artifact.trial_limits,
        families=tuple(_family_from_record(family) for family in artifact.families),
        family_priority=artifact.family_priority,
        genetic=artifact.genetic,
        similarity=artifact.similarity,
        python_version=artifact.rng.python_version,
        rng_engine=artifact.rng.engine,
    )


def render_checkpoint(state: CheckpointState) -> bytes:
    """Render one validated checkpoint as sorted compact finite JSON with a trailing newline."""
    try:
        validate_state(state)
        document = _checkpoint_document(state)
        wire_document = json.loads(json.dumps(document, allow_nan=False))
        artifact = CheckpointArtifact.model_validate(wire_document)
        validated_document = artifact.model_dump(mode="json")
        if validated_document != wire_document:
            raise ValueError("checkpoint schema validation changed the canonical document")
        text = json.dumps(validated_document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except ValidationError as error:
        raise invalid_checkpoint(validation_error_detail(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise invalid_checkpoint(str(error)) from error
    return f"{text}\n".encode()


def parse_checkpoint(content: bytes, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Parse strict checkpoint bytes and reject incompatibility before RNG/state reconstruction."""
    if type(content) is not bytes:
        raise TypeError("checkpoint content must be bytes")
    document = load_json_object(content)
    try:
        require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="checkpoint")
        experiment_identity = ContentIdentity.from_dict(document["experiment_identity"], name="experiment")
        validate_compatibility_shape(compatibility)
        if experiment_identity != compatibility.experiment_identity:
            raise compatibility_error("experiment snapshot SHA-256/size identity")
        raw_rng = document.get("rng")
        if type(raw_rng) is dict:
            engine = cast(dict[str, object], raw_rng).get("engine")
            if is_rng_engine_identifier(engine) and engine != compatibility.rng_engine:
                raise compatibility_error("RNG engine")
        artifact = CheckpointArtifact.model_validate(document)
        stored_compatibility = _compatibility_from_artifact(artifact)
        validate_compatibility(stored_compatibility, compatibility)
        state = CheckpointState(
            compatibility=stored_compatibility,
            generation=artifact.generation,
            population=tuple(_candidate_from_record(candidate) for candidate in artifact.population),
            history=tuple(_history_from_record(row) for row in artifact.history),
            rng_state=artifact.rng.state,
            best_identifier=_identifier_from_record(artifact.best.identifier),
            best_fitness=artifact.best.fitness,
            consecutive_stagnation=artifact.consecutive_stagnation,
            terminal_reason=artifact.terminal_reason,
            family_priority=stored_compatibility.family_priority,
        )
        validate_state(state)
        if render_checkpoint(state) != content:
            raise ValueError("checkpoint JSON must use the canonical sorted compact encoding with one final newline")
        return state
    except TrafficlabError:
        raise
    except ValidationError as error:
        raise invalid_checkpoint(validation_error_detail(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise invalid_checkpoint(str(error)) from error


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
