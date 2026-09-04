"""Behavioral tests for the exponential ACD traffic model."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from pydantic import ValidationError

import trafficlab.generation.models.acd as acd
from trafficlab.common.config import AcdConfig, GenerationLimits, IntegerBounds
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.acd import (
    AcdFamily,
    AcdFitDiagnostics,
    AcdModel,
    _conditional_means,
    _exponential_negative_log_likelihood,
    _generate_with_rng,
    _likelihood_and_gradient,
    _transform_parameters,
)
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.fitted_schema import AcdPayload

FAMILY = AcdFamily()
BOUNDS = AcdConfig(order=IntegerBounds(lower=1, upper=3))
LIMITS = GenerationLimits(max_packets=20, max_output_bytes=10_000, max_wall_seconds=10.0)
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.5, Direction.INBOUND, 100),
        TraceEvent(1.5, Direction.OUTBOUND, 60),
        TraceEvent(2.0, Direction.INBOUND, 100),
    )
)
MARKS = MarkDistribution(
    (
        MarkCount(Direction.OUTBOUND, 60, 1),
        MarkCount(Direction.INBOUND, 100, 3),
    )
)


class ScriptedAcdRng:
    """Expose the scalar innovation and empirical-mark calls in exact order."""

    def __init__(self, *, indices: Sequence[int], exponentials: Sequence[float]) -> None:
        self._indices = iter(indices)
        self._exponentials = iter(exponentials)
        self.calls: list[tuple[str, float | int]] = []

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        return next(self._indices)

    def exponential(self, scale: float) -> float:
        self.calls.append(("exponential", scale))
        return next(self._exponentials)


class ScriptedClock:
    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def _model() -> AcdModel:
    return AcdModel(
        omega=0.5,
        alpha=(0.2,),
        beta=(0.3,),
        diagnostics=AcdFitDiagnostics(1.0, 2.5, 7, True),
        marks=MARKS,
    )


def test_family_declares_integer_order_chromosome_and_fixed_estimator_policy() -> None:
    """Wrong metadata would make generic search or artifact validation reinterpret an ACD fit."""
    assert FAMILY.name == "acd"
    assert FAMILY.gene_names == ("order",)
    assert FAMILY.gene_coordinate_kinds == ("integer",)
    assert FAMILY.bounds_type is AcdConfig
    assert FAMILY.estimator_choices == {
        "first_event": "zero",
        "duration": "exponential_acd_mle",
        "initialization": "sample_mean_presample_durations_and_conditional_means",
        "marks": "joint_empirical_first_appearance",
        "optimizer": "scipy.optimize.minimize/L-BFGS-B",
        "optimizer_start": "zero_unconstrained_sample_mean_scale",
        "optimizer_tolerance": 1e-10,
        "optimizer_maximum_iterations": 500,
        "parameter_transform": "scaled_exponential_simplex_slack",
    }


def test_repair_clamps_exact_integer_order_at_inclusive_endpoints() -> None:
    """Using floating or unbounded orders would change both recursion and payload width."""
    assert FAMILY.repair((0,), BOUNDS, REFERENCE) == (1,)
    assert FAMILY.repair((1,), BOUNDS, REFERENCE) == (1,)
    assert FAMILY.repair((3,), BOUNDS, REFERENCE) == (3,)
    assert FAMILY.repair((9,), BOUNDS, REFERENCE) == (3,)


@pytest.mark.parametrize("genes", [(), (1, 2), (1.0,), (True,), (math.inf,)])
def test_repair_rejects_noncanonical_order_chromosomes(genes: tuple[object, ...]) -> None:
    """Coercing the structural order would make chromosome identity ambiguous."""
    with pytest.raises(TrafficlabError, match="order"):
        FAMILY.repair(genes, BOUNDS, REFERENCE)  # type: ignore[arg-type]


def test_repair_rejects_non_acd_bounds() -> None:
    """Another family's bounds cannot define an ACD recursion order."""
    with pytest.raises(TrafficlabError, match="ACD bounds"):
        FAMILY.repair((1,), object(), REFERENCE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bounds",
    [
        AcdConfig.model_construct(order=object()),
        AcdConfig.model_construct(order=IntegerBounds.model_construct(lower=0, upper=3)),
    ],
)
def test_repair_defensively_rejects_constructed_invalid_acd_bounds(bounds: AcdConfig) -> None:
    """Bypassing Pydantic construction cannot weaken the family repair boundary."""
    with pytest.raises(TrafficlabError, match="order bounds"):
        FAMILY.repair((1,), bounds, REFERENCE)


