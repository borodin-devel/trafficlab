"""One behavioral contract applied unchanged to every registered model family."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from trafficlab.compatibility import ContentIdentity
from trafficlab.config import (
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovRenewalConfig,
    MmppConfig,
    PoissonConfig,
)
from trafficlab.errors import TrafficlabError
from trafficlab.models import FamilyBounds, Genes, ModelFamily
from trafficlab.models.registry import (
    MARKOV_RENEWAL_FAMILY,
    MMPP_FAMILY,
    POISSON_FAMILY,
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.trace import Direction, TraceEvent, TrafficTrace

WINDOW = 10.0
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 100),
        TraceEvent(3.0, Direction.OUTBOUND, 140),
        TraceEvent(6.0, Direction.INBOUND, 80),
        TraceEvent(WINDOW, Direction.OUTBOUND, 60),
    )
)
COMPLETE_LIMITS = GenerationLimits(max_packets=10_000, max_output_bytes=10_000_000, max_wall_seconds=10.0)


@dataclass(frozen=True, slots=True)
class FamilyCase:
    name: str
    family: ModelFamily
    genes: Genes
    bounds: FamilyBounds


FAMILY_CASES = (
    FamilyCase(
        "poisson_empirical",
        POISSON_FAMILY,
        (1.0,),
        PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0)),
    ),
    FamilyCase(
        "markov_renewal",
        MARKOV_RENEWAL_FAMILY,
        (0.25, 0.75, 0.5, 2, 1.0),
        MarkovRenewalConfig(
            q1=FloatBounds(lower=0.1, upper=0.4),
            q2=FloatBounds(lower=0.6, upper=0.9),
            alpha=FloatBounds(lower=0.0, upper=2.0),
            r=IntegerBounds(lower=1, upper=8),
            c_t=FloatBounds(lower=0.25, upper=4.0),
        ),
    ),
    FamilyCase(
        "mmpp",
        MMPP_FAMILY,
        (0.5, 0.75, 0.25, 1.0),
        MmppConfig(
            q01=FloatBounds(lower=0.01, upper=10.0),
            q10=FloatBounds(lower=0.01, upper=10.0),
            lambda0=FloatBounds(lower=0.01, upper=100.0),
            lambda1=FloatBounds(lower=0.1, upper=1000.0),
        ),
    ),
)


@pytest.mark.parametrize("case", FAMILY_CASES, ids=lambda case: case.name)
def test_every_family_round_trips_and_reproduces(case: FamilyCase) -> None:
    """Family-specific codecs or RNG ownership must not weaken the shared reproducibility contract."""
    artifact = make_best_model(
        case.family,
        REFERENCE,
        case.genes,
        reference_identity=ContentIdentity(size=1, sha256="a" * 64),
        capture_identity=ContentIdentity(size=2, sha256="b" * 64),
        final_seed=2468,
        final_limits=COMPLETE_LIMITS,
        W=WINDOW,
        bounds=case.bounds,
    )
    loaded = load_best_model(render_best_model(artifact), source=Path("best_model.json"))
    first = case.family.generate(runtime_fitted_model(loaded), 2468, WINDOW, COMPLETE_LIMITS).require_complete()
    second = case.family.generate(runtime_fitted_model(loaded), 2468, WINDOW, COMPLETE_LIMITS).require_complete()
    assert first == second
    assert first[0].timestamp == 0.0
    assert all(left.timestamp <= right.timestamp for left, right in zip(first, first[1:], strict=False))
    assert first[-1].timestamp <= WINDOW
    assert all(14 <= event.frame_length <= 2**32 - 1 for event in first)
    assert loaded.observation_window_seconds == WINDOW


@pytest.mark.parametrize("case", FAMILY_CASES, ids=lambda case: case.name)
@pytest.mark.parametrize(
    ("limits", "clock", "reason"),
    [
        (
            GenerationLimits(max_packets=1, max_output_bytes=10_000_000, max_wall_seconds=10.0),
            None,
            "max_packets",
        ),
        (
            GenerationLimits(max_packets=10_000, max_output_bytes=14, max_wall_seconds=10.0),
            None,
            "max_output_bytes",
        ),
        (
            GenerationLimits(max_packets=10_000, max_output_bytes=10_000_000, max_wall_seconds=10.0),
            lambda: math.nan,
            "max_wall_seconds",
        ),
    ],
    ids=("packet-guard", "byte-guard", "wall-guard"),
)
def test_every_family_reports_each_incomplete_reason(
    case: FamilyCase,
    limits: GenerationLimits,
    clock: object,
    reason: str,
) -> None:
    """A family that returns a shortened trace would bias heterogeneous fitness comparisons."""
    fitted = case.family.fit(REFERENCE, case.genes, W=WINDOW, bounds=case.bounds)
    if clock is None:
        result = case.family.generate(fitted, 2468, WINDOW, limits)
    else:
        result = case.family.generate(fitted, 2468, WINDOW, limits, clock=clock)  # type: ignore[arg-type]
    assert result.complete is False
    assert result.reason == reason
    expected_detail = "generation exceeded the configured packet limit" if reason == "max_packets" else reason
    with pytest.raises(TrafficlabError, match=expected_detail):
        result.require_complete()
