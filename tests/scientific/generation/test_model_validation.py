"""Bounded direct validation for the approved traffic-model families."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from tests.scientific.generation.oracles import (
    acd_conditional_means,
    acd_stationary_mean,
    acd_unit_innovations,
    empirical_cdf,
    empirical_mean,
    lag_one_covariance,
    markov_stationary_distribution,
    mmpp_moments,
    nhpp_bin_mean,
    nhpp_integrated_intensity,
    packet_train_oracle,
)
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.compatibility import identify_bytes
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
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace, normalize_reference, parse_capture_metadata
from trafficlab.generation.models import (
    FamilyBounds,
    FittedModel,
    Gene,
    ModelFamily,
    get_family,
    load_best_model,
    make_best_model,
    mmpp,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.models.acd import AcdFamily, AcdFitDiagnostics, AcdModel
from trafficlab.generation.models.common import GenerationResult, MarkCount, MarkDistribution
from trafficlab.generation.models.markov_packet_train import MarkovPacketTrainFamily, MarkovPacketTrainModel
from trafficlab.generation.models.markov_packet_train.model import fit_trace as fit_packet_train
from trafficlab.generation.models.markov_renewal import (
    MarkovRenewalFamily,
    MarkovRenewalModel,
    MarkovState,
    choose_holding_sample,
)
from trafficlab.generation.models.mmpp import MmppFamily, MmppModel
from trafficlab.generation.models.nhpp import NhppFamily, NhppModel
from trafficlab.generation.models.packet_hmm import (
    BaumWelchDiagnostics,
    PacketCategory,
    PacketHmmFamily,
    PacketHmmModel,
    PacketSample,
)
from trafficlab.generation.models.poisson import PoissonFamily, PoissonModel

_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_DATA = PIPELINE_FIXTURE_ROOT

_POISSON_SEEDS = (1103, 2207, 3301, 4409)
_MARKOV_SEEDS = (5101, 5209, 5303, 5413)
_MMPP_SEEDS = (7103, 7207, 7309, 7411)
_NHPP_SEEDS = (8101, 8209, 8303, 8411)
_ACD_SEEDS = (9103, 9209, 9301, 9403)

_POISSON_WINDOW = 4096.0
_POISSON_SAMPLE_SIZE = 12_000
_MARKOV_WINDOW = 80_000.0
_MARKOV_TRANSITIONS = 20_000
_MMPP_WINDOW = 4096.0
_MMPP_SAMPLE_SIZE = 10_000
_NHPP_WINDOW = 1200.0
_NHPP_BIN_WIDTH = 400.0
_ACD_WINDOW = 12_000.0
_ACD_SAMPLE_SIZE = 8_000

# These constants are fixed by architecture/TESTING.md, not selected from test output.
_POISSON_MEAN_RELATIVE_TOLERANCE = 0.05
_POISSON_CDF_TOLERANCE = 0.04
_POISSON_RATE_RELATIVE_TOLERANCE = 0.08
_MARKOV_TRANSITION_TOLERANCE = 0.025
_MARKOV_OCCUPANCY_TOLERANCE = 0.03
_MARKOV_HOLDING_RELATIVE_TOLERANCE = 0.05
_MMPP_ARRIVAL_MIX_TOLERANCE = 0.03
_MMPP_TIME_OCCUPANCY_TOLERANCE = 0.03
_MMPP_RATE_RELATIVE_TOLERANCE = 0.06
_MMPP_COVARIANCE_TOLERANCE = 0.015
_MARK_TOLERANCE = 0.03
_NHPP_COUNT_RELATIVE_TOLERANCE = 0.1
_ACD_MEAN_RELATIVE_TOLERANCE = 0.06
_ACD_INNOVATION_MEAN_TOLERANCE = 0.05

_LIMITS = GenerationLimits(
    max_packets=100_000,
    max_output_bytes=100_000_000,
    max_wall_seconds=30.0,
)
_MARKS = MarkDistribution(
    (
        MarkCount(Direction.OUTBOUND, 60, 1),
        MarkCount(Direction.INBOUND, 120, 3),
    )
)
_POISSON_MODEL = PoissonModel(base_rate=4.0, rate=4.0, marks=_MARKS)


def _markov_model() -> MarkovRenewalModel:
    """Build one constructor-valid empirical corpus with the prescribed kernel."""
    aa = (1.0, 3.0) * 12
    ab = (2.0, 4.0) * 3
    ba = (3.0, 5.0) * 3
    bb = (4.0, 6.0) * 7
    return MarkovRenewalModel(
        alpha=0.0,
        conditional_iats=((aa, ab), (ba, bb)),
        global_iats=aa + ab + ba + bb,
        minimum_support=2,
        states=(
            MarkovState(Direction.OUTBOUND, 0, (60,) * 31, aa + ab),
            MarkovState(Direction.INBOUND, 2, (120,) * 20, ba + bb),
        ),
        thresholds=(80.0, 100.0),
        time_scale=1.0,
        transition_rows=((0.8, 0.2), (0.3, 0.7)),
    )


_MARKOV_MODEL = _markov_model()
_MMPP_MODEL = MmppModel(q01=1.0, q10=3.0, lambda0=1.0, lambda1=9.0, marks=_MARKS)
_NHPP_LOW_MARKS = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1), MarkCount(Direction.INBOUND, 120, 3)))
_NHPP_HIGH_MARKS = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 3), MarkCount(Direction.INBOUND, 120, 1)))
_NHPP_MODEL = NhppModel(
    bin_edges=(0.0, 400.0, 800.0, 1200.0),
    rates=(2.0, 0.0, 4.0),
    integrated_intensity=2400.0,
    bin_marks=(_NHPP_LOW_MARKS, None, _NHPP_HIGH_MARKS),
    global_marks=_MARKS,
)
_ACD_MODEL = AcdModel(
    omega=0.4,
    alpha=(0.2,),
    beta=(0.4,),
    diagnostics=AcdFitDiagnostics(1.0, 2.5, 7, True),
    marks=_MARKS,
)


def _packet_train_model() -> MarkovPacketTrainModel:
    timestamps = (0.0, 1.0, 2.0, 12.0, 13.0, 14.0, 15.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0)
    trace = TrafficTrace.from_events(
        tuple(
            TraceEvent(timestamp, Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND, 60 + index)
            for index, timestamp in enumerate(timestamps)
        )
    )
    return fit_packet_train(trace, length_cap=4)


_PACKET_TRAIN_MODEL = _packet_train_model()


def _guard_packet_hmm_model() -> PacketHmmModel:
    return PacketHmmModel(
        additive_smoothing=0.001,
        convergence_tolerance=1e-8,
        diagnostics=BaumWelchDiagnostics(True, 1, (-2.0, -2.0)),
        emission_rows=((0.8, 0.2), (0.1, 0.9)),
        iat_quantiles=(1.0 / 3.0, 2.0 / 3.0),
        iat_thresholds=(5.0 / 3.0, 7.0 / 3.0),
        initial_marks=MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),)),
        initial_probabilities=(0.75, 0.25),
        initialization="fixed_cyclic_v1",
        maximum_iterations=100,
        reservoirs=((PacketSample(1.0, 70),), (PacketSample(3.0, 130),)),
        size_quantiles=(1.0 / 3.0, 2.0 / 3.0),
        size_thresholds=(90.0, 110.0),
        state_count=2,
        transition_rows=((0.75, 0.25), (0.25, 0.75)),
        vocabulary=(
            PacketCategory(1, Direction.INBOUND, 0),
            PacketCategory(3, Direction.OUTBOUND, 2),
        ),
    )


_PACKET_HMM_MODEL = _guard_packet_hmm_model()


def test_packet_train_three_train_fit_matches_independent_hand_oracle() -> None:
    """Segmentation, state tables, gaps, and position marks must share no production expectation helper."""
    timestamps = (0.0, 1.0, 2.0, 12.0, 13.0, 14.0, 15.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0)
    raw_marks = (
        ("outbound", 60),
        ("inbound", 70),
        ("outbound", 80),
        ("inbound", 90),
        ("outbound", 100),
        ("outbound", 100),
        ("inbound", 110),
        ("outbound", 60),
        ("outbound", 100),
        ("inbound", 70),
        ("outbound", 100),
        ("outbound", 100),
        ("inbound", 120),
    )
    oracle = packet_train_oracle(timestamps, raw_marks, length_cap=4)
    trace = TrafficTrace.from_events(
        tuple(
            TraceEvent(timestamp, Direction(direction), frame_length)
            for timestamp, (direction, frame_length) in zip(timestamps, raw_marks, strict=True)
        )
    )
    fitted = fit_packet_train(trace, length_cap=4)

    assert oracle.gap_threshold == pytest.approx(9.1)
    assert oracle.train_bounds == ((0, 3), (3, 7), (7, 13))
    assert oracle.state_order == (3, 4)
    assert oracle.initial_probabilities == pytest.approx((1.0 / 3.0, 2.0 / 3.0))
    for row in oracle.transition_rows:
        assert row == pytest.approx((1.0 / 3.0, 2.0 / 3.0))
    assert oracle.actual_lengths == ((3, (3,)), (4, (4, 6)))
    assert oracle.inter_gaps == (10.0, 10.0)

    assert fitted.gap_threshold == oracle.gap_threshold
    assert tuple(state.length_state for state in fitted.states) == oracle.state_order
    assert fitted.initial_probabilities == pytest.approx(oracle.initial_probabilities)
    for actual, expected in zip(fitted.transition_rows, oracle.transition_rows, strict=True):
        assert actual == pytest.approx(expected)
    assert tuple((state.length_state, state.actual_lengths) for state in fitted.states) == oracle.actual_lengths
    assert fitted.conditional_inter_train_gaps == oracle.conditional_inter_gaps
    assert fitted.global_inter_train_gaps == oracle.inter_gaps

    fitted_within = tuple(
        item
        for state in fitted.states
        for item in (
            (state.length_state, "first", ()),
            (state.length_state, "interior", state.within_gaps.interior),
            (state.length_state, "last", state.within_gaps.last),
        )
    )
    assert fitted_within == oracle.within_gaps
    fitted_marks = tuple(
        (
            state.length_state,
            position,
            tuple((entry.direction.value, entry.frame_length, entry.count) for entry in entries),
        )
        for state in fitted.states
        for position, distribution in (
            ("first", state.marks.first),
            ("interior", state.marks.interior),
            ("last", state.marks.last),
        )
        for entries in ((distribution.entries if distribution is not None else ()),)
    )
    oracle_marks = tuple(
        (state, position, tuple((direction, frame_length, count) for (direction, frame_length), count in entries))
        for state, position, entries in oracle.position_mark_counts
    )
    assert fitted_marks == oracle_marks


def _assert_close(
    case: str,
    *,
    seed: int,
    sample_size: int,
    expected: float,
    observed: float,
    tolerance: float,
) -> None:
    """Compare against a predeclared bound with a stable diagnostic payload."""
    assert abs(observed - expected) <= tolerance, (
        f"scientific-validation:{case}: seed={seed} sample_size={sample_size} "
        f"expected={expected:.12g} observed={observed:.12g} tolerance={tolerance:.12g}"
    )


def _assert_complete_trace(result: GenerationResult, *, window: float) -> tuple[TraceEvent, ...]:
    events = result.require_complete().to_events()
    assert events
    assert all(math.isfinite(event.timestamp) and 0.0 <= event.timestamp <= window for event in events)
    assert all(left.timestamp <= right.timestamp for left, right in zip(events, events[1:], strict=False))
    return events


def _mark_frequencies(events: tuple[TraceEvent, ...]) -> dict[tuple[Direction, int], float]:
    counts = Counter((event.direction, event.frame_length) for event in events)
    return {mark: count / len(events) for mark, count in counts.items()}


@pytest.mark.parametrize("seed", _NHPP_SEEDS)
def test_nhpp_matches_independent_per_bin_poisson_intensity_and_mark_oracles(seed: int) -> None:
    """Piecewise rates and active-bin marks must match their analytical finite-window laws."""
    events = _assert_complete_trace(
        NhppFamily().generate(_NHPP_MODEL, seed, _NHPP_WINDOW, _LIMITS, clock=lambda: 0.0),
        window=_NHPP_WINDOW,
    )
    generated = events[1:]
    counts = tuple(
        sum(
            left <= event.timestamp < right or (right == _NHPP_WINDOW and event.timestamp == right)
            for event in generated
        )
        for left, right in ((0.0, 400.0), (400.0, 800.0), (800.0, _NHPP_WINDOW))
    )
    expected_counts = tuple(nhpp_bin_mean(rate, _NHPP_BIN_WIDTH) for rate in _NHPP_MODEL.rates)
    for bin_index, (expected, observed) in enumerate(zip(expected_counts, counts, strict=True)):
        _assert_close(
            f"nhpp-bin-{bin_index}-count",
            seed=seed,
            sample_size=counts[bin_index],
            expected=expected,
            observed=float(observed),
            tolerance=max(1.0, expected * _NHPP_COUNT_RELATIVE_TOLERANCE),
        )
    _assert_close(
        "nhpp-integrated-intensity",
        seed=seed,
        sample_size=len(generated),
        expected=nhpp_integrated_intensity(_NHPP_MODEL.rates, _NHPP_BIN_WIDTH),
        observed=float(len(generated)),
        tolerance=nhpp_integrated_intensity(_NHPP_MODEL.rates, _NHPP_BIN_WIDTH) * _NHPP_COUNT_RELATIVE_TOLERANCE,
    )
    assert counts[1] == 0
    for selected, expected in ((events[: counts[0] + 1], 0.25), (events[counts[0] + 1 :], 0.75)):
        frequencies = _mark_frequencies(selected)
        _assert_close(
            "nhpp-active-bin-mark",
            seed=seed,
            sample_size=len(selected),
            expected=expected,
            observed=frequencies.get((Direction.OUTBOUND, 60), 0.0),
            tolerance=_MARK_TOLERANCE,
        )


def test_acd_independent_oracles_match_hand_recursion_stationary_mean_and_innovations() -> None:
    """The ACD scientific oracle must not duplicate a wrong production recurrence."""
    conditional_means = acd_conditional_means(
        (1.0, 0.0, 3.0),
        omega=0.5,
        alpha=(0.2,),
        beta=(0.3,),
        initial_mean=2.0,
    )

    assert conditional_means == pytest.approx((1.5, 1.15, 0.845), abs=1e-15)
    assert acd_unit_innovations((1.0, 0.0, 3.0), conditional_means) == pytest.approx(
        (2.0 / 3.0, 0.0, 3.0 / 0.845), abs=1e-15
    )
    assert acd_stationary_mean(0.5, (0.2,), (0.3,)) == 1.0


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
def test_acd_independent_multilag_oracle_preserves_distinct_lag_order(
    durations: tuple[float, ...],
    omega: float,
    alpha: tuple[float, ...],
    beta: tuple[float, ...],
    initial_mean: float,
    expected: tuple[float, ...],
) -> None:
    """Reversing any duration or conditional-mean lag must fail a literal multi-lag hand case."""
    assert acd_conditional_means(
        durations,
        omega=omega,
        alpha=alpha,
        beta=beta,
        initial_mean=initial_mean,
    ) == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize("seed", _ACD_SEEDS)
def test_acd_matches_stationary_mean_unit_innovation_and_joint_mark_oracles(seed: int) -> None:
    """Generated ACD durations and marks must satisfy the fixed independent acceptance matrix."""
    events = _assert_complete_trace(
        AcdFamily().generate(_ACD_MODEL, seed, _ACD_WINDOW, _LIMITS, clock=lambda: 0.0),
        window=_ACD_WINDOW,
    )
    assert len(events) >= _ACD_SAMPLE_SIZE + 1
    durations = tuple(
        right.timestamp - left.timestamp
        for left, right in zip(events[:_ACD_SAMPLE_SIZE], events[1 : _ACD_SAMPLE_SIZE + 1], strict=True)
    )
    stationary_mean = acd_stationary_mean(_ACD_MODEL.omega, _ACD_MODEL.alpha, _ACD_MODEL.beta)
    conditional_means = acd_conditional_means(
        durations,
        omega=_ACD_MODEL.omega,
        alpha=_ACD_MODEL.alpha,
        beta=_ACD_MODEL.beta,
        initial_mean=stationary_mean,
    )
    innovations = acd_unit_innovations(durations, conditional_means)
    _assert_close(
        "acd-stationary-mean",
        seed=seed,
        sample_size=len(durations),
        expected=stationary_mean,
        observed=empirical_mean(durations),
        tolerance=stationary_mean * _ACD_MEAN_RELATIVE_TOLERANCE,
    )
    _assert_close(
        "acd-unit-innovation-mean",
        seed=seed,
        sample_size=len(innovations),
        expected=1.0,
        observed=empirical_mean(innovations),
        tolerance=_ACD_INNOVATION_MEAN_TOLERANCE,
    )
    frequencies = _mark_frequencies(events[:_ACD_SAMPLE_SIZE])
    for mark, expected in (
        ((Direction.OUTBOUND, 60), 0.25),
        ((Direction.INBOUND, 120), 0.75),
    ):
        _assert_close(
            f"acd-mark-{mark[0].value}-{mark[1]}",
            seed=seed,
            sample_size=_ACD_SAMPLE_SIZE,
            expected=expected,
            observed=frequencies.get(mark, 0.0),
            tolerance=_MARK_TOLERANCE,
        )


@pytest.mark.parametrize("seed", _POISSON_SEEDS)
def test_poisson_matches_exponential_rate_and_joint_mark_oracles(seed: int) -> None:
    """Production Poisson samples must satisfy the fixed analytical acceptance matrix."""
    events = _assert_complete_trace(
        PoissonFamily().generate(
            _POISSON_MODEL,
            seed,
            _POISSON_WINDOW,
            _LIMITS,
            clock=lambda: 0.0,
        ),
        window=_POISSON_WINDOW,
    )
    assert len(events) >= _POISSON_SAMPLE_SIZE + 1
    interarrivals = tuple(
        right.timestamp - left.timestamp
        for left, right in zip(
            events[:_POISSON_SAMPLE_SIZE],
            events[1 : _POISSON_SAMPLE_SIZE + 1],
            strict=True,
        )
    )
    expected_mean = 1.0 / _POISSON_MODEL.rate
    _assert_close(
        "poisson-mean-interarrival",
        seed=seed,
        sample_size=len(interarrivals),
        expected=expected_mean,
        observed=empirical_mean(interarrivals),
        tolerance=expected_mean * _POISSON_MEAN_RELATIVE_TOLERANCE,
    )
    _assert_close(
        "poisson-cdf-at-mean",
        seed=seed,
        sample_size=len(interarrivals),
        expected=1.0 - math.exp(-1.0),
        observed=empirical_cdf(interarrivals, expected_mean),
        tolerance=_POISSON_CDF_TOLERANCE,
    )
    _assert_close(
        "poisson-window-rate",
        seed=seed,
        sample_size=len(events),
        expected=_POISSON_MODEL.rate,
        observed=(len(events) - 1) / _POISSON_WINDOW,
        tolerance=_POISSON_MODEL.rate * _POISSON_RATE_RELATIVE_TOLERANCE,
    )
    frequencies = _mark_frequencies(events[:_POISSON_SAMPLE_SIZE])
    for mark, expected in (
        ((Direction.OUTBOUND, 60), 0.25),
        ((Direction.INBOUND, 120), 0.75),
    ):
        _assert_close(
            f"poisson-mark-{mark[0].value}-{mark[1]}",
            seed=seed,
            sample_size=_POISSON_SAMPLE_SIZE,
            expected=expected,
            observed=frequencies.get(mark, 0.0),
            tolerance=_MARK_TOLERANCE,
        )


def _state_index(event: TraceEvent) -> int:
    if (event.direction, event.frame_length) == (Direction.OUTBOUND, 60):
        return 0
    if (event.direction, event.frame_length) == (Direction.INBOUND, 120):
        return 1
    raise AssertionError(f"unexpected Markov state mark: {event!r}")


@pytest.mark.parametrize("seed", _MARKOV_SEEDS)
def test_markov_matches_kernel_occupancy_holding_and_mark_oracles(seed: int) -> None:
    """Production Markov samples must satisfy kernel and renewal-law expectations."""
    events = _assert_complete_trace(
        MarkovRenewalFamily().generate(
            _MARKOV_MODEL,
            seed,
            _MARKOV_WINDOW,
            _LIMITS,
            clock=lambda: 0.0,
        ),
        window=_MARKOV_WINDOW,
    )
    assert len(events) >= _MARKOV_TRANSITIONS + 1
    transitions = tuple(zip(events[:_MARKOV_TRANSITIONS], events[1 : _MARKOV_TRANSITIONS + 1], strict=True))
    counts = Counter((_state_index(source), _state_index(destination)) for source, destination in transitions)
    source_counts = Counter(_state_index(source) for source, _destination in transitions)
    kernel = ((0.8, 0.2), (0.3, 0.7))
    for source in range(2):
        for destination in range(2):
            _assert_close(
                f"markov-transition-{source}-{destination}",
                seed=seed,
                sample_size=source_counts[source],
                expected=kernel[source][destination],
                observed=counts[source, destination] / source_counts[source],
                tolerance=_MARKOV_TRANSITION_TOLERANCE,
            )

    stationary = markov_stationary_distribution(kernel)
    for state in range(2):
        _assert_close(
            f"markov-occupancy-{state}",
            seed=seed,
            sample_size=_MARKOV_TRANSITIONS,
            expected=stationary[state],
            observed=source_counts[state] / _MARKOV_TRANSITIONS,
            tolerance=_MARKOV_OCCUPANCY_TOLERANCE,
        )

    holding_tables = (((1.0, 3.0), (2.0, 4.0)), ((3.0, 5.0), (4.0, 6.0)))
    holding_observations: dict[tuple[int, int], list[float]] = {
        (0, 0): [],
        (0, 1): [],
        (1, 0): [],
        (1, 1): [],
    }
    for source, destination in transitions:
        holding_observations[_state_index(source), _state_index(destination)].append(
            destination.timestamp - source.timestamp
        )
    for state_pair, values in holding_observations.items():
        expected = empirical_mean(holding_tables[state_pair[0]][state_pair[1]])
        _assert_close(
            f"markov-holding-{state_pair[0]}-{state_pair[1]}",
            seed=seed,
            sample_size=len(values),
            expected=expected,
            observed=empirical_mean(values),
            tolerance=expected * _MARKOV_HOLDING_RELATIVE_TOLERANCE,
        )

    frequencies = _mark_frequencies(events[:_MARKOV_TRANSITIONS])
    for mark, expected in (
        ((Direction.OUTBOUND, 60), stationary[0]),
        ((Direction.INBOUND, 120), stationary[1]),
    ):
        _assert_close(
            f"markov-mark-{mark[0].value}-{mark[1]}",
            seed=seed,
            sample_size=_MARKOV_TRANSITIONS,
            expected=expected,
            observed=frequencies.get(mark, 0.0),
            tolerance=_MARK_TOLERANCE,
        )


def test_markov_holding_fallbacks_select_source_then_global_samples() -> None:
    """Under-supported conditional timing must use both documented fallbacks in order."""
    assert choose_holding_sample((1.0,), (7.0, 9.0), (11.0, 13.0), minimum_support=2) == (7.0, 9.0)
    assert choose_holding_sample((), (), (11.0, 13.0), minimum_support=2) == (11.0, 13.0)


def test_hand_derived_two_state_oracles_match_exact_rational_values() -> None:
    """Independent closed forms must retain the architecture's exact check values."""
    assert markov_stationary_distribution(((0.8, 0.2), (0.3, 0.7))) == pytest.approx((0.6, 0.4))
    oracle = mmpp_moments(q01=1, q10=3, lambda0=1, lambda1=9)
    assert oracle.time_stationary == (Fraction(3, 4), Fraction(1, 4))
    assert oracle.arrival_epoch == (Fraction(1, 4), Fraction(3, 4))
    assert oracle.mean_rate == 3
    assert oracle.mean_iat == Fraction(1, 3)
    assert oracle.adjacent_iat_covariance == Fraction(4, 147)