def test_zero_optimizer_coordinates_map_to_reference_mean_and_strict_stationarity() -> None:
    """Dropping the simplex slack would make the documented deterministic start nonstationary."""
    omega, alpha, beta = _transform_parameters(np.zeros(3, dtype=np.float64), order=1, reference_mean=2.0)
    coefficient = (1.0 - 1e-12) / 3.0
    slack = 1.0 - 2.0 * coefficient

    assert omega == pytest.approx(2.0 * slack, abs=1e-15)
    assert alpha == pytest.approx((coefficient,), abs=1e-15)
    assert beta == pytest.approx((coefficient,), abs=1e-15)
    assert sum(alpha) + sum(beta) < 1.0
    assert omega / (1.0 - sum(alpha) - sum(beta)) == pytest.approx(2.0, abs=1e-14)


@pytest.mark.parametrize(
    ("parameters", "order", "reference_mean", "message"),
    [
        (np.zeros(3, dtype=np.float64), 0, 1.0, "order"),
        (np.zeros(3, dtype=np.float64), 1, 0.0, "reference_mean"),
        (np.zeros(2, dtype=np.float64), 1, 1.0, "wrong shape"),
        (np.asarray([0.0, math.nan, 0.0]), 1, 1.0, "finite"),
        (np.asarray([1_000.0, 0.0, 0.0]), 1, 1.0, "overflowed"),
        (np.asarray([-1_000.0, 0.0, 0.0]), 1, 1.0, "stationary parameters"),
    ],
)
def test_parameter_transform_rejects_invalid_coordinates_without_leaking_numeric_failures(
    parameters: np.ndarray[tuple[int], np.dtype[np.float64]],
    order: int,
    reference_mean: float,
    message: str,
) -> None:
    """Malformed optimizer state must fail at the transform boundary."""
    with pytest.raises(ValueError, match=message):
        _transform_parameters(parameters, order=order, reference_mean=reference_mean)


def test_conditional_mean_recursion_and_exponential_likelihood_match_hand_values() -> None:
    """A lag reversal or omitted log-scale term would change these independent hand calculations."""
    durations = (1.0, 0.0, 3.0)
    conditional_means = _conditional_means(
        durations,
        omega=0.5,
        alpha=(0.2,),
        beta=(0.3,),
        initial_mean=2.0,
    )

    assert conditional_means == pytest.approx((1.5, 1.15, 0.845), abs=1e-15)
    expected = math.log(1.5) + 1.0 / 1.5 + math.log(1.15) + math.log(0.845) + 3.0 / 0.845
    assert _exponential_negative_log_likelihood(durations, conditional_means) == pytest.approx(expected, abs=1e-14)


@pytest.mark.parametrize(
    ("durations", "omega", "alpha", "beta", "initial_mean", "expected"),
    (
        (
            (1.0, 3.0, 2.0, 0.0),
            0.4,
            (0.2, 0.1),
            (0.3, 0.1),
            2.0,
            (1.8, 1.54, 1.742, 1.7766),
        ),
        (
            (0.5, 2.0, 1.0, 3.0),
            0.2,
            (0.1, 0.05, 0.02),
            (0.3, 0.2, 0.1),
            1.5,
            (1.355, 1.2115, 1.23945, 1.159635),
        ),
    ),
    ids=("order-2", "order-3"),
)
def test_multilag_conditional_mean_recursion_matches_literal_history_order(
    durations: tuple[float, ...],
    omega: float,
    alpha: tuple[float, ...],
    beta: tuple[float, ...],
    initial_mean: float,
    expected: tuple[float, ...],
) -> None:
    """Distinct alpha and beta lags must retain newest-to-oldest order for p=2 and p=3."""
    assert _conditional_means(
        durations,
        omega=omega,
        alpha=alpha,
        beta=beta,
        initial_mean=initial_mean,
    ) == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize(
    ("durations", "omega", "alpha", "beta", "initial_mean", "message"),
    [
        ((), 0.5, (0.2,), (0.3,), 1.0, "durations"),
        ((math.nan,), 0.5, (0.2,), (0.3,), 1.0, "durations"),
        ((1.0,), 0.0, (0.2,), (0.3,), 1.0, "omega"),
        ((1.0,), 0.5, (0.2, 0.1), (0.3,), 1.0, "matching"),
        ((1.0,), 0.5, (-0.2,), (0.3,), 1.0, "coefficients"),
        ((1.0,), 0.5, (0.7,), (0.3,), 1.0, "sum below one"),
        ((1.0,), 0.5, (0.2,), (0.3,), 0.0, "reference_mean"),
        (
            (math.nextafter(math.inf, 0.0), 1.0),
            math.ldexp(1.0, 1023),
            (0.75,),
            (0.0,),
            1.0,
            "recursion",
        ),
    ],
)
def test_conditional_mean_recursion_rejects_invalid_inputs(
    durations: tuple[float, ...],
    omega: float,
    alpha: tuple[float, ...],
    beta: tuple[float, ...],
    initial_mean: float,
    message: str,
) -> None:
    """Invalid likelihood inputs must fail before SciPy can interpret them."""
    with pytest.raises(ValueError, match=message):
        _conditional_means(
            durations,
            omega=omega,
            alpha=alpha,
            beta=beta,
            initial_mean=initial_mean,
        )


