"""Independent unit tests for bounded pooled-ECDF similarity methods."""

import math
from collections import Counter
from collections.abc import Callable, Mapping
from typing import cast

import numpy as np
import pytest

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.diagnostics import CramerVonMisesDiagnostic
from trafficlab.comparison.schema import MethodComparison
from trafficlab.comparison.similarity.common import SimilarityResult
from trafficlab.comparison.similarity.ecdf import (
    EcdfSampleResult,
    anderson_darling_similarity,
    bounded_ad_sample,
    bounded_cvm_sample,
    cramer_von_mises_similarity,
)

type _SampleMethod = Callable[[tuple[float, ...], tuple[float, ...]], EcdfSampleResult]
type _SimilarityMethod = Callable[
    [TrafficTrace, TrafficTrace, float, float, float, float, float, float], SimilarityResult
]


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
        _raw_sum, _weight, expected = _independent_ecdf_oracle(left, right, tail_weighted=method is bounded_ad_sample)
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

    result = similarity(trace, trace, 1.0, 0.4, 0.6, 0.5, 0.25, 0.25)

    assert result.score == 1.0
    assert result.diagnostics["discrepancy"] == 0.0


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_trace_metrics_combine_only_normalized_feature_discrepancies(similarity: _SimilarityMethod) -> None:
    """Using unnormalized component sums would ignore the declared feature weights."""
    reference = _events((0.0, 1.0, 3.0), (1, 2, 3))
    generated = _events((0.0, 1.0, 4.0), (1, 2, 4))

    result = similarity(reference, generated, 4.0, 0.25, 0.75, 1.0, 0.0, 0.0)

    strata = cast(Mapping[str, Mapping[str, object]], result.diagnostics["strata"])
    overall = strata["global"]
    iat = cast(Mapping[str, float], overall["iat"])
    size = cast(Mapping[str, float], overall["size"])
    expected = 0.25 * iat["discrepancy"] + 0.75 * size["discrepancy"]
    assert overall["discrepancy"] == pytest.approx(expected)
    assert result.diagnostics["discrepancy"] == pytest.approx(expected)
    assert result.score == pytest.approx(1.0 - expected)