@dataclass(frozen=True, slots=True)
class _Race:
    regime: int
    arrival_delay: float
    transition_delay: float


class _RecordingMmppRng:
    """Delegate to the production-seeded RNG while recording latent race draws."""

    def __init__(self, random_source: np.random.Generator) -> None:
        self._random_source = random_source
        self.initial_draw: float | None = None
        self.races: list[_Race] = []
        self._pending: tuple[int, float] | None = None

    def random(self) -> float:
        value = self._random_source.random()
        self.initial_draw = value
        return value

    def choice(self, a: int) -> int:
        return int(self._random_source.choice(a))

    def exponential(self, scale: float) -> float:
        value = self._random_source.exponential(scale)
        rate = 1.0 / scale
        if self._pending is None:
            if rate == _MMPP_MODEL.lambda0:
                regime = 0
            elif rate == _MMPP_MODEL.lambda1:
                regime = 1
            else:
                raise AssertionError(f"unexpected MMPP arrival rate {rate}")
            self._pending = (regime, value)
        else:
            regime, arrival_delay = self._pending
            expected_rate = _MMPP_MODEL.q01 if regime == 0 else _MMPP_MODEL.q10
            if rate != expected_rate:
                raise AssertionError(f"unexpected MMPP transition rate {rate}")
            self.races.append(_Race(regime, arrival_delay, value))
            self._pending = None
        return value