@pytest.mark.parametrize(
    ("durations", "conditional_means", "message"),
    [
        ((), (), "equally sized"),
        ((1.0,), (1.0, 2.0), "equally sized"),
        ((-1.0,), (1.0,), "durations"),
        ((1.0,), (0.0,), "conditional means"),
    ],
)
def test_exponential_likelihood_rejects_invalid_vectors(
    durations: tuple[float, ...], conditional_means: tuple[float, ...], message: str
) -> None:
    """A malformed likelihood vector cannot become optimizer evidence."""
    with pytest.raises(ValueError, match=message):
        _exponential_negative_log_likelihood(durations, conditional_means)


@pytest.mark.parametrize(
    ("parameters", "durations", "order", "reference_mean"),
    (
        (np.asarray([0.2, -0.4, 0.7], dtype=np.float64), (0.0, 0.5, 1.0, 0.5), 1, 0.5),
        (
            np.asarray([0.1, -0.3, 0.2, -0.1, 0.4], dtype=np.float64),
            (0.0, 0.5, 1.5, 0.25, 2.0),
            2,
            0.85,
        ),
        (
            np.asarray([0.05, -0.4, 0.3, -0.2, 0.1, 0.5, -0.1], dtype=np.float64),
            (0.25, 1.0, 0.0, 2.0, 0.5, 1.5),
            3,
            0.875,
        ),
    ),
    ids=("order-1", "order-2", "order-3"),
)
def test_analytic_likelihood_gradient_matches_an_independent_central_difference(
    parameters: np.ndarray[tuple[int], np.dtype[np.float64]],
    durations: tuple[float, ...],
    order: int,
    reference_mean: float,
) -> None:
    """Transform-chain and recursive derivatives must preserve every distinct lag index."""
    _loss, gradient = _likelihood_and_gradient(parameters, durations, order, reference_mean)
    finite_difference: list[float] = []
    for index in range(len(parameters)):
        step = 1e-6
        above = parameters.copy()
        below = parameters.copy()
        above[index] += step
        below[index] -= step
        upper_loss = _likelihood_and_gradient(above, durations, order, reference_mean)[0]
        lower_loss = _likelihood_and_gradient(below, durations, order, reference_mean)[0]
        finite_difference.append((upper_loss - lower_loss) / (2.0 * step))

    assert gradient.tolist() == pytest.approx(finite_difference, abs=1e-8)


@pytest.mark.parametrize(
    "parameters",
    (
        np.asarray([math.nan, math.inf, -math.inf], dtype=np.float64),
        np.asarray([1e308, -1e308, 1e308], dtype=np.float64),
        np.asarray([-700.0, -1_000.0, -1_000.0], dtype=np.float64),
    ),
)
def test_likelihood_objective_returns_a_finite_penalty_for_invalid_solver_coordinates(
    parameters: np.ndarray[tuple[int], np.dtype[np.float64]],
) -> None:
    """An invalid trial coordinate must guide SciPy away without leaking an exception."""
    loss, gradient = _likelihood_and_gradient(
        parameters,
        (0.5,),
        1,
        0.5,
    )

    assert math.isfinite(loss) and loss >= 1e100
    assert np.all(np.isfinite(gradient))


