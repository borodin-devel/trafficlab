"""Direct compatibility checkpoint behavior tests."""

import json
import math
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from tests.support.checkpoint import (
    COMPATIBILITY,
    FAMILIES,
    GENETIC,
    SIMILARITY,
    VALID_STATE,
    changed_checkpoint,
    decoded_checkpoint,
    encoded_checkpoint,
    replace,
)
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import (
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointCorruptionError,
    Pcg64CoreState,
    RngState,
    decode_rng_state,
    encode_rng_state,
    load_checkpoint,
    parse_checkpoint,
    render_checkpoint,
    validate_compatibility,
)
from trafficlab.fitting.genetic.checkpoint.compatibility import parse_family_name
from trafficlab.fitting.genetic.checkpoint.schema import FamilyCheckpointSpec
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.generation.models.common import make_rng


def test_rng_state_round_trip_reproduces_all_next_primitives() -> None:
    rng = make_rng(73)
    _ = (rng.random(), rng.integers(0, 9, endpoint=False), rng.normal(0.0, 0.1))
    clone = decode_rng_state(encode_rng_state(rng))
    assert (clone.random(), int(clone.integers(0, 9, endpoint=False)), clone.normal(0.0, 0.1)) == (
        rng.random(),
        int(rng.integers(0, 9, endpoint=False)),
        rng.normal(0.0, 0.1),
    )


def test_rng_codec_requires_the_exact_named_generator_and_bit_generator() -> None:
    current = make_rng(0)
    assert encode_rng_state(decode_rng_state(encode_rng_state(current))) == encode_rng_state(current)

    with pytest.raises(TrafficlabError, match="PCG64"):
        encode_rng_state(np.random.Generator(np.random.Philox(0)))


@pytest.mark.parametrize("family_name", ("nhpp", "acd", "markov_packet_train"))
def test_checkpoint_parser_accepts_each_new_registered_family_name(family_name: str) -> None:
    """A fresh or resumed GA state needs the current registered-name boundary."""
    assert parse_family_name(family_name, name="family name") == family_name


def test_fresh_and_resumed_checkpoint_compatibility_accept_nhpp_metadata() -> None:
    """An explicit NHPP GA configuration must be valid both before and during resume comparison."""
    nhpp = FamilyCheckpointSpec(
        name="nhpp",
        gene_order=("bin_count",),
        coordinates=(GeneCoordinate("bin_count", "integer", IntegerBounds(lower=2, upper=4)),),
        crossover_probability=0.9,
        mutation_probability=1.0,
        mutation_scale=0.1,
    )
    fresh = replace(COMPATIBILITY, families=(nhpp,), family_priority=("nhpp",))

    validate_compatibility(fresh, fresh)


def test_fresh_and_resumed_checkpoint_compatibility_accept_acd_metadata() -> None:
    """An explicit ACD GA configuration must be valid both before and during resume comparison."""
    acd = FamilyCheckpointSpec(
        name="acd",
        gene_order=("order",),
        coordinates=(GeneCoordinate("order", "integer", IntegerBounds(lower=1, upper=3)),),
        crossover_probability=0.9,
        mutation_probability=1.0,
        mutation_scale=0.1,
    )
    fresh = replace(COMPATIBILITY, families=(acd,), family_priority=("acd",))

    validate_compatibility(fresh, fresh)


def test_fresh_and_resumed_checkpoint_compatibility_accept_packet_train_metadata() -> None:
    """The capped-length coordinate must survive strict checkpoint validation."""
    packet_train = FamilyCheckpointSpec(
        name="markov_packet_train",
        gene_order=("length_cap",),
        coordinates=(GeneCoordinate("length_cap", "integer", IntegerBounds(lower=3, upper=8)),),
        crossover_probability=0.9,
        mutation_probability=1.0,
        mutation_scale=0.1,
    )
    fresh = replace(
        COMPATIBILITY,
        families=(packet_train,),
        family_priority=("markov_packet_train",),
    )

    validate_compatibility(fresh, fresh)


