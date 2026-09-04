"""Direct packet-HMM value-object and model invariant rejection tests."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, cast

import pytest

from tests.unit.generation.models.packet_hmm._support import two_state_model
from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.packet_hmm.model import (
    BaumWelchDiagnostics,
    PacketCategory,
    PacketSample,
    fit_trace,
)


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (lambda: PacketCategory(True, Direction.OUTBOUND, 0), "iat_bin"),
        (lambda: PacketCategory(4, Direction.OUTBOUND, 0), "iat_bin"),
        (lambda: PacketCategory(0, cast(Any, "outbound"), 0), "Direction"),
        (lambda: PacketCategory(0, Direction.OUTBOUND, 3), "size_bin"),
        (lambda: PacketSample(iat=math.nan, frame_length=60), "finite"),
        (lambda: PacketSample(iat=-1.0, frame_length=60), "nonnegative"),
        (lambda: PacketSample(iat=1.0, frame_length=13), "frame_length"),
        (
            lambda: BaumWelchDiagnostics(converged=True, iterations=1, log_likelihoods=(-2.0, -3.0)),
            "nondecreasing",
        ),
        (
            lambda: BaumWelchDiagnostics(converged=False, iterations=2, log_likelihoods=(-2.0, -1.0)),
            "iterations",
        ),
    ),
)
def test_value_objects_reject_malformed_scalars_and_diagnostics(factory: object, message: str) -> None:
    """Invalid raw observations and dishonest convergence records must fail at construction."""
    with pytest.raises((TypeError, ValueError), match=message):
        cast(Any, factory)()


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"state_count": 1}, "state_count"),
        ({"additive_smoothing": 0.01}, "smoothing"),
        ({"initialization": "random"}, "initialization"),
        ({"iat_quantiles": (0.2, 0.8)}, "IAT quantiles"),
        ({"size_quantiles": (0.2, 0.8)}, "size quantiles"),
        ({"diagnostics": cast(Any, object())}, "diagnostics"),
        (
            {
                "diagnostics": BaumWelchDiagnostics(
                    converged=True,
                    iterations=101,
                    log_likelihoods=tuple(0.0 for _ in range(102)),
                )
            },
            "exceed",
        ),
        ({"initial_marks": MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 2),))}, "initial_marks"),
        ({"state_count": 3}, "state_count"),
        ({"initial_probabilities": (0.4, 0.4)}, "sum to one"),
        ({"transition_rows": ((0.5, 0.5),)}, "K x K"),
        ({"emission_rows": ((0.5, 0.5), (0.5, math.nan))}, "finite"),
        ({"vocabulary": ()}, "vocabulary"),
        ({"vocabulary": (cast(Any, object()), PacketCategory(3, Direction.OUTBOUND, 2))}, "PacketCategory"),
        ({"vocabulary": (PacketCategory(1, Direction.INBOUND, 0),) * 2}, "unique"),
        ({"reservoirs": ((), (PacketSample(3.0, 130),))}, "nonempty"),
        ({"reservoirs": (((PacketSample(1.0, 70),),))}, "reservoir"),
        (
            {
                "vocabulary": (
                    PacketCategory(2, Direction.INBOUND, 0),
                    PacketCategory(3, Direction.OUTBOUND, 2),
                )
            },
            "IAT bin",
        ),
        (
            {
                "vocabulary": (
                    PacketCategory(1, Direction.INBOUND, 1),
                    PacketCategory(3, Direction.OUTBOUND, 2),
                )
            },
            "size bin",
        ),
        ({"emission_rows": ((0.8, 0.2),)}, "K x M"),
        ({"emission_rows": ((0.1, 0.9), (0.8, 0.2))}, "canonical"),
        ({"iat_thresholds": (2.0, 1.0)}, "IAT thresholds"),
        ({"size_thresholds": (120.0, 100.0)}, "size thresholds"),
        ({"maximum_iterations": 99}, "maximum_iterations"),
        ({"convergence_tolerance": 1e-7}, "tolerance"),
    ),
)
def test_model_rejects_shape_probability_category_and_estimator_corruption(
    changes: dict[str, object], message: str
) -> None:
    """A fitted model is self-validating even when constructed outside the JSON codec."""
    with pytest.raises((TypeError, ValueError), match=message):
        replace(two_state_model(), **changes)  # type: ignore[arg-type]


def test_model_binds_convergence_claim_to_final_likelihood_improvement() -> None:
    """A zero-update convergence claim, large terminal gain, or stalled capped fit misstates EM termination."""
    invalid = (
        BaumWelchDiagnostics(converged=True, iterations=0, log_likelihoods=(-2.0,)),
        BaumWelchDiagnostics(converged=True, iterations=1, log_likelihoods=(-2.0, -1.5)),
        BaumWelchDiagnostics(
            converged=False,
            iterations=100,
            log_likelihoods=(-2.0, *(-1.0 for _ in range(100))),
        ),
        BaumWelchDiagnostics(
            converged=False,
            iterations=100,
            log_likelihoods=(-2.0, *(-1.0 for _ in range(99)), -0.999999999),
        ),
        BaumWelchDiagnostics(
            converged=False,
            iterations=100,
            log_likelihoods=tuple(float(index) for index in range(101)),
        ),
    )
    for diagnostics in invalid:
        with pytest.raises(ValueError, match="converg|improvement"):
            replace(two_state_model(), diagnostics=diagnostics)


def test_fit_trace_rejects_an_invalid_state_count_before_encoding() -> None:
    """The direct estimator boundary must reject an unsupported latent dimension before allocating observations."""
    with pytest.raises(ValueError, match="state_count"):
        fit_trace(cast(Any, object()), state_count=1)