def test_fit_accepts_zero_iats_is_deterministic_and_returns_a_stationary_ordered_model() -> None:
    """Filtering ties or randomizing the optimizer would change the fitted duration evidence."""
    first = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)
    second = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)

    assert first == second
    assert len(first.alpha) == len(first.beta) == 2
    assert first.omega > 0.0
    assert all(value >= 0.0 for value in (*first.alpha, *first.beta))
    assert math.fsum((*first.alpha, *first.beta)) < 1.0
    assert first.diagnostics.initial_conditional_duration == 0.5
    assert math.isfinite(first.diagnostics.final_negative_log_likelihood)
    assert 0 <= first.diagnostics.iterations <= 500
    assert first.diagnostics.converged is True
    assert first.marks.entries == (
        MarkCount(Direction.OUTBOUND, 60, 3),
        MarkCount(Direction.INBOUND, 100, 2),
    )


def test_acd_wire_schema_requires_converged_bounded_likelihood_diagnostics() -> None:
    """Discarding optimizer diagnostics would make a serialized ACD estimate unauditable."""
    payload = {
        **FAMILY.dump_fitted(_model()),
        "diagnostics": {
            "initial_conditional_duration": 1.0,
            "final_negative_log_likelihood": 2.5,
            "iterations": 7,
            "converged": True,
        },
    }

    assert AcdPayload.model_validate(payload).diagnostics.iterations == 7
    for malformed in (
        {**payload, "diagnostics": {**cast(dict[str, object], payload["diagnostics"]), "converged": False}},
        {**payload, "diagnostics": {**cast(dict[str, object], payload["diagnostics"]), "iterations": 501}},
        {
            **payload,
            "diagnostics": {
                **cast(dict[str, object], payload["diagnostics"]),
                "initial_conditional_duration": 0.0,
            },
        },
    ):
        with pytest.raises(ValidationError, match="diagnostics|converged|iterations|conditional"):
            AcdPayload.model_validate(malformed)


