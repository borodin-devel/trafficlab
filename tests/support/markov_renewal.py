"""Immutable Markov renewal fixtures shared by owner-level tests."""

from __future__ import annotations

from collections.abc import Sequence

from trafficlab.common.config import FloatBounds, GenerationLimits, IntegerBounds, MarkovRenewalConfig
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.markov_renewal import MarkovRenewalFamily, MarkovRenewalModel

FAMILY = MarkovRenewalFamily()
BOUNDS = MarkovRenewalConfig(
    q1=FloatBounds(lower=0.1, upper=0.4),
    q2=FloatBounds(lower=0.6, upper=0.9),
    alpha=FloatBounds(lower=0.0, upper=2.0),
    r=IntegerBounds(lower=1, upper=5),
    c_t=FloatBounds(lower=0.5, upper=2.0),
)
DISTINCT_REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.INBOUND, 20),
        TraceEvent(1.0, Direction.OUTBOUND, 80),
        TraceEvent(2.0, Direction.INBOUND, 40),
        TraceEvent(3.0, Direction.OUTBOUND, 60),
    )
)
LARGE_LIMITS = GenerationLimits(max_packets=100, max_output_bytes=100_000, max_wall_seconds=10.0)


class ScriptedMarkovRng:
    """Expose every continuous and integer draw made by Markov generation."""

    def __init__(self, *, random_values: Sequence[float], indices: Sequence[int]) -> None:
        self._random_values = iter(random_values)
        self._indices = iter(indices)
        self.calls: list[tuple[str, int | None]] = []

    def random(self) -> float:
        self.calls.append(("random", None))
        return next(self._random_values)

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        return next(self._indices)


class ScriptedClock:
    """Place wall-clock boundaries exactly around stochastic draws."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def two_state_reference() -> TrafficTrace:
    """Return the two-state fixture used by fitted and generation behavior tests."""
    return TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 20),
            TraceEvent(1.0, Direction.INBOUND, 80),
        )
    )


def two_state_model(*, alpha: float = 0.0, minimum_support: float = 2.0) -> MarkovRenewalModel:
    """Fit the deterministic two-state fixture with the shared configuration."""
    return FAMILY.fit(
        two_state_reference(),
        (0.25, 0.75, alpha, minimum_support, 1.0),
        W=1.0,
        bounds=BOUNDS,
    )
