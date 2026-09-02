"""Direct codec checkpoint behavior tests."""

import json
import math
from typing import Any, cast

import pytest

from tests.support.checkpoint import (
    COMPATIBILITY,
    MARKOV_MODEL_DIAGNOSTICS,
    POPULATION,
    SIMILARITY,
    VALID_STATE,
    candidate_update,
    changed_checkpoint,
    checkpoint_without,
    decoded_checkpoint,
    encoded_checkpoint,
    markov_state,
    replace,
)
from trafficlab.common.config import (
    FloatBounds,
    GenerationLimits,
    MmppConfig,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointArtifact,
    CheckpointCorruptionError,
    encode_rng_state,
    parse_checkpoint,
    render_checkpoint,
    summarize_generation,
)
from trafficlab.fitting.genetic.evaluation import EvaluationContext, evaluate_candidate, validate_evaluation_context
from trafficlab.fitting.genetic.operators import ReproductionContext, reproduce_child
from trafficlab.fitting.genetic.types import (
    METHOD_ORDER,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
)
from trafficlab.generation.models.common import Genes, make_rng
from trafficlab.generation.models.registry import (
    MMPP_FAMILY,
)


def test_checkpoint_round_trip_is_canonical_and_preserves_frozen_nested_diagnostics() -> None:
    content = render_checkpoint(VALID_STATE)
    loaded = parse_checkpoint(content, COMPATIBILITY)
    assert loaded == VALID_STATE
    assert content.endswith(b"\n")
    decoded = json.loads(content)
    assert content == (json.dumps(decoded, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    assert SCIENTIFIC_ARTIFACT_SCHEMA_VERSION == 5
    assert decoded["scientific_artifact_schema"] == 5
    assert tuple(method.name for method in loaded.population[0].trials[0].methods) == METHOD_ORDER
    with pytest.raises(TypeError):
        cast(dict[str, object], loaded.population[0].trials[0].methods[0].diagnostics)["changed"] = True


def test_checkpoint_round_trip_retains_exact_model_diagnostic_counts() -> None:
    """Checkpoint evidence must preserve owner-derived per-seed model counters."""
    state = markov_state((0.2, 0.7, 0.5, 2, 1.0))

    content = render_checkpoint(state)
    document = decoded_checkpoint(content)
    stored_trial = cast(
        dict[str, object],
        cast(list[object], cast(dict[str, object], cast(list[object], document["population"])[0])["trials"])[0],
    )
    assert stored_trial["model_diagnostics"] == MARKOV_MODEL_DIAGNOSTICS
    loaded = parse_checkpoint(content, state.compatibility)
    assert dict(loaded.population[0].trials[0].model_diagnostics) == MARKOV_MODEL_DIAGNOSTICS
    with pytest.raises(TypeError):
        loaded.population[0].trials[0].model_diagnostics["timing_tier_global_count"] = 4  # type: ignore[index]


def test_checkpoint_rejects_postfit_diagnostics_inside_a_genetic_trial() -> None:
    """Final-only diagnostics must not acquire a second persisted path through trial/checkpoint payloads."""
    document = decoded_checkpoint()
    population = cast(list[object], document["population"])
    candidate = cast(dict[str, object], population[0])
    trials = cast(list[object], candidate["trials"])
    cast(dict[str, object], trials[0])["postfit_diagnostics"] = {}

    with pytest.raises(TrafficlabError, match="postfit_diagnostics"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


@pytest.mark.parametrize(
    "diagnostics",
    [
        [],
        {"counter": True},
        {"counter": -1},
        {"": 1},
        {"invented": 1},
        {"timing_tier_transition_count": 1},
        MARKOV_MODEL_DIAGNOSTICS,
    ],
    ids=("array", "boolean", "negative", "empty-name", "unknown", "partial", "cross-family"),
)
def test_checkpoint_rejects_malformed_model_diagnostic_counts(diagnostics: object) -> None:
    """Loose counter JSON must not enter authoritative candidate evidence."""
    document = decoded_checkpoint()
    population = cast(list[object], document["population"])
    candidate = cast(dict[str, object], population[0])
    trials = cast(list[object], candidate["trials"])
    cast(dict[str, object], trials[0])["model_diagnostics"] = diagnostics

    with pytest.raises(TrafficlabError, match="model diagnostics"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


@pytest.mark.parametrize(
    "diagnostics",
    [
        {},
        {"timing_tier_transition_count": 1},
        {**MARKOV_MODEL_DIAGNOSTICS, "invented": 1},
    ],
    ids=("missing", "partial", "extra-unknown"),
)
def test_checkpoint_rejects_incomplete_or_extended_markov_diagnostic_namespaces(
    diagnostics: dict[str, int],
) -> None:
    state = markov_state((0.2, 0.7, 0.5, 2, 1.0))
    document = decoded_checkpoint(render_checkpoint(state))
    population = cast(list[object], document["population"])
    candidate = cast(dict[str, object], population[0])
    trials = cast(list[object], candidate["trials"])
    cast(dict[str, object], trials[0])["model_diagnostics"] = diagnostics

    with pytest.raises(TrafficlabError, match="model diagnostics"):
        parse_checkpoint(encoded_checkpoint(document), state.compatibility)


def test_repair_failed_offspring_round_trips_without_unvalidated_genes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real repair-invalid child must remain scientific evidence instead of aborting checkpoint publication."""
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 64),
            TraceEvent(1.0, Direction.INBOUND, 128),
            TraceEvent(2.0, Direction.OUTBOUND, 256),
        )
    )
    bounds = MmppConfig(
        crossover_probability=0.8,
        mutation_probability=0.3,
        mutation_scale=0.2,
        q01=FloatBounds(lower=0.1, upper=10.0),
        q10=FloatBounds(lower=0.1, upper=10.0),
        lambda0=FloatBounds(lower=0.1, upper=10.0),
        lambda1=FloatBounds(lower=0.1, upper=10.0),
    )
    evaluation = validate_evaluation_context(
        EvaluationContext(
            reference=reference,
            window=2.0,
            families={"mmpp": MMPP_FAMILY},
            bounds={"mmpp": bounds},
            trial_seeds=(7,),
            trial_limits=GenerationLimits(max_packets=20, max_output_bytes=20_000, max_wall_seconds=2.0),
            similarity=SIMILARITY,
        )
    )
    parent = POPULATION[0]
    other = replace(parent, identifier=CandidateId(birth_generation=0, birth_index=3), genes=(2.0, 3.0, 1.0, 2.0))

    def fail_repair(*_args: object, **_kwargs: object) -> Genes:
        raise TrafficlabError("offspring repair failed", corrective_action="retain invalid evidence")

    monkeypatch.setattr(type(MMPP_FAMILY), "repair", fail_repair)
    rng = make_rng(35)
    child = reproduce_child(
        parent,
        other,
        context=ReproductionContext(
            reference=reference,
            family_bounds={"mmpp": bounds},
            family_priority=("mmpp",),
            duplicate_mutation_attempts=1,
        ),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=rng,
    )
    evaluated_child = evaluate_candidate(child, evaluation)
    current_population = (parent, evaluated_child, POPULATION[2])
    history = VALID_STATE.history + summarize_generation(
        1,
        current_population,
        ("mmpp", "poisson_empirical"),
        family_priority=VALID_STATE.family_priority,
    )
    state = replace(
        VALID_STATE,
        generation=1,
        population=current_population,
        history=history,
        rng_state=encode_rng_state(rng),
        consecutive_stagnation=1,
    )

    loaded = parse_checkpoint(render_checkpoint(state), COMPATIBILITY)
    stored = next(
        candidate
        for candidate in loaded.population
        if candidate.identifier == CandidateId(birth_generation=1, birth_index=0)
    )
    assert (stored.status, stored.genes, stored.fitness, stored.trials) == ("invalid", None, 0.0, ())
    assert stored.invalid == CandidateFailure(
        kind="repair",
        seed=None,
        detail="offspring repair failed",
        stage="fit",
        affected_evidence="candidate genes",
        evidence_state="diagnostic_only",
        corrective_action="retain invalid evidence",
        authority="primary",
    )
    assert loaded.history[-1].valid_count == 2


def test_checkpoint_round_trip_preserves_candidate_failure_scientific_diagnostics() -> None:
    """Candidate-invalid provenance is exact checkpoint evidence, not an in-memory-only detail."""
    failure = CandidateFailure(
        kind="incomplete_generation",
        seed=7,
        detail="max_packets",
        stage="generate",
        affected_evidence="candidate trace",
        evidence_state="not_published",
        corrective_action="increase generation limits or repair the candidate model",
        authority="primary",
    )
    state = replace(VALID_STATE, population=(POPULATION[0], replace(POPULATION[1], invalid=failure), POPULATION[2]))

    document = decoded_checkpoint(render_checkpoint(state))
    assert cast(list[dict[str, object]], document["population"])[1]["invalid"] == {
        "kind": "incomplete_generation",
        "seed": 7,
        "detail": "max_packets",
        "stage": "generate",
        "affected_evidence": "candidate trace",
        "evidence_state": "not_published",
        "corrective_action": "increase generation limits or repair the candidate model",
        "authority": "primary",
    }
    assert parse_checkpoint(render_checkpoint(state), COMPATIBILITY).population[1].invalid == failure

    del cast(dict[str, object], cast(list[dict[str, object]], document["population"])[1]["invalid"])["authority"]
    with pytest.raises(TrafficlabError, match="invalid.*authority"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


@pytest.mark.parametrize(
    ("kind", "seed", "detail"),
    [
        ("repair", None, "legacy repair"),
        ("fit", None, "legacy fit"),
        ("generation", 7, "legacy generation"),
        ("incomplete_generation", 7, "legacy incomplete generation"),
        ("similarity_precondition", 7, "legacy similarity precondition"),
        ("nonfinite_score", 7, "legacy nonfinite score"),
    ],
)
def test_schema_v5_checkpoint_rejects_incomplete_legacy_candidate_failure(
    kind: str,
    seed: int | None,
    detail: str,
) -> None:
    """Current checkpoints cannot silently upgrade incomplete failure semantics."""
    document = decoded_checkpoint()
    invalid = cast(dict[str, object], cast(list[dict[str, object]], document["population"])[1]["invalid"])
    invalid.clear()
    invalid.update({"kind": kind, "seed": seed, "detail": detail})

    with pytest.raises(TrafficlabError, match="invalid checkpoint"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


def test_checkpoint_rejects_wrong_bit_generator_and_duplicate_candidate_ids() -> None:
    with pytest.raises(TrafficlabError, match="bit_generator"):
        parse_checkpoint(changed_checkpoint(("rng", "state", "bit_generator"), "Philox"), COMPATIBILITY)

    duplicate = decoded_checkpoint()
    population = cast(list[dict[str, object]], duplicate["population"])
    population[1]["identifier"] = population[0]["identifier"]
    with pytest.raises(TrafficlabError, match="duplicate candidate"):
        parse_checkpoint(encoded_checkpoint(duplicate), COMPATIBILITY)


@pytest.mark.parametrize(
    "content",
    [
        candidate_update(extra=1),
        candidate_update(fitness=True),
        candidate_update(fitness=math.nan),
        candidate_update(trials={}),
    ],
)
def test_checkpoint_rejects_nested_shape_type_and_number_errors(content: bytes) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(content, COMPATIBILITY)


def test_checkpoint_rejects_duplicate_json_keys() -> None:
    content = render_checkpoint(VALID_STATE)
    duplicate_key = content.replace(b'{\n  "best":', b'{\n  "best": null,\n  "best":', 1)
    with pytest.raises(TrafficlabError, match="duplicate JSON key"):
        parse_checkpoint(duplicate_key, COMPATIBILITY)


def test_checkpoint_parse_normalizes_pydantic_errors_without_input_or_url() -> None:
    """Persisted attacker-controlled values and Pydantic URLs must not enter stable diagnostics."""
    document = decoded_checkpoint()
    candidate = cast(dict[str, object], cast(list[object], document["population"])[0])
    candidate["fitness"] = {"DO_NOT_LEAK_SECRET": True}

    with pytest.raises(CheckpointCorruptionError) as captured:
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)

    assert str(captured.value) == (
        "invalid checkpoint: population.0.valid.fitness: Value error, value must be an exact float [value_error]"
    )
    assert "DO_NOT_LEAK_SECRET" not in str(captured.value)
    assert "pydantic.dev" not in str(captured.value)


def test_checkpoint_render_normalizes_pydantic_errors_without_input_or_url() -> None:
    """Invalid nested runtime instances must produce the same stable boundary diagnostic shape."""
    poisoned = DuplicateDiagnostic.model_construct(
        attempt=0,
        outcome="duplicate",
        detail=cast(Any, {"DO_NOT_LEAK_SECRET": True}),
    )
    candidate = replace(POPULATION[0], duplicate_diagnostics=(poisoned,))
    state = replace(VALID_STATE, population=(candidate, *POPULATION[1:]))

    with pytest.raises(CheckpointCorruptionError) as captured:
        render_checkpoint(state)

    assert str(captured.value) == (
        "invalid checkpoint: population.0.valid.duplicate_diagnostics.0.detail: "
        "Input should be a valid string [string_type]"
    )
    assert "DO_NOT_LEAK_SECRET" not in str(captured.value)
    assert "pydantic.dev" not in str(captured.value)


def test_checkpoint_render_rejects_schema_validation_that_changes_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema codec that normalizes canonical values must fail before publication."""

    class ChangedArtifact:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {}

    def changed_document(_cls: type[CheckpointArtifact], _value: object) -> ChangedArtifact:
        return ChangedArtifact()

    monkeypatch.setattr(CheckpointArtifact, "model_validate", classmethod(changed_document))

    with pytest.raises(CheckpointCorruptionError, match="schema validation changed"):
        render_checkpoint(VALID_STATE)


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"{",
        b"[]\n",
        b'{"experiment_identity":NaN}\n',
        checkpoint_without(("capture_identity",)),
        encoded_checkpoint({**decoded_checkpoint(), "unknown": 1}),
    ],
)
def test_checkpoint_rejects_encoding_syntax_root_shape_and_exact_key_errors(content: bytes) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(content, COMPATIBILITY)


def test_checkpoint_parser_rejects_non_bytes_before_json_parsing() -> None:
    with pytest.raises(TypeError, match="checkpoint content must be bytes"):
        parse_checkpoint(cast(bytes, "not bytes"), COMPATIBILITY)


def test_checkpoint_rejects_noncanonical_but_equivalent_json() -> None:
    canonical = render_checkpoint(VALID_STATE)
    data = json.loads(canonical)
    noncanonical = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with pytest.raises(TrafficlabError, match="canonical"):
        parse_checkpoint(noncanonical, COMPATIBILITY)