def test_large_cvm_diagnostics_survive_pooled_mass_roundoff() -> None:
    """A normalization within schema tolerance of one remains a CvM diagnostic."""
    trace = _events(tuple(float(index) for index in range(491)), tuple(range(60, 551)))
    result = cramer_von_mises_similarity(trace, trace, 490.0, 0.5, 0.5, 1.0, 0.0, 0.0)

    method = MethodComparison.model_validate(
        {"score": result.score, "weight": 0.125, "diagnostics": result.diagnostics}
    )

    assert isinstance(method.diagnostics, CramerVonMisesDiagnostic)


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

    result = similarity(reference, generated, 2.0, 0.3, 0.7, 1.0, 0.0, 0.0)

    diagnostics = result.diagnostics
    assert diagnostics["observation_window_seconds"] == 2.0
    assert diagnostics["feature_weights"] == {"iat": 0.3, "size": 0.7}
    assert diagnostics["stratum_weights"] == {"global": 1.0, "outbound": 0.0, "inbound": 0.0}
    strata = cast(Mapping[str, Mapping[str, object]], diagnostics["strata"])
    overall = strata["global"]
    expected_discrepancies: dict[str, float] = {}
    for name, left, right, reference_ties, generated_ties in (
        ("iat", (0.0, 1.0, 0.0), (1.0, 0.0, 1.0), 1, 1),
        ("size", (100.0, 100.0, 200.0, 200.0), (100.0, 200.0, 200.0, 300.0), 2, 1),
    ):
        raw_sum, normalization_weight, discrepancy = _independent_ecdf_oracle(left, right, tail_weighted=tail_weighted)
        component = cast(Mapping[str, float | int | str], overall[name])
        assert component["status"] == "compared"
        assert component["reference_sample_count"] == len(left)
        assert component["generated_sample_count"] == len(right)
        assert component["reference_tie_count"] == reference_ties
        assert component["generated_tie_count"] == generated_ties
        assert component["raw_sum"] == pytest.approx(raw_sum)
        assert component["normalization_weight"] == pytest.approx(normalization_weight)
        assert component["discrepancy"] == pytest.approx(discrepancy)
        expected_discrepancies[name] = discrepancy
    final_discrepancy = 0.3 * expected_discrepancies["iat"] + 0.7 * expected_discrepancies["size"]
    assert overall["discrepancy"] == pytest.approx(final_discrepancy)
    assert diagnostics["discrepancy"] == pytest.approx(final_discrepancy)
    assert result.score == pytest.approx(1.0 - final_discrepancy)


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_trace_metrics_apply_global_uplink_and_downlink_weights_to_both_features(
    similarity: _SimilarityMethod,
) -> None:
    """Ignoring directional samples or either weight layer changes this hand-derived aggregate."""
    reference = _events(
        (0.0, 1.0, 2.0),
        (10, 20, 30),
        (Direction.OUTBOUND, Direction.INBOUND, Direction.OUTBOUND),
    )
    generated = _events(
        (0.0, 1.0, 2.0),
        (10, 20, 30),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.INBOUND),
    )

    result = similarity(reference, generated, 2.0, 0.25, 0.75, 0.5, 0.25, 0.25)

    strata = cast(Mapping[str, Mapping[str, object]], result.diagnostics["strata"])
    assert strata["global"]["discrepancy"] == 0.0
    if similarity is cramer_von_mises_similarity:
        assert strata["outbound"]["discrepancy"] == pytest.approx(0.046875)
        assert strata["inbound"]["discrepancy"] == pytest.approx(0.375)
        expected = 0.10546875
    else:
        assert strata["outbound"]["discrepancy"] == pytest.approx(3.0 / 28.0)
        assert strata["inbound"]["discrepancy"] == pytest.approx(0.75)
        expected = 3.0 / 14.0
    assert result.diagnostics["discrepancy"] == pytest.approx(expected)
    assert result.score == pytest.approx(1.0 - expected)


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_trace_metrics_assign_one_to_one_sided_empty_strata_and_zero_to_shared_empty_strata(
    similarity: _SimilarityMethod,
) -> None:
    """Dropping an absent generated downlink must be maximally different, never neutral or fabricated."""
    reference = _events(
        (0.0, 1.0, 2.0),
        (10, 20, 30),
        (Direction.OUTBOUND, Direction.INBOUND, Direction.OUTBOUND),
    )
    generated = _events(
        (0.0, 1.0, 2.0),
        (10, 20, 30),
        (Direction.OUTBOUND, Direction.OUTBOUND, Direction.OUTBOUND),
    )

    result = similarity(reference, generated, 2.0, 0.25, 0.75, 0.0, 0.0, 1.0)

    strata = cast(Mapping[str, Mapping[str, object]], result.diagnostics["strata"])
    downlink = strata["inbound"]
    assert cast(Mapping[str, object], downlink["iat"])["status"] == "one_sided_empty"
    assert cast(Mapping[str, object], downlink["size"])["status"] == "one_sided_empty"
    assert downlink["discrepancy"] == 1.0
    assert result.diagnostics["discrepancy"] == 1.0
    assert result.score == 0.0
    assert MethodComparison.model_validate({"score": result.score, "weight": 0.125, "diagnostics": result.diagnostics})

    outbound_only = _events((0.0, 1.0), (10, 20))
    shared_empty = similarity(outbound_only, outbound_only, 1.0, 0.5, 0.5, 0.0, 0.0, 1.0)
    empty_stratum = cast(Mapping[str, Mapping[str, object]], shared_empty.diagnostics["strata"])["inbound"]
    assert cast(Mapping[str, object], empty_stratum["iat"])["status"] == "both_empty"
    assert cast(Mapping[str, object], empty_stratum["size"])["status"] == "both_empty"
    assert empty_stratum["discrepancy"] == 0.0
    assert shared_empty.score == 1.0


@pytest.mark.parametrize("similarity", [cramer_von_mises_similarity, anderson_darling_similarity])
def test_typed_diagnostics_reject_inconsistent_empty_status_and_stratum_arithmetic(
    similarity: _SimilarityMethod,
) -> None:
    """A strict artifact cannot relabel observed samples as empty or detach a stratum from its features."""
    reference = _events(
        (0.0, 1.0, 2.0),
        (10, 20, 30),
        (Direction.OUTBOUND, Direction.INBOUND, Direction.OUTBOUND),
    )
    generated = _events((0.0, 1.0, 2.0), (10, 20, 30))
    result = similarity(reference, generated, 2.0, 0.5, 0.5, 0.0, 0.0, 1.0)
    diagnostics = cast(dict[str, object], result.as_dict()["diagnostics"])
    strata = cast(dict[str, object], diagnostics["strata"])
    inbound = cast(dict[str, object], strata["inbound"])
    cast(dict[str, object], inbound["iat"])["status"] = "both_empty"

    with pytest.raises(ValueError, match="both_empty"):
        MethodComparison.model_validate({"score": result.score, "weight": 0.125, "diagnostics": diagnostics})

    diagnostics = cast(dict[str, object], result.as_dict()["diagnostics"])
    strata = cast(dict[str, object], diagnostics["strata"])
    cast(dict[str, object], strata["inbound"])["discrepancy"] = 0.5
    with pytest.raises(ValueError, match="strata.inbound.discrepancy"):
        MethodComparison.model_validate({"score": result.score, "weight": 0.125, "diagnostics": diagnostics})
