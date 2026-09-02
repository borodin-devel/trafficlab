"""Independent behavioral tests for exact Jensen--Shannon similarity."""

from collections import Counter
from fractions import Fraction
from math import log1p, log2, sqrt
from typing import cast

import pytest

from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.similarity.jensen_shannon import jensen_shannon_similarity


def _trace(
    timestamps: tuple[float, ...], lengths: tuple[int, ...], directions: tuple[Direction, ...]
) -> TrafficTrace:
    """Build one compact canonical trace without production metric helpers."""
    return TrafficTrace.from_events(
        TraceEvent(timestamp, direction, length)
        for timestamp, direction, length in zip(timestamps, directions, lengths, strict=True)
    )


def _entropy(counts: Counter[object]) -> float:
    """Compute base-2 entropy from exact rational masses for a tiny oracle PMF."""
    total = sum(counts.values())
    return -sum(float(Fraction(count, total)) * log2(float(Fraction(count, total))) for count in counts.values() if count)


def _jsd(reference: Counter[object], generated: Counter[object]) -> float:
    """Calculate JSD by the entropy identity independently of production counting."""
    reference_total = sum(reference.values())
    generated_total = sum(generated.values())
    mixture: Counter[object] = Counter()
    for category in set(reference) | set(generated):
        mixture[category] = reference[category] * generated_total + generated[category] * reference_total
    # mixture stores twice the common-denominator mixture mass; normalizing it is exact.
    return _entropy(mixture) - 0.5 * _entropy(reference) - 0.5 * _entropy(generated)


def test_identical_exact_joint_mark_and_iat_pmfs_score_one() -> None:
    """Any positive JSD for identical PMFs would make a perfect trace score below one."""
    trace = _trace(
        (0.0, 0.0, 1.0, 3.0),
        (60, 120, 60, 120),
        (Direction.OUTBOUND, Direction.INBOUND, Direction.OUTBOUND, Direction.INBOUND),
    )

    result = jensen_shannon_similarity(trace, trace, 3.0, 3, 0.25, 0.75)

    assert result.score == 1.0
    assert result.diagnostics["discrepancy"] == 0.0
    assert result.diagnostics["mark"]["jsd"] == 0.0  # type: ignore[index]
    assert result.diagnostics["iat"]["jsd"] == 0.0  # type: ignore[index]


def test_disjoint_joint_mark_pmfs_have_base_two_jsd_one_without_pseudocounts() -> None:
    """Changing the logarithm base or smoothing a zero mass changes this exact limit."""
    reference = _trace((0.0, 1.0), (60, 60), (Direction.OUTBOUND, Direction.OUTBOUND))
    generated = _trace((0.0, 1.0), (120, 120), (Direction.INBOUND, Direction.INBOUND))

    result = jensen_shannon_similarity(reference, generated, 1.0, 1, 0.0, 1.0)

    assert result.score == 0.0
    assert result.diagnostics["mark"]["jsd"] == pytest.approx(1.0)  # type: ignore[index]
    assert result.diagnostics["mark"]["reference_count"] == 2  # type: ignore[index]
    assert result.diagnostics["mark"]["generated_count"] == 2  # type: ignore[index]


def test_mark_jsd_matches_a_fraction_entropy_oracle_on_exact_direction_length_categories() -> None:
    """Collapsing direction or frame length would fail this independently counted PMF."""
    reference = _trace(
        (0.0, 1.0, 2.0, 3.0),
        (60, 60, 120, 120),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.INBOUND, Direction.INBOUND),
    )
    generated = _trace(
        (0.0, 1.0, 2.0, 3.0),
        (60, 120, 120, 120),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.INBOUND, Direction.INBOUND),
    )
    reference_counts: Counter[tuple[Direction, int]] = Counter((event.direction, event.frame_length) for event in reference)
    generated_counts = Counter((event.direction, event.frame_length) for event in generated)

    result = jensen_shannon_similarity(reference, generated, 3.0, 3, 0.0, 1.0)

    assert result.diagnostics["mark"]["jsd"] == pytest.approx(_jsd(reference_counts, generated_counts))  # type: ignore[index]
    categories = cast(tuple[object, ...], result.diagnostics["mark"]["categories"])  # type: ignore[index]
    assert len(categories) == 3


def test_iat_bin_edges_are_fixed_by_reference_window_and_include_generated_endpoints() -> None:
    """Rebinning from generated values or excluding an upper endpoint changes these counts."""
    reference = _trace(
        (0.0, sqrt(5.0) - 1.0, 4.0),
        (60, 60, 60),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.OUTBOUND),
    )
    generated = _trace(
        (0.0, 4.0),
        (60, 60),
        (Direction.OUTBOUND, Direction.OUTBOUND),
    )

    result = jensen_shannon_similarity(reference, generated, 4.0, 2, 1.0, 0.0)

    iat = cast(dict[str, object], result.diagnostics["iat"])
    assert iat["bin_edges"] == pytest.approx((0.0, log1p(4.0) / 2.0, log1p(4.0)))
    categories = cast(tuple[dict[str, object], ...], iat["categories"])
    assert categories == (
        {"direction": "outbound", "bin_index": 1, "reference_count": 2, "generated_count": 1},
    )


def test_generated_iat_at_an_internal_reference_bin_endpoint_uses_the_later_bin() -> None:
    """Using left-open bins would assign an exact generated boundary to the wrong category."""
    boundary = sqrt(5.0) - 1.0
    reference = _trace((0.0, 4.0), (60, 60), (Direction.OUTBOUND, Direction.OUTBOUND))
    generated = _trace(
        (0.0, boundary, boundary),
        (60, 60, 60),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.OUTBOUND),
    )

    result = jensen_shannon_similarity(reference, generated, 4.0, 2, 1.0, 0.0)

    categories = cast(tuple[dict[str, object], ...], result.diagnostics["iat"]["categories"])  # type: ignore[index]
    assert {"direction": "outbound", "bin_index": 1, "reference_count": 1, "generated_count": 1} in categories


def test_zero_mass_union_categories_use_only_defined_jsd_terms() -> None:
    """Evaluating a zero-mass logarithm would make this finite PMF comparison fail."""
    reference = _trace(
        (0.0, 1.0, 2.0), (60, 60, 60), (Direction.OUTBOUND, Direction.OUTBOUND, Direction.OUTBOUND)
    )
    generated = _trace(
        (0.0, 1.0, 2.0), (60, 60, 60), (Direction.INBOUND, Direction.INBOUND, Direction.INBOUND)
    )

    result = jensen_shannon_similarity(reference, generated, 2.0, 2, 0.5, 0.5)

    assert result.diagnostics["iat"]["jsd"] == pytest.approx(1.0)  # type: ignore[index]
    assert result.diagnostics["mark"]["jsd"] == pytest.approx(1.0)  # type: ignore[index]
    assert result.score == 0.0
