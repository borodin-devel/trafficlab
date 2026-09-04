"""Packet-HMM family metadata, codec, and fit tests."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

import trafficlab.generation.models.packet_hmm.family as packet_hmm_family
from tests.unit.generation.models.packet_hmm._support import two_state_model
from trafficlab.common.config import IntegerBounds, PacketHmmConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.fitted_schema import PacketHmmPayload
from trafficlab.generation.models.packet_hmm.family import PacketHmmFamily
from trafficlab.generation.models.packet_hmm.inference import BaumWelchDiagnostics
from trafficlab.generation.models.packet_hmm.model import PacketHmmModel

FAMILY = PacketHmmFamily()
BOUNDS = PacketHmmConfig(state_count=IntegerBounds(lower=2, upper=4))
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.0, Direction.INBOUND, 70),
        TraceEvent(1.0, Direction.OUTBOUND, 90),
        TraceEvent(3.0, Direction.INBOUND, 120),
        TraceEvent(6.0, Direction.OUTBOUND, 150),
    )
)


def test_family_declares_integer_state_count_and_fixed_estimator_policy() -> None:
    """Changing EM, binning, or label policy must invalidate the outer artifact metadata."""
    assert FAMILY.name == "packet_hmm"
    assert FAMILY.gene_names == ("state_count",)
    assert FAMILY.gene_coordinate_kinds == ("integer",)
    assert FAMILY.bounds_type is PacketHmmConfig
    assert FAMILY.estimator_choices == {
        "em": "scaled_baum_welch_bounded_100_tolerance_1e-8",
        "emission": "observed_category_additive_0.001",
        "first_event": "zero_empirical_initial_mark",
        "iat_bins": "zero_plus_type7_terciles",
        "initialization": "fixed_cyclic_v1",
        "reservoirs": "individual_raw_category_members",
        "size_bins": "type7_terciles",
        "state_order": "expected_iat_then_emission_transition",
    }


def test_repair_clamps_exact_integer_state_count() -> None:
    """A floating or out-of-range latent dimension would make fitted matrices ambiguous."""
    assert FAMILY.repair((1,), BOUNDS, REFERENCE) == (2,)
    assert FAMILY.repair((3,), BOUNDS, REFERENCE) == (3,)
    assert FAMILY.repair((8,), BOUNDS, REFERENCE) == (4,)


def test_dump_load_round_trip_retains_estimators_and_individual_reservoirs() -> None:
    """Strict loading must retain all generation evidence without adding observation templates."""
    model = two_state_model()
    payload = FAMILY.dump_fitted(model)

    assert FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS) == model
    assert set(payload) == {
        "additive_smoothing",
        "convergence_tolerance",
        "diagnostics",
        "emission_rows",
        "iat_quantiles",
        "iat_thresholds",
        "initial_marks",
        "initial_probabilities",
        "initialization",
        "maximum_iterations",
        "reservoirs",
        "size_quantiles",
        "size_thresholds",
        "state_count",
        "transition_rows",
        "vocabulary",
    }
    assert "sequence" not in repr(payload)
    assert "template" not in repr(payload)


def test_fit_is_repeatable_and_persists_non_decreasing_likelihoods() -> None:
    """Data-order randomness or an unchecked EM decrease would make equal candidates diverge."""
    first = FAMILY.fit(REFERENCE, (2,), W=6.0, bounds=BOUNDS)
    second = FAMILY.fit(REFERENCE, (2,), W=6.0, bounds=BOUNDS)

    assert first == second
    assert all(
        right + 1e-10 >= left
        for left, right in zip(first.diagnostics.log_likelihoods, first.diagnostics.log_likelihoods[1:], strict=False)
    )


def _valid_nonconverged_diagnostics() -> BaumWelchDiagnostics:
    return BaumWelchDiagnostics(
        converged=False,
        iterations=100,
        log_likelihoods=tuple(float(index) for index in range(101)),
    )


def test_runtime_model_rejects_a_mathematically_consistent_nonconverged_estimate() -> None:
    """A well-formed capped EM history is still not an admissible competitive model."""
    with pytest.raises(ValueError, match="converg"):
        replace(two_state_model(), diagnostics=_valid_nonconverged_diagnostics())


def test_family_fit_rejects_a_bypassed_nonconverged_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidate fitting must invalidate nonconvergence even if an estimator object bypasses construction."""
    corrupted = two_state_model()
    object.__setattr__(corrupted, "diagnostics", _valid_nonconverged_diagnostics())

    def nonconverged_fit(_trace: TrafficTrace, *, state_count: int) -> PacketHmmModel:
        del state_count
        return corrupted

    monkeypatch.setattr(packet_hmm_family, "fit_trace", nonconverged_fit)

    with pytest.raises(TrafficlabError, match="converg"):
        FAMILY.fit(REFERENCE, (2,), W=6.0, bounds=BOUNDS)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("state_count",), 3, "state_count"),
        (("additive_smoothing",), 0.01, "smoothing"),
        (("maximum_iterations",), 99, "maximum_iterations"),
        (("initialization",), "random", "initialization"),
        (("convergence_tolerance",), 1e-7, "convergence tolerance"),
        (("emission_rows", 0), [0.7, 0.2], "sum to one"),
        (("transition_rows", 0), [0.8], "K x K"),
        (("reservoirs", 0, 0, "iat"), 2.0, "IAT"),
        (("diagnostics", "log_likelihoods"), [-4.0, -4.1, -3.5], "nondecreasing"),
        (("diagnostics",), [], "diagnostics must contain"),
        (("diagnostics", "converged"), 1, "exact bool"),
        (("vocabulary",), [], "nonempty"),
        (("vocabulary", 0), {}, "vocabulary entries"),
        (("vocabulary", 0, "iat_bin"), 1.0, "exact scalar"),
        (("reservoirs",), {}, "reservoirs must be a list"),
        (("reservoirs", 0), {}, "reservoir must be a list"),
        (("reservoirs", 0, 0), {}, "reservoir entries"),
        (("reservoirs", 0, 0, "iat"), 1, "exact float"),
        (("iat_quantiles",), [1.0 / 3.0], "contain two"),
    ),
)
def test_loader_rejects_corrupt_and_outer_gene_inconsistent_payload(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    """Every redundant estimator, category, and matrix invariant must be rechecked after JSON decoding."""
    payload = copy.deepcopy(FAMILY.dump_fitted(two_state_model()))
    cursor: object = payload
    for component in path[:-1]:
        cursor = cursor[component]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]

    with pytest.raises(TrafficlabError, match=message):
        FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS)