@pytest.mark.parametrize(
    ("present", "value"),
    (
        (False, None),
        (True, None),
        (True, 4),
        (True, 6),
        (True, True),
        (True, "2"),
        (True, 2.0),
    ),
    ids=("missing", "null", "schema-4", "future", "boolean", "string", "nonintegral"),
)
def test_checkpoint_rejects_noncurrent_scientific_schema_before_rng_decode(
    present: bool,
    value: object,
) -> None:
    """Older scientific semantics must fail before malformed RNG state can be reconstructed."""
    document = decoded_checkpoint()
    document["rng"] = {"deliberately": "unreadable"}
    population = cast(list[object], document["population"])
    candidate = cast(dict[str, object], population[0])
    trials = cast(list[object], candidate["trials"])
    cast(dict[str, object], trials[0])["model_diagnostics"] = {"invented": 1}
    if present:
        document["scientific_artifact_schema"] = value
    else:
        document.pop("scientific_artifact_schema", None)
    with pytest.raises(TrafficlabError, match="checkpoint schema is incompatible"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


def test_schema_four_checkpoint_rejection_requires_a_fresh_refit_directory() -> None:
    document = decoded_checkpoint()
    document["scientific_artifact_schema"] = 4

    with pytest.raises(TrafficlabError, match="checkpoint schema is incompatible") as captured:
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)

    assert captured.value.corrective_action == "refit under the current schema in a new run directory"


@pytest.mark.parametrize(
    ("value", "case"),
    (
        (None, "null"),
        ("mmpp", "string"),
        (["mmpp"], "missing"),
        (["mmpp", "mmpp"], "duplicate"),
        (["mmpp", "markov_renewal"], "foreign"),
        (["poisson_empirical", "mmpp"], "reordered"),
    ),
)
def test_checkpoint_priority_is_strict_and_rejected_before_rng_parsing(
    value: object,
    case: str,
) -> None:
    """Priority is scientific compatibility, never a permissive resume hint."""
    document = decoded_checkpoint()
    assert document["family_priority"] == list(COMPATIBILITY.family_priority)
    document["family_priority"] = value
    with pytest.raises(TrafficlabError, match="priority|checkpoint"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


def test_checkpoint_rejects_missing_or_expected_mismatched_priority_before_rng_parsing() -> None:
    """Schema-v3 checkpoints without priority have no migration path."""
    document = decoded_checkpoint()
    assert document["family_priority"] == list(COMPATIBILITY.family_priority)
    missing = dict(document)
    del missing["family_priority"]
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(encoded_checkpoint(missing), COMPATIBILITY)
    expected = replace(COMPATIBILITY, family_priority=tuple(reversed(COMPATIBILITY.family_priority)))
    with pytest.raises(TrafficlabError, match="family priority"):
        parse_checkpoint(encoded_checkpoint(document), expected)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("reference_identity", "sha256"), "A" * 64),
        (("capture_identity", "sha256"), "short"),
        (("trial_limits", "max_packets"), True),
        (("trial_limits", "max_output_bytes"), 0),
        (("trial_limits", "max_wall_seconds"), 3),
        (("observation_window_seconds",), 2),
        (("observation_window_seconds",), 0.0),
        (("trial_seeds",), []),
        (("trial_seeds",), [7, 7]),
        (("trial_seeds",), [True]),
        (("families",), []),
        (("families", 0, "name"), "unknown"),
        (("families", 0, "gene_order"), []),
        (("families", 0, "gene_order"), ["q01", "q01", "lambda0", "lambda1"]),
        (("families", 0, "coordinates"), {}),
        (("families", 0, "coordinates", 0, "kind"), "curved"),
        (("families", 0, "coordinates", 0, "lower"), 0),
        (("families", 0, "coordinates", 0, "upper"), math.inf),
        (("families", 0, "operators", "mutation_probability"), True),
        (("families", 0, "operators", "mutation_scale"), 0.0),
        (("genetic", "population_size"), True),
        (("genetic", "tournament_size"), 4),
        (("genetic", "elite_count"), 3),
        (("genetic", "early_stopping_generations"), 3),
        (("genetic", "early_stopping_tolerance"), 0),
        (("genetic", "resume"), 1),
        (("similarity", "method_weights", "frame_size_ks"), 1),
        (("similarity", "method_weights", "frame_size_ks"), 0.5),
        (("similarity", "acf_lags"), [True]),
        (("similarity", "multiscale_widths_seconds"), [0.0]),
        (("rng", "engine"), "other"),
        (("rng", "python_version"), ""),
        (("rng", "state", "bit_generator"), "Philox"),
        (("rng", "state", "state", "state"), -1),
        (("rng", "state", "state", "inc"), 2**128),
        (("rng", "state", "has_uint32"), True),
        (("rng", "state", "uinteger"), 2**32),
    ],
)
def test_checkpoint_rejects_strict_compatibility_metadata_and_rng_corruption(
    path: tuple[str | int, ...], value: object
) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(changed_checkpoint(path, value), COMPATIBILITY)