def test_fit_rejects_explicit_optimizer_nonconvergence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing a capped or failed MLE would hide an invalid fitted family candidate."""
    failed = SimpleNamespace(
        success=False,
        x=np.zeros(3, dtype=np.float64),
        nit=500,
        fun=1.0,
        message="iteration limit",
    )

    def fake_minimize(*_args: object, **_kwargs: object) -> object:
        return failed

    monkeypatch.setattr(acd, "minimize", fake_minimize)

    with pytest.raises(TrafficlabError, match="ACD optimizer"):
        FAMILY.fit(REFERENCE, (1,), W=2.0, bounds=BOUNDS)


@pytest.mark.parametrize("defect", ("iterations", "shape", "nonfinite", "loss"))
def test_fit_rejects_malformed_or_inconsistent_success_results(monkeypatch: pytest.MonkeyPatch, defect: str) -> None:
    """A nominal success cannot bypass the fixed solver-result and direct-loss checks."""
    parameters = np.zeros(3, dtype=np.float64)
    durations = (0.0, 0.5, 1.0, 0.5)
    final_loss = _likelihood_and_gradient(parameters, durations, 1, 0.5)[0]
    result = SimpleNamespace(success=True, x=parameters, nit=1, fun=final_loss, message="ok")
    if defect == "iterations":
        result.nit = "bad"
    elif defect == "shape":
        result.x = np.zeros(4, dtype=np.float64)
    elif defect == "nonfinite":
        result.fun = math.nan
    else:
        result.fun = final_loss + 1.0

    def fake_minimize(*_args: object, **_kwargs: object) -> object:
        return result

    monkeypatch.setattr(acd, "minimize", fake_minimize)

    with pytest.raises(TrafficlabError, match="ACD optimizer"):
        FAMILY.fit(REFERENCE, (1,), W=2.0, bounds=BOUNDS)


@pytest.mark.parametrize(
    "parameters",
    [
        (0.0, (0.2,), (0.3,)),
        (math.inf, (0.2,), (0.3,)),
        (0.5, (-0.1,), (0.3,)),
        (0.5, (0.5,), (0.5,)),
        (0.5, (0.2, 0.1), (0.3,)),
        (math.ldexp(1.0, 1023), (0.0,), (math.nextafter(1.0, 0.0),)),
    ],
)
def test_model_rejects_nonpositive_nonstationary_or_unusable_parameters(
    parameters: tuple[float, tuple[float, ...], tuple[float, ...]],
) -> None:
    """Invalid recursion state must fail at construction instead of during a later generation."""
    with pytest.raises((TypeError, ValueError)):
        AcdModel(*parameters, AcdFitDiagnostics(1.0, 2.5, 7, True), MARKS)


def test_model_rejects_huge_finite_coefficients_as_nonstationary_without_overflow() -> None:
    """Summing individually nonstationary finite coefficients must not leak arithmetic overflow."""
    maximum = math.nextafter(math.inf, 0.0)

    with pytest.raises(ValueError, match="coefficient"):
        AcdModel(0.5, (maximum,), (maximum,), AcdFitDiagnostics(1.0, 2.5, 7, True), MARKS)


def test_wire_payload_rejects_huge_finite_coefficients_as_validation_error() -> None:
    """The public fitted-payload boundary must translate a nonstationary finite vector consistently."""
    maximum = math.nextafter(math.inf, 0.0)
    payload = {
        "omega": 0.5,
        "alpha": [maximum],
        "beta": [maximum],
        "diagnostics": {
            "initial_conditional_duration": 1.0,
            "final_negative_log_likelihood": 2.5,
            "iterations": 7,
            "converged": True,
        },
        "marks": [{"direction": "outbound", "frame_length": 60, "count": 1}],
    }

    with pytest.raises(ValidationError, match="coefficient"):
        AcdPayload.model_validate(payload)


def test_generation_uses_stationary_prehistory_unit_innovations_and_mark_after_arrival() -> None:
    """Changing initialization, innovation scale, or draw order changes schema-5 output."""
    rng = ScriptedAcdRng(indices=[0, 1, 2], exponentials=[0.5, 1.0, 1.0])

    result = _generate_with_rng(_model(), rng, W=1.4, limits=LIMITS, clock=ScriptedClock([0.0] * 30))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.5, Direction.INBOUND, 100),
        TraceEvent(1.4, Direction.INBOUND, 100),
    )
    assert rng.calls == [
        ("choice", 4),
        ("exponential", 1.0),
        ("choice", 4),
        ("exponential", 1.0),
        ("choice", 4),
        ("exponential", 1.0),
    ]


@pytest.mark.parametrize(
    ("model", "exponentials", "window", "expected_timestamps"),
    (
        (
            AcdModel(
                omega=0.6,
                alpha=(0.2, 0.1),
                beta=(0.3, 0.1),
                diagnostics=AcdFitDiagnostics(2.0, 2.5, 7, True),
                marks=MARKS,
            ),
            (0.5, 2.0, 1.0, 10.0),
            7.0,
            (0.0, 1.0, 4.6, 6.76),
        ),
        (
            AcdModel(
                omega=0.23,
                alpha=(0.1, 0.05, 0.02),
                beta=(0.3, 0.2, 0.1),
                diagnostics=AcdFitDiagnostics(1.0, 2.5, 7, True),
                marks=MARKS,
            ),
            (0.5, 2.0, 1.0, 10.0),
            4.0,
            (0.0, 0.5, 2.4, 3.45),
        ),
    ),
    ids=("order-2", "order-3"),
)
def test_generation_preserves_multilag_duration_and_conditional_mean_history_order(
    model: AcdModel,
    exponentials: tuple[float, ...],
    window: float,
    expected_timestamps: tuple[float, ...],
) -> None:
    """Reversing either p=2 or p=3 history would change the third scripted arrival."""
    rng = ScriptedAcdRng(indices=[0, 0, 0, 0], exponentials=exponentials)

    result = _generate_with_rng(model, rng, W=window, limits=LIMITS, clock=ScriptedClock([0.0] * 40))

    events = result.require_complete().to_events()
    assert tuple(event.timestamp for event in events) == pytest.approx(expected_timestamps, abs=1e-15)
    assert tuple((event.direction, event.frame_length) for event in events) == ((Direction.OUTBOUND, 60),) * 4
    assert rng.calls == [
        ("choice", 4),
        ("exponential", 1.0),
        ("choice", 4),
        ("exponential", 1.0),
        ("choice", 4),
        ("exponential", 1.0),
        ("choice", 4),
        ("exponential", 1.0),
    ]


def test_generation_keeps_zero_iats_and_consumes_no_mark_after_window_crossing() -> None:
    """Zero innovations are valid, while an out-of-window arrival must not consume a mark."""
    rng = ScriptedAcdRng(indices=[0, 0], exponentials=[0.0, 1.0])

    result = _generate_with_rng(_model(), rng, W=0.1, limits=LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.0, Direction.OUTBOUND, 60),
    )
    assert rng.calls == [
        ("choice", 4),
        ("exponential", 1.0),
        ("choice", 4),
        ("exponential", 1.0),
    ]


def test_generation_checks_initial_and_later_packet_and_byte_guards() -> None:
    """Guard exhaustion must return diagnostic prefixes rather than shortened complete traces."""
    initial_byte = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=GenerationLimits(max_packets=2, max_output_bytes=59, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0] * 10),
    )
    later_packet = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=10_000, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0] * 10),
    )
    later_byte = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0, 0], exponentials=[0.5]),
        W=1.0,
        limits=GenerationLimits(max_packets=3, max_output_bytes=119, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0] * 20),
    )

    assert (initial_byte.complete, initial_byte.reason, initial_byte.trace) == (False, "max_output_bytes", ())
    assert (later_packet.complete, later_packet.reason) == (False, "max_packets")
    assert (later_byte.complete, later_byte.reason) == (False, "max_output_bytes")
    assert later_byte.trace.to_events() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)


def test_generation_checks_wall_guard_before_and_after_each_stochastic_draw() -> None:
    """A stochastic primitive cannot run outside the configured wall deadline."""
    before = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([math.inf]),
    )
    after_innovation = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0], exponentials=[0.5]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )
    after_initial_mark = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([0.0, 0.0, 10.0]),
    )
    after_mark = _generate_with_rng(
        _model(),
        ScriptedAcdRng(indices=[0, 0], exponentials=[0.5]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )

    assert (before.complete, before.reason, before.trace) == (False, "max_wall_seconds", ())
    assert (after_initial_mark.complete, after_initial_mark.reason) == (False, "max_wall_seconds")
    assert (after_innovation.complete, after_innovation.reason) == (False, "max_wall_seconds")
    assert (after_mark.complete, after_mark.reason) == (False, "max_wall_seconds")


@pytest.mark.parametrize("innovation", [math.nan, math.inf, -0.1, True])
def test_generation_rejects_invalid_unit_exponential_innovations(innovation: object) -> None:
    """A nonfinite, negative, or coercible innovation would corrupt recursion time."""
    with pytest.raises(TrafficlabError, match="innovation"):
        _generate_with_rng(
            _model(),
            ScriptedAcdRng(indices=[0], exponentials=[cast(float, innovation)]),
            W=1.0,
            limits=LIMITS,
            clock=ScriptedClock([0.0] * 20),
        )


def test_generation_rejects_invalid_empirical_choice_primitive() -> None:
    """An out-of-range scalar mark draw must not become a generated event."""
    with pytest.raises(TrafficlabError, match="empirical random draw"):
        _generate_with_rng(
            _model(),
            ScriptedAcdRng(indices=[4], exponentials=[]),
            W=1.0,
            limits=LIMITS,
            clock=ScriptedClock([0.0] * 10),
        )


def test_generation_rejects_overflowed_duration_or_arrival_time() -> None:
    """Finite primitives whose product overflows must fail explicitly."""
    model = AcdModel(
        omega=math.ldexp(1.0, 1023),
        alpha=(0.0,),
        beta=(0.0,),
        diagnostics=AcdFitDiagnostics(1.0, 2.5, 7, True),
        marks=MARKS,
    )
    with pytest.raises(TrafficlabError, match="duration|arrival time"):
        _generate_with_rng(
            model,
            ScriptedAcdRng(indices=[0], exponentials=[2.0]),
            W=1.0,
            limits=LIMITS,
            clock=ScriptedClock([0.0] * 20),
        )


@pytest.mark.parametrize("seed", [True, -1, 1.0])
def test_generate_rejects_noncanonical_seed_primitives(seed: object) -> None:
    """Coercible seeds would weaken byte-for-byte reproduction."""
    with pytest.raises(TrafficlabError, match="seed"):
        FAMILY.generate(_model(), seed, 2.0, LIMITS)  # type: ignore[arg-type]


@pytest.mark.parametrize("window", [True, 0.0, math.nan, math.inf])
def test_generate_rejects_invalid_window_primitives(window: object) -> None:
    """Generation uses the common finite positive closed-window contract."""
    with pytest.raises(TrafficlabError, match="observation window"):
        _generate_with_rng(
            _model(),
            ScriptedAcdRng(indices=[], exponentials=[]),
            W=window,  # type: ignore[arg-type]
            limits=LIMITS,
        )


def test_fitted_model_round_trips_strict_payload_and_rejects_corruption() -> None:
    """A loose payload could change order, stationarity, or joint marks after reload."""
    model = _model()
    payload = FAMILY.dump_fitted(model)

    assert payload == {
        "omega": 0.5,
        "alpha": [0.2],
        "beta": [0.3],
        "diagnostics": {
            "initial_conditional_duration": 1.0,
            "final_negative_log_likelihood": 2.5,
            "iterations": 7,
            "converged": True,
        },
        "marks": [
            {"direction": "outbound", "frame_length": 60, "count": 1},
            {"direction": "inbound", "frame_length": 100, "count": 3},
        ],
    }
    assert FAMILY.load_fitted(payload, genes=(1,), bounds=BOUNDS) == model
    first_mark = cast(list[object], payload["marks"])[0]
    malformed_payloads: tuple[object, ...] = (
        {**payload, "omega": 0.0},
        {**payload, "omega": 1},
        {**payload, "alpha": [-0.1]},
        {**payload, "alpha": [0.7], "beta": [0.3]},
        {**payload, "alpha": [0.2, 0.1]},
        {**payload, "diagnostics": {**cast(dict[str, object], payload["diagnostics"]), "converged": False}},
        {**payload, "diagnostics": {**cast(dict[str, object], payload["diagnostics"]), "iterations": 501}},
        {**payload, "marks": []},
        {**payload, "marks": [first_mark, first_mark]},
        {**payload, "extra": 1},
        {**payload, "omega": math.nan},
        {**payload, "omega": math.inf},
        {**payload, "marks": object()},
        {**payload, "marks": [object()]},
        {**payload, "marks": [{"direction": "outbound", "frame_length": 60, "count": 1, "extra": 1}]},
        {**payload, "marks": [{"direction": 1, "frame_length": 60, "count": 1}]},
        {**payload, "marks": [{"direction": "sideways", "frame_length": 60, "count": 1}]},
        {**payload, "marks": [{"direction": "outbound", "frame_length": 60, "count": 0}]},
    )
    for malformed in malformed_payloads:
        with pytest.raises(TrafficlabError):
            FAMILY.load_fitted(malformed, genes=(1,), bounds=BOUNDS)

    with pytest.raises(TrafficlabError, match="order"):
        FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS)

    with pytest.raises(TrafficlabError, match="payload"):
        FAMILY.load_fitted(object(), genes=(1,), bounds=BOUNDS)


def test_dump_revalidates_a_corrupted_frozen_runtime_model() -> None:
    """A bypassed dataclass mutation cannot escape through artifact publication."""
    model = _model()
    object.__setattr__(model, "alpha", (1.0,))

    with pytest.raises(TrafficlabError, match="fitted ACD model"):
        FAMILY.dump_fitted(model)


def test_generation_rejects_a_model_owned_by_another_family() -> None:
    """Interpreting another fitted family as ACD state would corrupt generation."""
    with pytest.raises(TypeError, match="AcdModel"):
        _generate_with_rng(
            cast(AcdModel, object()),
            ScriptedAcdRng(indices=[], exponentials=[]),
            W=1.0,
            limits=LIMITS,
        )


def test_same_seed_generation_is_identical_without_changing_module_global_rng() -> None:
    """Generation must own one local PCG64 stream."""
    np.random.seed(1234)
    expected = np.random.random(3)
    np.random.seed(1234)
    first = FAMILY.generate(_model(), 73, 2.0, LIMITS, clock=lambda: 0.0)
    second = FAMILY.generate(_model(), 73, 2.0, LIMITS, clock=lambda: 0.0)

    assert first == second
    assert np.array_equal(np.random.random(3), expected)