def test_wire_payload_rejects_changed_fixed_constants_before_family_load() -> None:
    """Publication schema must not accept a numerically valid but scientifically different estimator."""
    payload = FAMILY.dump_fitted(two_state_model())
    payload["additive_smoothing"] = 0.01

    with pytest.raises(ValidationError, match="additive_smoothing"):
        PacketHmmPayload.model_validate(payload)


def test_wire_payload_cross_validator_rejects_every_estimator_table_shape() -> None:
    """Every cross-field HMM shape and fixed-estimator branch must remain strict in the public union."""
    base = FAMILY.dump_fitted(two_state_model())
    mutations: tuple[tuple[tuple[str | int, ...], object, str], ...] = (
        (("convergence_tolerance",), 1e-7, "convergence_tolerance"),
        (("iat_quantiles",), [0.2, 0.8], "quantiles"),
        (("iat_thresholds",), [1.0], "iat_thresholds"),
        (("size_thresholds",), [120.0, 100.0], "size_thresholds"),
        (("vocabulary", 1), copy.deepcopy(cast(list[object], base["vocabulary"])[0]), "vocabulary"),
        (("reservoirs", 0), [], "reservoirs"),
        (
            ("initial_marks",),
            [
                {"direction": "outbound", "frame_length": 60, "count": 1},
                {"direction": "inbound", "frame_length": 70, "count": 1},
            ],
            "initial_marks",
        ),
        (("initial_probabilities",), [1.0], "initial_probabilities"),
        (("transition_rows",), [[0.5, 0.5]], "transition_rows"),
        (("emission_rows",), [[0.5, 0.5]], "emission_rows"),
    )
    for path, value, message in mutations:
        payload = copy.deepcopy(base)
        cursor: object = payload
        for component in path[:-1]:
            cursor = cursor[component]  # type: ignore[index]
        cursor[path[-1]] = value  # type: ignore[index]
        with pytest.raises(ValidationError, match=message):
            PacketHmmPayload.model_validate(payload)