def test_rng_codec_rejects_foreign_generators_and_malformed_pcg64_state() -> None:
    for rng in (object(), np.random.Generator(np.random.Philox(73))):
        with pytest.raises(TrafficlabError, match="checkpoint"):
            encode_rng_state(rng)

    with pytest.raises(TrafficlabError, match="rng state"):
        decode_rng_state(cast(Any, None))
    malformed = RngState.model_construct(
        bit_generator="PCG64",
        state=Pcg64CoreState.model_construct(state=-1, inc=1),
        has_uint32=0,
        uinteger=0,
    )
    with pytest.raises(TrafficlabError, match="state.state"):
        decode_rng_state(malformed)


def test_compatibility_reports_each_scientifically_relevant_difference_specifically() -> None:
    renamed_coordinate = replace(FAMILIES[0].coordinates[0], name="renamed")
    renamed_family = replace(
        FAMILIES[0],
        gene_order=("renamed", *FAMILIES[0].gene_order[1:]),
        coordinates=(renamed_coordinate, *FAMILIES[0].coordinates[1:]),
    )
    wider_coordinate = replace(
        FAMILIES[0].coordinates[0],
        bounds=FloatBounds(lower=0.05, upper=10.0),
    )
    wider_family = replace(FAMILIES[0], coordinates=(wider_coordinate, *FAMILIES[0].coordinates[1:]))
    changed_operator = replace(FAMILIES[0], mutation_probability=0.4)
    reordered_family = replace(
        FAMILIES[0],
        gene_order=tuple(reversed(FAMILIES[0].gene_order)),
        coordinates=tuple(reversed(FAMILIES[0].coordinates)),
    )
    changed_similarity = SIMILARITY.model_copy(update={"iat_diagnostic_quantile": 0.6})
    cases = (
        (
            replace(COMPATIBILITY, experiment_identity=ContentIdentity(size=101, sha256="d" * 64)),
            "experiment snapshot SHA-256",
        ),
        (
            replace(COMPATIBILITY, reference_identity=ContentIdentity(size=102, sha256="d" * 64)),
            "reference SHA-256",
        ),
        (
            replace(COMPATIBILITY, capture_identity=ContentIdentity(size=103, sha256="d" * 64)),
            "capture SHA-256",
        ),
        (
            replace(COMPATIBILITY, reference_identity=ContentIdentity(size=999, sha256="b" * 64)),
            "reference SHA-256",
        ),
        (replace(COMPATIBILITY, observation_window_seconds=3.0), "observation window"),
        (replace(COMPATIBILITY, trial_seeds=(8,)), "trial seeds"),
        (
            replace(
                COMPATIBILITY,
                trial_limits=GenerationLimits(max_packets=1_001, max_output_bytes=2_000, max_wall_seconds=3.0),
            ),
            "trial generation limits",
        ),
        (
            replace(
                COMPATIBILITY,
                families=(FAMILIES[1],),
                family_priority=("poisson_empirical",),
            ),
            "family names",
        ),
        (replace(COMPATIBILITY, families=(reordered_family, FAMILIES[1])), "gene order"),
        (replace(COMPATIBILITY, families=(renamed_family, FAMILIES[1])), "gene order"),
        (replace(COMPATIBILITY, families=(wider_family, FAMILIES[1])), "coordinate metadata"),
        (replace(COMPATIBILITY, families=(changed_operator, FAMILIES[1])), "operator values"),
        (replace(COMPATIBILITY, genetic=replace(GENETIC, final_seed=102)), "genetic setting final_seed"),
        (replace(COMPATIBILITY, similarity=changed_similarity), "similarity settings"),
        (replace(COMPATIBILITY, python_version="0.0.0"), "Python version"),
    )
    for changed, match in cases:
        with pytest.raises(TrafficlabError, match=match):
            validate_compatibility(COMPATIBILITY, changed)


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("cvm_iat_weight", 0.4),
        ("cvm_size_weight", 0.6),
        ("ad_iat_weight", 0.6),
        ("ad_size_weight", 0.4),
        ("js_iat_bin_count", 9),
        ("js_iat_weight", 0.5),
        ("js_mark_weight", 0.5),
        ("mmd_feature_count", 13),
        ("mmd_seed", 43),
        ("mmd_scale_floor", 0.02),
    ],
)
def test_resume_rejects_every_changed_schema_five_similarity_setting(
    field_name: str, changed_value: float | int
) -> None:
    """Every new setting changes candidate scores and therefore belongs to resume identity."""
    changed = SIMILARITY.model_copy(update={field_name: changed_value})

    with pytest.raises(TrafficlabError, match="similarity settings"):
        validate_compatibility(COMPATIBILITY, replace(COMPATIBILITY, similarity=changed))

    with pytest.raises(TrafficlabError, match="invalid checkpoint"):
        validate_compatibility(COMPATIBILITY, cast(Any, None))


