"""Independent unit tests for bounded pooled-ECDF similarity methods."""

import math
from collections import Counter
from collections.abc import Callable, Mapping
from typing import cast

import numpy as np
import pytest

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.similarity.common import SimilarityResult
from trafficlab.comparison.similarity.ecdf import (
    EcdfSampleResult,
    anderson_darling_similarity,
    bounded_ad_sample,
    bounded_cvm_sample,
    cramer_von_mises_similarity,
)

type _SampleMethod = Callable[[tuple[float, ...], tuple[float, ...]], EcdfSampleResult]
type _SimilarityMethod = Callable[[TrafficTrace, TrafficTrace, float, float, float], SimilarityResult]


def _events(
    timestamps: tuple[float, ...],
    lengths: tuple[int, ...],
    directions: tuple[Direction, ...] | None = None,
) -> TrafficTrace:
    """Create one compact canonical trace with explicit marks."""
    event_directions = directions if directions is not None else (Direction.OUTBOUND,) * len(timestamps)
    return TrafficTrace.from_events(
        TraceEvent(timestamp, direction, length)
        for timestamp, direction, length in zip(timestamps, event_directions, lengths, strict=True)
    )


def _independent_ecdf_oracle(
    left: tuple[float, ...], right: tuple[float, ...], *, tail_weighted: bool
) -> tuple[float, float, float]:
    """Direct support/count scan kept independent from production ECDF helpers."""
    left_counts = Counter(left)
    right_counts = Counter(right)
    left_seen = 0
    right_seen = 0
    numerator = 0.0
    denominator = 0.0
    total_count = len(left) + len(right)
    for value in sorted(set(left_counts) | set(right_counts)):
        left_seen += left_counts[value]
        right_seen += right_counts[value]
        difference = left_seen / len(left) - right_seen / len(right)
        pooled_mass = (left_counts[value] + right_counts[value]) / total_count
        if tail_weighted:
            pooled_cdf = (left_seen + right_seen) / total_count
            if pooled_cdf == 1.0:
                continue
            weight = 1.0 / (pooled_cdf * (1.0 - pooled_cdf))
        else:
            weight = pooled_mass
        numerator += weight * difference**2
        denominator += weight
    return numerator, denominator, numerator / denominator if denominator else 0.0


def test_cvm_hand_case_uses_pooled_empirical_mass() -> None:
    """A missing pooled-mass factor would make this documented arithmetic fail."""
    result = bounded_cvm_sample((1.0, 2.0), (1.0, 3.0))

    assert result.discrepancy == pytest.approx(0.0625)
    assert result.raw_sum == pytest.approx(0.0625)
    assert result.normalization_weight == 1.0


def test_cvm_consumes_all_tied_values_before_one_ecdf_comparison() -> None:
    """Comparing within a tie run would overstate the stated ECDF distance."""
    result = bounded_cvm_sample((1.0, 1.0, 2.0), (1.0, 2.0, 2.0))

    assert result.discrepancy == pytest.approx(1.0 / 18.0)
    assert result.reference_tie_count == 1
    assert result.generated_tie_count == 1


def test_disjoint_singletons_retain_each_documented_bounded_discrepancy() -> None:
    """Dropping CvM pooled mass or AD endpoint normalization changes these limits."""
    assert bounded_cvm_sample((1.0,), (2.0,)).discrepancy == 0.5
    assert bounded_ad_sample((1.0,), (2.0,)).discrepancy == 1.0


def test_ad_gives_more_weight_to_an_equally_sized_tail_difference() -> None:
    """Replacing AD tail weights with CvM masses would reverse this ordering."""
    lower_tail = bounded_ad_sample((0.0, 2.0, 3.0, 4.0), (1.0, 2.0, 3.0, 4.0)).discrepancy
    central = bounded_ad_sample((0.0, 1.0, 3.0, 4.0), (0.0, 2.0, 3.0, 4.0)).discrepancy

    assert lower_tail > central


@pytest.mark.parametrize("method", [bounded_cvm_sample, bounded_ad_sample])
def test_sample_methods_match_an_independent_support_scan_on_small_random_samples(
    method: _SampleMethod,
) -> None:
    """A shared support-scan bug cannot satisfy this separately counted oracle."""
    rng = np.random.default_rng(20260902)
    for _ in range(20):
        left = tuple(float(value) for value in rng.integers(0, 6, size=7))
        right = tuple(float(value) for value in rng.integers(0, 6, size=9))
        _raw_sum, _weight, expected = _independent_ecdf_oracle(
            left, right, tail_weighted=method is bounded_ad_sample
        )
        assert method(left, right).discrepancy == pytest.approx(expected)