def test_loader_rejects_a_non_mapping_payload_before_field_access() -> None:
    """A non-object wire value must remain a stable family-domain error."""
    with pytest.raises(TrafficlabError, match="fitted payload"):
        FAMILY.load_fitted(object(), genes=(2,), bounds=BOUNDS)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("additive_smoothing", 0.25),
        ("convergence_tolerance", 0.5),
        ("maximum_iterations", 1),
        ("iat_quantiles", [0.1, 0.9]),
        ("size_quantiles", [0.1, 0.9]),
    ),
)
def test_draft_2020_schema_rejects_every_changed_fixed_hmm_estimator(field: str, value: object) -> None:
    """Independent schema validation must expose constants instead of relying on runtime validators."""
    payload = FAMILY.dump_fitted(two_state_model())
    schema = PacketHmmPayload.model_json_schema(mode="validation")
    validator = cast(Any, Draft202012Validator(schema))
    validator.validate(payload)
    payload[field] = value

    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)


@pytest.mark.parametrize(
    "diagnostics",
    (
        {"converged": True, "iterations": 0, "log_likelihoods": [-4.0]},
        {"converged": True, "iterations": 1, "log_likelihoods": [-4.0, -3.0]},
        {"converged": False, "iterations": 100, "log_likelihoods": [-4.0] + [-3.0] * 100},
    ),
)
def test_payload_and_loader_reject_false_convergence_or_nonconvergence_claims(
    diagnostics: dict[str, object],
) -> None:
    """Wire validation and family loading must derive termination truth from the final improvement."""
    payload = FAMILY.dump_fitted(two_state_model())
    payload["diagnostics"] = diagnostics

    with pytest.raises(ValidationError, match="converg|improvement"):
        PacketHmmPayload.model_validate(payload)
    with pytest.raises(TrafficlabError, match="converg|improvement"):
        FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS)


def test_wire_and_loader_reject_a_consistent_nonconverged_history() -> None:
    """Strict wire validation must reject nonconvergence, not merely contradictory termination fields."""
    payload = FAMILY.dump_fitted(two_state_model())
    diagnostics = _valid_nonconverged_diagnostics()
    payload["diagnostics"] = {
        "converged": diagnostics.converged,
        "iterations": diagnostics.iterations,
        "log_likelihoods": list(diagnostics.log_likelihoods),
    }

    with pytest.raises(ValidationError, match="converg"):
        PacketHmmPayload.model_validate(payload)
    with pytest.raises(TrafficlabError, match="converg"):
        FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS)


@pytest.mark.parametrize("genes", [(), (2, 3), (True,), (2.0,), (float("nan"),)])
def test_repair_rejects_noncanonical_state_count_chromosomes(genes: tuple[object, ...]) -> None:
    """Repair accepts one exact integer coordinate and never rounds ambiguous numeric inputs."""
    with pytest.raises(TrafficlabError, match="state_count"):
        FAMILY.repair(genes, BOUNDS, REFERENCE)  # type: ignore[arg-type]
