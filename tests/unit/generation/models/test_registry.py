"""Behavioral tests for the closed model registry and strict best-model JSON."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

import trafficlab.generation.models.fitted_model as registry_module
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import (
    AcdConfig,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovPacketTrainConfig,
    MarkovRenewalConfig,
    MmppConfig,
    NhppConfig,
    PacketHmmConfig,
    PoissonConfig,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.common import ModelFamily
from trafficlab.generation.models.fitted_model import (
    BestModel,
    load_best_model,
    make_best_model,
    rebuild_best_model,
    render_best_model,
)
from trafficlab.generation.models.registry import (
    ACD_FAMILY,
    MARKOV_PACKET_TRAIN_FAMILY,
    MARKOV_RENEWAL_FAMILY,
    MMPP_FAMILY,
    NHPP_FAMILY,
    PACKET_HMM_FAMILY,
    POISSON_FAMILY,
    REGISTRY,
    get_family,
)

REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 100),
        TraceEvent(3.0, Direction.OUTBOUND, 140),
        TraceEvent(6.0, Direction.INBOUND, 80),
        TraceEvent(10.0, Direction.OUTBOUND, 60),
    )
)
WINDOW = 10.0
POISSON_BOUNDS = PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0))
MARKOV_BOUNDS = MarkovRenewalConfig(
    q1=FloatBounds(lower=0.1, upper=0.4),
    q2=FloatBounds(lower=0.6, upper=0.9),
    alpha=FloatBounds(lower=0.0, upper=2.0),
    r=IntegerBounds(lower=1, upper=8),
    c_t=FloatBounds(lower=0.25, upper=4.0),
)
MMPP_BOUNDS = MmppConfig(
    q01=FloatBounds(lower=0.01, upper=10.0),
    q10=FloatBounds(lower=0.01, upper=10.0),
    lambda0=FloatBounds(lower=0.01, upper=100.0),
    lambda1=FloatBounds(lower=0.1, upper=1000.0),
)
NHPP_BOUNDS = NhppConfig(bin_count=IntegerBounds(lower=2, upper=4))
ACD_BOUNDS = AcdConfig(order=IntegerBounds(lower=1, upper=3))
PACKET_TRAIN_BOUNDS = MarkovPacketTrainConfig(length_cap=IntegerBounds(lower=3, upper=8))
PACKET_HMM_BOUNDS = PacketHmmConfig(state_count=IntegerBounds(lower=2, upper=4))
SEED_POLICY = {
    "empirical": "choice_scalar_index",
    "exponential": "exponential_scale_inverse_rate",
    "generator": "numpy.random.Generator/PCG64",
    "weighted": "random_cumulative",
}
REFERENCE_IDENTITY = ContentIdentity(size=1_024, sha256="a" * 64)
CAPTURE_IDENTITY = ContentIdentity(size=256, sha256="b" * 64)
FINAL_SEED = 17
FINAL_LIMITS = GenerationLimits(max_packets=10_000, max_output_bytes=10_000_000, max_wall_seconds=30.0)


def test_registry_is_closed_and_stably_ordered() -> None:
    """Dynamic or replaceable registry entries would make fitted artifacts non-reproducible."""
    assert type(REGISTRY) is MappingProxyType
    assert tuple(REGISTRY) == (
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
        "nhpp",
        "acd",
        "markov_packet_train",
        "packet_hmm",
    )
    assert REGISTRY["poisson_empirical"] is POISSON_FAMILY
    assert REGISTRY["nhpp"] is NHPP_FAMILY
    assert REGISTRY["acd"] is ACD_FAMILY
    assert REGISTRY["markov_packet_train"] is MARKOV_PACKET_TRAIN_FAMILY
    assert REGISTRY["packet_hmm"] is PACKET_HMM_FAMILY
    with pytest.raises(TypeError):
        REGISTRY["plugin.family"] = POISSON_FAMILY  # type: ignore[index]
    with pytest.raises(TrafficlabError, match="unknown model family"):
        get_family("plugin.family")


def test_existing_families_declare_coordinate_kinds() -> None:
    """Coordinate transforms belong to family contracts, not registry identity checks."""
    assert POISSON_FAMILY.gene_coordinate_kinds == ("log",)
    assert MARKOV_RENEWAL_FAMILY.gene_coordinate_kinds == (
        "linear",
        "linear",
        "linear",
        "integer",
        "log",
    )
    assert MMPP_FAMILY.gene_coordinate_kinds == ("log", "log", "log", "log")
    assert NHPP_FAMILY.gene_coordinate_kinds == ("integer",)
    assert ACD_FAMILY.gene_coordinate_kinds == ("integer",)
    assert MARKOV_PACKET_TRAIN_FAMILY.gene_coordinate_kinds == ("integer",)
    assert PACKET_HMM_FAMILY.gene_coordinate_kinds == ("integer",)


@pytest.fixture
def valid_best_model() -> BestModel:
    return make_best_model(
        POISSON_FAMILY,
        REFERENCE,
        (1.0,),
        reference_identity=REFERENCE_IDENTITY,
        capture_identity=CAPTURE_IDENTITY,
        final_seed=FINAL_SEED,
        final_limits=FINAL_LIMITS,
        W=WINDOW,
        bounds=POISSON_BOUNDS,
    )


def _document(model: BestModel) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(render_best_model(model)))


def _encoded(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, allow_nan=True) + "\n").encode()


def test_make_best_model_repairs_and_owns_all_outer_metadata() -> None:
    """Allowing callers to repeat derived metadata would let it drift from the real family and bounds."""
    artifact = make_best_model(
        POISSON_FAMILY,
        REFERENCE,
        (99.0,),
        reference_identity=REFERENCE_IDENTITY,
        capture_identity=CAPTURE_IDENTITY,
        final_seed=FINAL_SEED,
        final_limits=FINAL_LIMITS,
        W=WINDOW,
        bounds=POISSON_BOUNDS,
    )
    assert artifact.version == 1
    assert artifact.scientific_artifact_schema == 5
    assert artifact.family == "poisson_empirical"
    assert artifact.genes == (4.0,)
    assert artifact.reference_identity == REFERENCE_IDENTITY
    assert artifact.capture_identity == CAPTURE_IDENTITY
    assert artifact.reference_sha256 == REFERENCE_IDENTITY.sha256
    assert artifact.capture_sha256 == CAPTURE_IDENTITY.sha256
    assert artifact.final_seed == FINAL_SEED
    assert artifact.final_limits == FINAL_LIMITS
    assert artifact.observation_window_seconds == WINDOW
    assert artifact.gene_bounds == {"c_lambda": FloatBounds(lower=0.25, upper=4.0)}
    assert artifact.estimator_choices == {
        "first_event": "zero",
        "marks": "joint_empirical_first_appearance",
        "rate": "interval_count_over_window",
    }
    assert artifact.seed_policy == SEED_POLICY


def test_best_model_render_is_canonical(valid_best_model: BestModel) -> None:
    """Noncanonical JSON would make byte identity depend on the writing caller."""
    rendered = render_best_model(valid_best_model)
    assert rendered.endswith(b"\n")
    document = json.loads(rendered)
    assert set(document) == {
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
    assert rendered == (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    loaded = load_best_model(rendered, source=Path("best_model.json"))
    assert type(loaded.genes) is tuple
    assert render_best_model(loaded) == rendered


def test_best_model_loader_rejects_compact_equivalent_json(valid_best_model: BestModel) -> None:
    document = json.loads(render_best_model(valid_best_model))
    compact = (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()

    with pytest.raises(TrafficlabError, match="not canonical"):
        load_best_model(compact, source=Path("best_model.json"))


@pytest.mark.parametrize(
    ("family", "genes", "bounds"),
    [
        (POISSON_FAMILY, (1.0,), POISSON_BOUNDS),
        (MARKOV_RENEWAL_FAMILY, (0.25, 0.75, 0.5, 2.0, 1.0), MARKOV_BOUNDS),
        (MMPP_FAMILY, (1.0, 2.0, 3.0, 8.0), MMPP_BOUNDS),
        (NHPP_FAMILY, (2,), NHPP_BOUNDS),
        (ACD_FAMILY, (1,), ACD_BOUNDS),
        (MARKOV_PACKET_TRAIN_FAMILY, (3,), PACKET_TRAIN_BOUNDS),
        (PACKET_HMM_FAMILY, (2,), PACKET_HMM_BOUNDS),
    ],
    ids=("poisson_empirical", "markov_renewal", "mmpp", "nhpp", "acd", "markov_packet_train", "packet_hmm"),
)
def test_best_model_round_trips_every_real_fitted_family(
    family: object, genes: tuple[float, ...], bounds: object
) -> None:
    """A registry entry without its real fitted codec would fail only after a winning search."""
    artifact = make_best_model(
        family,  # type: ignore[arg-type]
        REFERENCE,
        genes,
        reference_identity=REFERENCE_IDENTITY,
        capture_identity=CAPTURE_IDENTITY,
        final_seed=FINAL_SEED,
        final_limits=FINAL_LIMITS,
        W=WINDOW,
        bounds=bounds,  # type: ignore[arg-type]
    )
    loaded = load_best_model(render_best_model(artifact), source=Path("best_model.json"))
    assert loaded == artifact


def test_best_model_loader_translates_huge_acd_coefficients_to_stable_domain_error() -> None:
    """A finite but individually nonstationary coefficient must not escape as an OverflowError."""
    artifact = make_best_model(
        ACD_FAMILY,
        REFERENCE,
        (1,),
        reference_identity=REFERENCE_IDENTITY,
        capture_identity=CAPTURE_IDENTITY,
        final_seed=FINAL_SEED,
        final_limits=FINAL_LIMITS,
        W=WINDOW,
        bounds=ACD_BOUNDS,
    )
    document = _document(artifact)
    fitted = cast(dict[str, object], document["fitted"])
    maximum = math.nextafter(math.inf, 0.0)
    fitted["alpha"] = [maximum]
    fitted["beta"] = [maximum]

    with pytest.raises(TrafficlabError, match="invalid fitted acd model") as error:
        load_best_model(_encoded(document), source=Path("best_model.json"))
    assert "ACD coefficients" in str(error.value)


def test_best_model_rejects_duplicate_keys_at_every_object_depth(valid_best_model: BestModel) -> None:
    """Last-value-wins parsing at any nesting level would bypass exact artifact validation."""
    rendered = render_best_model(valid_best_model).decode()
    duplicates = (
        rendered.replace("{", '{\n  "version": 1,', 1),
        rendered.replace('"lower": 0.25,', '"lower": 0.25,\n      "lower": 0.25,', 1),
        rendered.replace('"direction": "outbound",', '"direction": "outbound",\n        "direction": "outbound",', 1),
    )
    for content in duplicates:
        with pytest.raises(TrafficlabError, match="duplicate JSON key"):
            load_best_model(content.encode(), source=Path("best_model.json"))


def test_best_model_rejects_every_missing_and_extra_outer_key(valid_best_model: BestModel) -> None:
    """Ignoring unknown or defaulting absent outer metadata would make lineage incomplete."""
    original = _document(valid_best_model)
    for key in tuple(original):
        missing = copy.deepcopy(original)
        del missing[key]
        match = "best model schema is incompatible" if key == "scientific_artifact_schema" else "outer object"
        with pytest.raises(TrafficlabError, match=match):
            load_best_model(_encoded(missing), source=Path("best_model.json"))
    extra = copy.deepcopy(original)
    extra["lineage"] = {}
    with pytest.raises(TrafficlabError, match="outer object"):
        load_best_model(_encoded(extra), source=Path("best_model.json"))


@pytest.mark.parametrize("version", [True, 1.0, 0, 2])
def test_best_model_rejects_noncanonical_version(valid_best_model: BestModel, version: object) -> None:
    document = _document(valid_best_model)
    document["version"] = version
    with pytest.raises(TrafficlabError, match="version"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize(
    ("present", "value"),
    (
        (False, None),
        (True, None),
        (True, 1),
        (True, 6),
        (True, True),
        (True, "2"),
        (True, 2.0),
    ),
    ids=("missing", "null", "old", "future", "boolean", "string", "nonintegral"),
)
def test_best_model_rejects_noncurrent_scientific_schema_before_fitted_decode(
    valid_best_model: BestModel,
    present: bool,
    value: object,
) -> None:
    """A schema mismatch is scientific incompatibility, not malformed fitted-model data."""
    document = _document(valid_best_model)
    document["fitted"] = {"deliberately": "unreadable"}
    if present:
        document["scientific_artifact_schema"] = value
    else:
        document.pop("scientific_artifact_schema", None)

    with pytest.raises(TrafficlabError, match="best model schema is incompatible"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("field", ["reference_identity", "capture_identity"])
@pytest.mark.parametrize(
    "identity",
    [
        None,
        {"size": 1},
        {"size": True, "sha256": "a" * 64},
        {"size": -1, "sha256": "a" * 64},
        {"size": 1, "sha256": "A" * 64},
        {"size": 1, "sha256": "a" * 64, "path": "/host/artifact"},
    ],
)
def test_best_model_rejects_malformed_content_identities(
    valid_best_model: BestModel, field: str, identity: object
) -> None:
    document = _document(valid_best_model)
    document[field] = identity
    with pytest.raises(TrafficlabError, match="identity"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


def test_best_model_rejects_legacy_hash_only_lineage(valid_best_model: BestModel) -> None:
    document = _document(valid_best_model)
    document["reference_sha256"] = document.pop("reference_identity")["sha256"]
    document["capture_sha256"] = document.pop("capture_identity")["sha256"]

    with pytest.raises(TrafficlabError, match="outer object"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("seed", [True, -1, 1.0, "1", None])
def test_best_model_rejects_noncanonical_final_seed(valid_best_model: BestModel, seed: object) -> None:
    document = _document(valid_best_model)
    document["final_seed"] = seed
    with pytest.raises(TrafficlabError, match="final seed"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize(
    "limits",
    [
        None,
        {},
        {"max_packets": True, "max_output_bytes": 100, "max_wall_seconds": 1.0},
        {"max_packets": 0, "max_output_bytes": 100, "max_wall_seconds": 1.0},
        {"max_packets": 1, "max_output_bytes": True, "max_wall_seconds": 1.0},
        {"max_packets": 1, "max_output_bytes": 0, "max_wall_seconds": 1.0},
        {"max_packets": 1, "max_output_bytes": 100, "max_wall_seconds": 1},
        {"max_packets": 1, "max_output_bytes": 100, "max_wall_seconds": 0.0},
        {"max_packets": 1, "max_output_bytes": 100, "max_wall_seconds": 1.0, "extra": 1},
    ],
)
def test_best_model_rejects_malformed_final_limits(valid_best_model: BestModel, limits: object) -> None:
    document = _document(valid_best_model)
    document["final_limits"] = limits
    with pytest.raises(TrafficlabError, match="final limits"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("window", [True, 10, 0.0, -1.0, math.nan, math.inf])
def test_best_model_rejects_invalid_observation_window(valid_best_model: BestModel, window: object) -> None:
    document = _document(valid_best_model)
    document["observation_window_seconds"] = window
    with pytest.raises(TrafficlabError, match="observation window|nonfinite JSON"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("genes", [[True], [1], [1.0, 2.0], "1.0"])
def test_best_model_rejects_noncanonical_genes(valid_best_model: BestModel, genes: object) -> None:
    document = _document(valid_best_model)
    document["genes"] = genes
    with pytest.raises(TrafficlabError, match="genes"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("gene", [math.nan, math.inf])
def test_best_model_constructor_rejects_nonfinite_tuple_genes(valid_best_model: BestModel, gene: float) -> None:
    with pytest.raises(ValueError, match="genes"):
        rebuild_best_model(valid_best_model, genes=(gene,))


def test_best_model_rejects_mismatched_bound_names_and_types(valid_best_model: BestModel) -> None:
    original = _document(valid_best_model)
    malformed_bounds: tuple[dict[str, object], ...] = (
        {},
        {"rate": {"lower": 0.25, "upper": 4.0}},
        {"c_lambda": {"lower": 0, "upper": 4.0}},
        {"c_lambda": {"lower": 0.25, "upper": 4.0, "scale": "log"}},
    )
    for bounds in malformed_bounds:
        document = copy.deepcopy(original)
        document["gene_bounds"] = bounds
        with pytest.raises(TrafficlabError, match="gene bounds"):
            load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("field", ["estimator_choices", "seed_policy"])
def test_best_model_rejects_mismatched_fixed_policy(valid_best_model: BestModel, field: str) -> None:
    document = _document(valid_best_model)
    document[field] = {}
    with pytest.raises(TrafficlabError, match="policy|estimator"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


def test_best_model_rejects_unknown_or_mismatched_family(valid_best_model: BestModel) -> None:
    original = _document(valid_best_model)
    for family in ("plugin.family", "mmpp", True):
        document = copy.deepcopy(original)
        document["family"] = family
        with pytest.raises(TrafficlabError, match="family|fitted|gene bounds"):
            load_best_model(_encoded(document), source=Path("best_model.json"))


def test_best_model_revalidates_fitted_parameters_against_outer_genes(valid_best_model: BestModel) -> None:
    document = _document(valid_best_model)
    cast(dict[str, object], document["fitted"])["rate"] = 999.0
    with pytest.raises(TrafficlabError, match="rate"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


def test_best_model_rejects_noncanonical_outer_genes_even_when_fitted_matches_repair(
    valid_best_model: BestModel,
) -> None:
    """Repairing persisted genes on load would silently change the winning chromosome."""
    document = _document(valid_best_model)
    document["genes"] = [99.0]
    fitted = cast(dict[str, object], document["fitted"])
    fitted["rate"] = cast(float, fitted["base_rate"]) * 4.0
    with pytest.raises(TrafficlabError, match="genes"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


def test_best_model_rejects_invalid_utf8_and_non_object_policy(valid_best_model: BestModel) -> None:
    """Decoder or mapping coercion would admit bytes and policy shapes the renderer cannot reproduce."""
    with pytest.raises(TrafficlabError, match="UTF-8"):
        load_best_model(b"\xff", source=Path("best_model.json"))
    document = _document(valid_best_model)
    document["seed_policy"] = []
    with pytest.raises(TrafficlabError, match="seed policy"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize("content", [b"[]", b"null"])
def test_best_model_loader_rejects_non_object_root_before_schema_validation(content: bytes) -> None:
    with pytest.raises(TrafficlabError, match="outer object"):
        load_best_model(content, source=Path("best_model.json"))


@pytest.mark.parametrize(
    "bounds",
    [
        [],
        {"c_lambda": {"lower": 4.0, "upper": 0.25}},
        {"c_lambda": {"lower": -1.0, "upper": 4.0}},
    ],
)
def test_best_model_rejects_structurally_or_semantically_invalid_bounds(
    valid_best_model: BestModel, bounds: object
) -> None:
    """Validating only lower/upper scalar types would bypass bound and family constraints."""
    document = _document(valid_best_model)
    document["gene_bounds"] = bounds
    with pytest.raises(TrafficlabError, match="gene bounds"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


@pytest.mark.parametrize(
    ("family", "genes", "bounds"),
    [
        (
            MARKOV_RENEWAL_FAMILY,
            [0.4, 0.4, 0.5, 2, 1.0],
            {
                "q1": {"lower": 0.1, "upper": 0.4},
                "q2": {"lower": 0.4, "upper": 0.9},
                "alpha": {"lower": 0.0, "upper": 2.0},
                "r": {"lower": 1, "upper": 8},
                "c_t": {"lower": 0.25, "upper": 4.0},
            },
        ),
        (
            MMPP_FAMILY,
            [0.5, 0.75, 1.0, 1.0],
            {
                "q01": {"lower": 0.01, "upper": 10.0},
                "q10": {"lower": 0.01, "upper": 10.0},
                "lambda0": {"lower": 0.01, "upper": 100.0},
                "lambda1": {"lower": 0.1, "upper": 1000.0},
            },
        ),
    ],
    ids=("markov-quantile-order", "mmpp-rate-order"),
)
def test_best_model_rejects_unrepaired_ordered_genes(
    valid_best_model: BestModel, family: ModelFamily, genes: object, bounds: object
) -> None:
    """Stored chromosomes must already be canonical rather than repaired during load."""
    document = _document(valid_best_model)
    document["family"] = family.name
    document["genes"] = genes
    document["gene_bounds"] = bounds
    document["estimator_choices"] = dict(family.estimator_choices)
    with pytest.raises(TrafficlabError, match="genes"):
        load_best_model(_encoded(document), source=Path("best_model.json"))


def test_best_model_constructor_and_renderer_reject_noncanonical_values(valid_best_model: BestModel) -> None:
    """In-memory construction must enforce the same boundary as JSON loading."""
    with pytest.raises(ValueError, match="version"):
        rebuild_best_model(valid_best_model, version=True)
    with pytest.raises(TrafficlabError, match="gene bounds"):
        rebuild_best_model(valid_best_model, gene_bounds={})
    with pytest.raises((TypeError, ValueError), match="identity"):
        rebuild_best_model(valid_best_model, reference_identity={"size": True, "sha256": "a" * 64})
    with pytest.raises(ValueError, match="final_seed"):
        rebuild_best_model(valid_best_model, final_seed=True)
    with pytest.raises(TrafficlabError, match="final limits"):
        rebuild_best_model(
            valid_best_model,
            final_limits={**FINAL_LIMITS.model_dump(), "max_packets": 0},
        )
    with pytest.raises(TypeError, match="BestModel"):
        render_best_model(object())  # type: ignore[arg-type]


def test_best_model_constructor_freezes_canonical_json_list_genes(valid_best_model: BestModel) -> None:
    """Canonical JSON arrays must become immutable tuples when validated directly."""
    rebuilt = rebuild_best_model(valid_best_model, genes=[1.0])

    assert rebuilt.genes == (1.0,)
    assert type(rebuilt.genes) is tuple
    assert load_best_model(render_best_model(rebuilt), source=Path("best_model.json")) == rebuilt


def test_best_model_reconstruction_and_render_revalidate_nested_model_instances(
    valid_best_model: BestModel,
) -> None:
    """A model_copy-mutated nested limit must not survive reconstruction or canonical rendering."""
    invalid_limits = valid_best_model.final_limits.model_copy(update={"max_packets": 0})

    with pytest.raises(TrafficlabError, match="final_limits|final limits|max_packets"):
        rebuild_best_model(valid_best_model, final_limits=invalid_limits)

    corrupted = valid_best_model.model_copy(update={"final_limits": invalid_limits})
    with pytest.raises(TrafficlabError, match="final limits|max_packets"):
        render_best_model(corrupted)


def test_best_model_renderer_translates_encoding_failure_and_rejects_roundtrip_change(
    valid_best_model: BestModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical rendering must translate encoder errors and reject a changed loader result."""
    real_dumps = registry_module.json.dumps

    def fail_dumps(*_args: object, **_kwargs: object) -> str:
        raise TypeError("injected JSON encoder failure")

    monkeypatch.setattr(registry_module.json, "dumps", fail_dumps)
    with pytest.raises(TrafficlabError, match="JSON rendering.*encoder failure"):
        render_best_model(valid_best_model)

    monkeypatch.setattr(registry_module.json, "dumps", real_dumps)
    changed = rebuild_best_model(valid_best_model, final_seed=valid_best_model.final_seed + 1)

    def load_changed(_content: bytes, *, source: Path) -> BestModel:
        del source
        return changed

    monkeypatch.setattr(registry_module, "load_best_model", load_changed)
    with pytest.raises(TrafficlabError, match="round trip changed"):
        render_best_model(valid_best_model)


def test_make_best_model_rejects_a_nonregistry_family_instance() -> None:
    """Accepting another instance would create a runtime replacement hook around the closed registry."""
    from trafficlab.generation.models.poisson import PoissonFamily

    with pytest.raises(TrafficlabError, match="unknown model family object"):
        make_best_model(
            PoissonFamily(),
            REFERENCE,
            (1.0,),
            reference_identity=REFERENCE_IDENTITY,
            capture_identity=CAPTURE_IDENTITY,
            final_seed=FINAL_SEED,
            final_limits=FINAL_LIMITS,
            W=WINDOW,
            bounds=POISSON_BOUNDS,
        )