def _mmpp_observations(
    recorder: _RecordingMmppRng,
    *,
    window: float,
    arrival_threshold: float,
) -> tuple[tuple[int, ...], tuple[float, float], tuple[float, ...]]:
    if recorder.initial_draw is None:
        raise AssertionError("MMPP generator did not draw an initial regime")
    arrival_regimes = [0 if recorder.initial_draw < arrival_threshold else 1]
    arrival_times = [0.0]
    occupied = [0.0, 0.0]
    current_time = 0.0
    for race in recorder.races:
        duration = min(race.arrival_delay, race.transition_delay)
        retained = min(duration, window - current_time)
        occupied[race.regime] += retained
        if current_time + duration > window:
            break
        current_time += duration
        if race.arrival_delay < race.transition_delay:
            arrival_regimes.append(race.regime)
            arrival_times.append(current_time)
    assert math.isclose(sum(occupied), window, rel_tol=0.0, abs_tol=1e-9)
    return (
        tuple(arrival_regimes),
        (occupied[0] / window, occupied[1] / window),
        tuple(arrival_times),
    )


@pytest.mark.parametrize("seed", _MMPP_SEEDS)
def test_mmpp_matches_arrival_epoch_ctmc_rate_covariance_and_mark_oracles(
    seed: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production MMPP races must satisfy independent MAP and CTMC moments."""
    recorders: list[_RecordingMmppRng] = []

    def recording_rng(seed: int) -> _RecordingMmppRng:
        recorder = _RecordingMmppRng(np.random.Generator(np.random.PCG64(seed)))
        recorders.append(recorder)
        return recorder

    monkeypatch.setattr(mmpp, "make_rng", recording_rng)
    events = _assert_complete_trace(
        MmppFamily().generate(
            _MMPP_MODEL,
            seed,
            _MMPP_WINDOW,
            _LIMITS,
            clock=lambda: 0.0,
        ),
        window=_MMPP_WINDOW,
    )
    assert len(recorders) == 1
    oracle = mmpp_moments(q01=1, q10=3, lambda0=1, lambda1=9)
    arrival_regimes, time_occupancy, arrival_times = _mmpp_observations(
        recorders[0],
        window=_MMPP_WINDOW,
        arrival_threshold=float(oracle.arrival_epoch[0]),
    )
    assert len(arrival_regimes) == len(events)
    assert arrival_times == tuple(event.timestamp for event in events)
    assert len(events) >= _MMPP_SAMPLE_SIZE + 1

    epoch_counts = Counter(arrival_regimes[:_MMPP_SAMPLE_SIZE])
    for regime in range(2):
        _assert_close(
            f"mmpp-arrival-epoch-{regime}",
            seed=seed,
            sample_size=_MMPP_SAMPLE_SIZE,
            expected=float(oracle.arrival_epoch[regime]),
            observed=epoch_counts[regime] / _MMPP_SAMPLE_SIZE,
            tolerance=_MMPP_ARRIVAL_MIX_TOLERANCE,
        )
        _assert_close(
            f"mmpp-time-occupancy-{regime}",
            seed=seed,
            sample_size=len(recorders[0].races),
            expected=float(oracle.time_stationary[regime]),
            observed=time_occupancy[regime],
            tolerance=_MMPP_TIME_OCCUPANCY_TOLERANCE,
        )

    _assert_close(
        "mmpp-window-rate",
        seed=seed,
        sample_size=len(events),
        expected=float(oracle.mean_rate),
        observed=(len(events) - 1) / _MMPP_WINDOW,
        tolerance=float(oracle.mean_rate) * _MMPP_RATE_RELATIVE_TOLERANCE,
    )
    interarrivals = tuple(
        right.timestamp - left.timestamp
        for left, right in zip(
            events[:_MMPP_SAMPLE_SIZE],
            events[1 : _MMPP_SAMPLE_SIZE + 1],
            strict=True,
        )
    )
    _assert_close(
        "mmpp-adjacent-iat-covariance",
        seed=seed,
        sample_size=len(interarrivals),
        expected=float(oracle.adjacent_iat_covariance),
        observed=lag_one_covariance(interarrivals),
        tolerance=_MMPP_COVARIANCE_TOLERANCE,
    )
    frequencies = _mark_frequencies(events[:_MMPP_SAMPLE_SIZE])
    for mark, expected in (
        ((Direction.OUTBOUND, 60), 0.25),
        ((Direction.INBOUND, 120), 0.75),
    ):
        _assert_close(
            f"mmpp-mark-{mark[0].value}-{mark[1]}",
            seed=seed,
            sample_size=_MMPP_SAMPLE_SIZE,
            expected=expected,
            observed=frequencies.get(mark, 0.0),
            tolerance=_MARK_TOLERANCE,
        )


def test_mmpp_arrival_epoch_normalization_is_finite_at_float_limit() -> None:
    """The production normalization must remain finite without multiplying rates."""
    below_maximum = math.nextafter(sys.float_info.max, 0.0)
    probabilities = mmpp._arrival_epoch_probabilities(
        sys.float_info.max,
        below_maximum,
        below_maximum,
        sys.float_info.max,
    )
    assert all(math.isfinite(value) and 0.0 < value < 1.0 for value in probabilities)
    assert math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-12)


class _DeadlineClock:
    def __init__(self) -> None:
        self._values = iter((0.0, 2.0))

    def __call__(self) -> float:
        return next(self._values)


def _family_models() -> tuple[tuple[ModelFamily, FittedModel], ...]:
    return (
        (PoissonFamily(), _POISSON_MODEL),
        (MarkovRenewalFamily(), _MARKOV_MODEL),
        (MmppFamily(), _MMPP_MODEL),
        (AcdFamily(), _ACD_MODEL),
        (MarkovPacketTrainFamily(), _PACKET_TRAIN_MODEL),
        (PacketHmmFamily(), _PACKET_HMM_MODEL),
    )


@pytest.mark.parametrize(
    ("family", "model"),
    _family_models(),
    ids=("poisson_empirical", "markov_renewal", "mmpp", "acd", "markov_packet_train", "packet_hmm"),
)
def test_each_family_reports_all_resource_guards_as_incomplete(
    family: ModelFamily,
    model: FittedModel,
) -> None:
    """A bounded abort is not scientific completion for any model family."""
    cases: tuple[tuple[str, GenerationLimits, Callable[[], float]], ...] = (
        (
            "max_packets",
            GenerationLimits(max_packets=1, max_output_bytes=100_000, max_wall_seconds=10.0),
            lambda: 0.0,
        ),
        (
            "max_output_bytes",
            GenerationLimits(max_packets=100, max_output_bytes=1, max_wall_seconds=10.0),
            lambda: 0.0,
        ),
        (
            "max_wall_seconds",
            GenerationLimits(max_packets=100, max_output_bytes=100_000, max_wall_seconds=1.0),
            _DeadlineClock(),
        ),
    )
    for reason, limits, clock in cases:
        result = family.generate(model, 1234, 100.0, limits, clock=clock)
        assert result.complete is False
        assert result.reason == reason


def _artifact_inputs(family_name: str) -> tuple[tuple[Gene, ...], FamilyBounds]:
    if family_name == "poisson_empirical":
        return (
            (1.0,),
            PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0)),
        )
    if family_name == "markov_renewal":
        return (
            (0.25, 0.75, 0.0, 1, 1.0),
            MarkovRenewalConfig(
                q1=FloatBounds(lower=0.1, upper=0.4),
                q2=FloatBounds(lower=0.6, upper=0.9),
                alpha=FloatBounds(lower=0.0, upper=2.0),
                r=IntegerBounds(lower=1, upper=8),
                c_t=FloatBounds(lower=0.25, upper=4.0),
            ),
        )
    if family_name == "nhpp":
        return ((2,), NhppConfig(bin_count=IntegerBounds(lower=2, upper=4)))
    if family_name == "acd":
        return ((1,), AcdConfig(order=IntegerBounds(lower=1, upper=3)))
    if family_name == "markov_packet_train":
        return ((3,), MarkovPacketTrainConfig(length_cap=IntegerBounds(lower=3, upper=8)))
    if family_name == "packet_hmm":
        return ((2,), PacketHmmConfig(state_count=IntegerBounds(lower=2, upper=4)))
    return (
        (1.0, 3.0, 1.0, 9.0),
        MmppConfig(
            q01=FloatBounds(lower=0.01, upper=10.0),
            q10=FloatBounds(lower=0.01, upper=10.0),
            lambda0=FloatBounds(lower=0.01, upper=100.0),
            lambda1=FloatBounds(lower=0.1, upper=1000.0),
        ),
    )


@pytest.mark.parametrize(
    "family_name",
    ("poisson_empirical", "markov_renewal", "mmpp", "nhpp", "acd", "markov_packet_train", "packet_hmm"),
)
def test_current_schema_model_and_pcapng_round_trip_for_every_family(family_name: str) -> None:
    """Every production family must reload and reproduce canonical bytes at its stored window."""
    reference_path = _EXAMPLE_DATA / "reference.pcapng"
    metadata_path = _EXAMPLE_DATA / "capture.json"
    reference_content = reference_path.read_bytes()
    metadata_content = metadata_path.read_bytes()
    metadata = parse_capture_metadata(metadata_content, source=metadata_path)
    parsed_reference = read_pcapng_bytes(reference_content, metadata, source=reference_path)
    reference, window = normalize_reference(parsed_reference)
    assert isinstance(reference, TrafficTrace)
    genes, bounds = _artifact_inputs(family_name)
    family = get_family(family_name)
    best = make_best_model(
        family,
        reference,
        genes,
        reference_identity=identify_bytes(reference_content),
        capture_identity=identify_bytes(metadata_content),
        final_seed=54321,
        final_limits=_LIMITS,
        W=window,
        bounds=bounds,
    )
    rendered = render_best_model(best)
    loaded = load_best_model(rendered, source=Path(f"{family_name}-best_model.json"))
    assert loaded.scientific_artifact_schema == SCIENTIFIC_ARTIFACT_SCHEMA_VERSION == 5
    assert render_best_model(loaded) == rendered

    first = family.generate(
        runtime_fitted_model(loaded), 54321, loaded.observation_window_seconds, _LIMITS, clock=lambda: 0.0
    )
    second = family.generate(
        runtime_fitted_model(loaded), 54321, loaded.observation_window_seconds, _LIMITS, clock=lambda: 0.0
    )
    first_events = _assert_complete_trace(first, window=loaded.observation_window_seconds)
    second_events = _assert_complete_trace(second, window=loaded.observation_window_seconds)
    first_pcapng = encode_pcapng(first_events, metadata)
    second_pcapng = encode_pcapng(second_events, metadata)
    reparsed = read_pcapng_bytes(first_pcapng, metadata, source=Path(f"{family_name}-generated.pcapng"))

    assert first_events == second_events
    assert first_pcapng == second_pcapng
    assert encode_pcapng(reparsed, metadata) == first_pcapng
