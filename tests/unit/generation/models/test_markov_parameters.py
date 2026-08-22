"""Behavioral tests for one Markov renewal owner."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math

import pytest

from tests.support.markov_renewal import (
    BOUNDS,
    DISTINCT_REFERENCE,
    FAMILY,
)
from trafficlab.common.config import FloatBounds, IntegerBounds, MarkovRenewalConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.markov_renewal import (
    size_bin,
    type7_quantile,
)


def test_type7_thresholds_and_inclusive_bins() -> None:
    """Changing interpolation or bin inequalities would change observable states."""
    lengths = (10, 20, 30, 40)
    assert type7_quantile(lengths, 0.25) == 17.5
    assert type7_quantile(lengths, 0.75) == 32.5
    assert tuple(size_bin(length, 17.5, 32.5) for length in (10, 17, 18, 32, 33, 40)) == (0, 0, 1, 1, 2, 2)


@pytest.mark.parametrize(
    ("values", "q"),
    [
        (None, 0.5),
        ((), 0.5),
        ((1.0,), True),
        ((1.0,), math.nan),
        ((math.inf,), 0.5),
    ],
)
def test_type7_quantile_rejects_malformed_samples_and_levels(values: object, q: object) -> None:
    """Coercing an invalid sample or level would make state thresholds noncanonical."""
    with pytest.raises(TrafficlabError, match="quantile"):
        type7_quantile(values, q)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("frame_length", "lower", "upper"),
    [(True, 10.0, 20.0), (20, math.nan, 30.0), (20, 30.0, 30.0)],
)
def test_size_bin_rejects_noncanonical_inputs(frame_length: object, lower: float, upper: float) -> None:
    """Loose bin inputs would permit states fit could never construct."""
    with pytest.raises((TypeError, ValueError)):
        size_bin(frame_length, lower, upper)  # type: ignore[arg-type]


def test_repair_sorts_quantiles_before_named_clamping_and_rounds_r_half_up() -> None:
    """Clamping before sorting or banker's rounding would produce a different chromosome."""
    assert FAMILY.repair((0.8, 0.2, -1.0, 2.5, 10.0), BOUNDS, DISTINCT_REFERENCE) == (
        0.2,
        0.8,
        0.0,
        3,
        2.0,
    )


@pytest.mark.parametrize("r", [0.5, 1.0, 5.49, 9.5])
def test_repair_clamps_half_up_r_to_inclusive_integer_bounds(r: float) -> None:
    """Repair must preserve the configured inclusive integer coordinate domain."""
    repaired = FAMILY.repair((0.2, 0.8, 0.0, r, 1.0), BOUNDS, DISTINCT_REFERENCE)
    assert 1 <= repaired[3] <= 5
    assert type(repaired[3]) is int


@pytest.mark.parametrize(
    "genes",
    [
        (),
        (0.2, 0.8, 0.0, 2.0),
        (0.2, 0.8, 0.0, 2.0, 1.0, 9.0),
        (True, 0.8, 0.0, 2.0, 1.0),
        (0.2, 1, 0.0, 2.0, 1.0),
        (0.2, 0.8, math.nan, 2.0, 1.0),
        (0.2, 0.8, 0.0, math.inf, 1.0),
        (0.2, 0.8, 0.0, 2.0, -math.inf),
    ],
)
def test_repair_rejects_invalid_gene_arity_and_values(genes: tuple[object, ...]) -> None:
    """Coercing malformed genes would make persisted chromosomes ambiguous."""
    with pytest.raises(TrafficlabError):
        FAMILY.repair(genes, BOUNDS, DISTINCT_REFERENCE)  # type: ignore[arg-type]


def test_repair_rejects_named_clamping_that_destroys_quantile_order() -> None:
    """A sorted pair must still obey its named q1 and q2 domains after clamping."""
    crossing_bounds = MarkovRenewalConfig(
        q1=FloatBounds(lower=0.7, upper=0.8),
        q2=FloatBounds(lower=0.2, upper=0.3),
        alpha=FloatBounds(lower=0.0, upper=1.0),
        r=IntegerBounds(lower=1, upper=2),
        c_t=FloatBounds(lower=0.5, upper=2.0),
    )
    with pytest.raises(TrafficlabError, match="quantile"):
        FAMILY.repair((0.1, 0.9, 0.0, 1.0, 1.0), crossing_bounds, DISTINCT_REFERENCE)


def test_repair_rejects_equal_quantiles_and_duplicate_reference_thresholds() -> None:
    """Accepting either equality would collapse one of the three promised size bins."""
    overlapping_bounds = MarkovRenewalConfig(
        q1=FloatBounds(lower=0.2, upper=0.6),
        q2=FloatBounds(lower=0.4, upper=0.8),
        alpha=FloatBounds(lower=0.0, upper=1.0),
        r=IntegerBounds(lower=1, upper=2),
        c_t=FloatBounds(lower=0.5, upper=2.0),
    )
    with pytest.raises(TrafficlabError, match="quantile"):
        FAMILY.repair((0.5, 0.5, 0.0, 1.0, 1.0), overlapping_bounds, DISTINCT_REFERENCE)
    duplicate_lengths = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 60),
    )
    with pytest.raises(TrafficlabError, match="threshold"):
        FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), BOUNDS, TrafficTrace.from_events(duplicate_lengths))


@pytest.mark.parametrize("bounds", [object(), FloatBounds(lower=0.1, upper=0.9)])
def test_repair_rejects_foreign_bounds(bounds: object) -> None:
    """Using another family's bounds would silently assign the wrong chromosome domains."""
    with pytest.raises(TrafficlabError, match="bounds"):
        FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), bounds, DISTINCT_REFERENCE)  # type: ignore[arg-type]


@pytest.mark.parametrize("reference", [None, (), (TraceEvent(0.0, Direction.OUTBOUND, 20),), (object(), object())])
def test_repair_rejects_malformed_references(reference: object) -> None:
    """Repair must validate its materialized reference before deriving thresholds."""
    with pytest.raises(TrafficlabError, match="reference"):
        FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), BOUNDS, reference)  # type: ignore[arg-type]


def test_repair_revalidates_bounds_even_when_config_validation_was_bypassed() -> None:
    """Trusting a constructed config object would permit invalid genetic coordinate domains."""
    invalid_bounds = (
        BOUNDS.model_copy(update={"q1": FloatBounds.model_construct(lower=0.0, upper=0.4)}),
        BOUNDS.model_copy(update={"alpha": FloatBounds.model_construct(lower=-1.0, upper=2.0)}),
        BOUNDS.model_copy(update={"r": IntegerBounds.model_construct(lower=0, upper=5)}),
        BOUNDS.model_copy(update={"c_t": FloatBounds.model_construct(lower=0.0, upper=2.0)}),
    )
    for bounds in invalid_bounds:
        with pytest.raises(TrafficlabError, match="bounds"):
            FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), bounds, DISTINCT_REFERENCE)