@pytest.mark.parametrize("method", [bounded_cvm_sample, bounded_ad_sample])
@pytest.mark.parametrize("sample", [(math.inf,), (math.nan,), ()])
def test_sample_methods_reject_nonfinite_or_empty_samples(method: _SampleMethod, sample: tuple[float, ...]) -> None:
    """Accepting invalid direct samples would poison bounded fitness arithmetic."""
    with pytest.raises(TrafficlabError):
        method(sample, (1.0,))


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_trace_metrics_score_identical_tied_zero_iat_traces_as_one(similarity: _SimilarityMethod) -> None:
    """Zero IATs and ties are observed values, not missing data or an error."""
    trace = _events((0.0, 0.0, 1.0, 1.0), (100, 100, 200, 200))

    result = similarity(trace, trace, 1.0, 0.4, 0.6)

    assert result.score == 1.0
    assert result.diagnostics["discrepancy"] == 0.0


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_trace_metrics_combine_only_normalized_feature_discrepancies(similarity: _SimilarityMethod) -> None:
    """Using unnormalized component sums would ignore the declared feature weights."""
    reference = _events((0.0, 1.0, 3.0), (1, 2, 3))
    generated = _events((0.0, 1.0, 4.0), (1, 2, 4))

    result = similarity(reference, generated, 4.0, 0.25, 0.75)

    iat = cast(Mapping[str, float], result.diagnostics["iat"])
    size = cast(Mapping[str, float], result.diagnostics["size"])
    expected = 0.25 * iat["discrepancy"] + 0.75 * size["discrepancy"]
    assert result.diagnostics["discrepancy"] == pytest.approx(expected)
    assert result.score == pytest.approx(1.0 - expected)


@pytest.mark.parametrize(
    ("similarity", "tail_weighted"),
    [(cramer_von_mises_similarity, False), (anderson_darling_similarity, True)],
)
def test_trace_metrics_publish_the_complete_public_diagnostic_contract(
    similarity: _SimilarityMethod, tail_weighted: bool
) -> None:
    """Removing any public ECDF audit field or changing its arithmetic must fail."""
    reference = _events((0.0, 0.0, 1.0, 1.0), (100, 100, 200, 200))
    generated = _events((0.0, 1.0, 1.0, 2.0), (100, 200, 200, 300))

    result = similarity(reference, generated, 2.0, 0.3, 0.7)

    diagnostics = result.diagnostics
    assert diagnostics["observation_window_seconds"] == 2.0
    assert diagnostics["feature_weights"] == {"iat": 0.3, "size": 0.7}
    expected_discrepancies: dict[str, float] = {}
    for name, left, right, reference_ties, generated_ties in (
        ("iat", (0.0, 1.0, 0.0), (1.0, 0.0, 1.0), 1, 1),
        ("size", (100.0, 100.0, 200.0, 200.0), (100.0, 200.0, 200.0, 300.0), 2, 1),
    ):
        raw_sum, normalization_weight, discrepancy = _independent_ecdf_oracle(
            left, right, tail_weighted=tail_weighted
        )
        component = cast(Mapping[str, float | int], diagnostics[name])
        assert component["reference_sample_count"] == len(left)
        assert component["generated_sample_count"] == len(right)
        assert component["reference_tie_count"] == reference_ties
        assert component["generated_tie_count"] == generated_ties
        assert component["raw_sum"] == pytest.approx(raw_sum)
        assert component["normalization_weight"] == pytest.approx(normalization_weight)
        assert component["discrepancy"] == pytest.approx(discrepancy)
        expected_discrepancies[name] = discrepancy
    final_discrepancy = 0.3 * expected_discrepancies["iat"] + 0.7 * expected_discrepancies["size"]
    assert diagnostics["discrepancy"] == pytest.approx(final_discrepancy)
    assert result.score == pytest.approx(1.0 - final_discrepancy)


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_trace_metrics_report_a_missing_direction_stratum_without_inventing_samples(similarity: _SimilarityMethod) -> None:
    """A missing generated direction must remain observable to later aggregation policy."""
    reference = _events(
        (0.0, 1.0, 3.0),
        (100, 200, 300),
        (Direction.OUTBOUND, Direction.INBOUND, Direction.OUTBOUND),
    )
    generated = _events(
        (0.0, 1.0, 3.0),
        (100, 200, 300),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.OUTBOUND),
    )

    result = similarity(reference, generated, 3.0, 0.5, 0.5)

    strata = cast(Mapping[str, Mapping[str, Mapping[str, bool]]], result.diagnostics["direction_strata"])
    assert strata["size"]["inbound"] == {
        "reference_available": True,
        "generated_available": False,
        "both_available": False,
    }
    assert strata["iat"]["inbound"] == {
        "reference_available": True,
        "generated_available": False,
        "both_available": False,
    }