def test_render_rejects_malformed_family_genetic_and_compatibility_instances() -> None:
    mmpp = FAMILIES[0]
    bad_coordinate_kind = replace(mmpp.coordinates[0], kind=cast(Any, "curved"))
    bad_integer_bounds = replace(mmpp.coordinates[0], kind="integer")
    bad_continuous_bounds = replace(
        mmpp.coordinates[0],
        bounds=cast(Any, IntegerBounds(lower=1, upper=2)),
    )
    bad_log_lower = replace(mmpp.coordinates[0], bounds=FloatBounds(lower=-1.0, upper=2.0))
    family_cases: tuple[Any, ...] = (
        None,
        replace(mmpp, gene_order=cast(Any, [])),
        replace(mmpp, gene_order=("", *mmpp.gene_order[1:])),
        replace(mmpp, gene_order=("q01", "q01", "lambda0", "lambda1")),
        replace(mmpp, coordinates=cast(Any, [])),
        replace(mmpp, coordinates=(cast(Any, None), *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_coordinate_kind, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_integer_bounds, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_continuous_bounds, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_log_lower, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=mmpp.coordinates[:-1]),
        replace(mmpp, gene_order=("renamed", *mmpp.gene_order[1:])),
        replace(mmpp, crossover_probability=cast(Any, True)),
        replace(mmpp, mutation_scale=0.0),
    )
    for family in family_cases:
        compatibility = replace(COMPATIBILITY, families=cast(Any, (family, FAMILIES[1])))
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(replace(VALID_STATE, compatibility=compatibility))

    genetic_cases: tuple[Any, ...] = (
        None,
        replace(GENETIC, tournament_size=4),
        replace(GENETIC, elite_count=3),
        replace(GENETIC, population_size=2),
        replace(GENETIC, early_stopping_generations=3),
        replace(GENETIC, final_seed=7),
    )
    for genetic in genetic_cases:
        compatibility = replace(COMPATIBILITY, genetic=genetic)
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(replace(VALID_STATE, compatibility=compatibility))

    compatibility_cases: tuple[Any, ...] = (
        None,
        replace(COMPATIBILITY, reference_identity=cast(Any, None)),
        replace(COMPATIBILITY, trial_seeds=cast(Any, [])),
        replace(COMPATIBILITY, trial_seeds=()),
        replace(COMPATIBILITY, trial_seeds=(7, 7)),
        replace(COMPATIBILITY, trial_limits=cast(Any, None)),
        replace(COMPATIBILITY, families=cast(Any, [])),
        replace(COMPATIBILITY, families=()),
        replace(COMPATIBILITY, families=tuple(reversed(FAMILIES))),
        replace(COMPATIBILITY, families=(FAMILIES[0], FAMILIES[0])),
        replace(COMPATIBILITY, similarity=cast(Any, None)),
        replace(COMPATIBILITY, python_version=""),
        replace(COMPATIBILITY, rng_engine=cast(Any, "other")),
    )
    for compatibility in compatibility_cases:
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(replace(VALID_STATE, compatibility=compatibility))


def test_experiment_hash_mismatch_precedes_redundant_operator_mismatch() -> None:
    data = decoded_checkpoint()
    data["experiment_identity"] = {"size": 101, "sha256": "d" * 64}
    operators = cast(dict[str, object], cast(list[dict[str, object]], data["families"])[0]["operators"])
    operators["mutation_probability"] = 0.4
    with pytest.raises(TrafficlabError, match="experiment snapshot SHA-256"):
        parse_checkpoint(encoded_checkpoint(data), COMPATIBILITY)


def test_experiment_hash_mismatch_precedes_rng_engine_and_engine_mismatch_is_specific() -> None:
    engine_only = decoded_checkpoint()
    cast(dict[str, object], engine_only["rng"])["engine"] = "alternate.random/PCG64"
    with pytest.raises(TrafficlabError, match="RNG engine"):
        parse_checkpoint(encoded_checkpoint(engine_only), COMPATIBILITY)

    engine_and_experiment = cast(dict[str, object], json.loads(encoded_checkpoint(engine_only)))
    engine_and_experiment["experiment_identity"] = {"size": 101, "sha256": "d" * 64}
    with pytest.raises(TrafficlabError, match="experiment snapshot SHA-256"):
        parse_checkpoint(encoded_checkpoint(engine_and_experiment), COMPATIBILITY)


@pytest.mark.parametrize(
    "engine",
    (
        None,
        True,
        7,
        {},
        "",
        " ",
        "numpy.random.Generator//PCG64",
        "/PCG64",
        "numpy.random.Generator/",
        "numpy.random.Generator /PCG64",
        "numpy-random/PCG64",
        "numpy.random.Generator\\PCG64",
        "nümpy.random/PCG64",
    ),
    ids=(
        "null",
        "boolean",
        "integer",
        "object",
        "empty",
        "whitespace",
        "double-separator",
        "leading-separator",
        "trailing-separator",
        "embedded-whitespace",
        "punctuation",
        "backslash",
        "non-ascii",
    ),
)
def test_malformed_rng_engine_is_corruption_not_experiment_incompatibility(engine: object) -> None:
    document = decoded_checkpoint()
    cast(dict[str, object], document["rng"])["engine"] = engine

    with pytest.raises(CheckpointCorruptionError, match="rng.engine"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


def test_missing_rng_engine_is_corruption_not_experiment_incompatibility() -> None:
    document = decoded_checkpoint()
    del cast(dict[str, object], document["rng"])["engine"]

    with pytest.raises(CheckpointCorruptionError, match="rng.engine"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


def test_nonobject_rng_record_is_corruption_not_experiment_incompatibility() -> None:
    document = decoded_checkpoint()
    document["rng"] = []

    with pytest.raises(CheckpointCorruptionError, match="rng"):
        parse_checkpoint(encoded_checkpoint(document), COMPATIBILITY)


def test_operator_mismatch_is_specific_and_checkpoint_is_not_rewritten(tmp_path: Path) -> None:
    data = decoded_checkpoint()
    operators = cast(dict[str, object], cast(list[dict[str, object]], data["families"])[0]["operators"])
    operators["mutation_probability"] = 0.4
    path = tmp_path / "checkpoint.json"
    path.write_bytes(encoded_checkpoint(data))
    before = path.read_bytes()
    with pytest.raises(TrafficlabError, match="operator values for family mmpp"):
        load_checkpoint(path, COMPATIBILITY)
    assert path.read_bytes() == before
