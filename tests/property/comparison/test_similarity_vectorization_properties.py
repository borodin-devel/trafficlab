"""Property tests comparing vector implementations with scalar definitions."""

import math
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy.stats import ks_2samp  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

from trafficlab.comparison.similarity.autocorrelation import sample_autocorrelation
from trafficlab.comparison.similarity.multiscale import normalized_l1


def _merged_ecdf_distance(left: list[int], right: list[int]) -> float:
    """Independently derive the tied-sample KS distance by ECDF definition."""
    return max(
        abs(sum(item <= value for item in left) / len(left) - sum(item <= value for item in right) / len(right))
        for value in set((*left, *right))
    )


@given(
    st.lists(st.integers(-20, 20), min_size=1, max_size=30),
    st.lists(st.integers(-20, 20), min_size=1, max_size=30),
)
@pytest.mark.filterwarnings("ignore:ks_2samp:RuntimeWarning")
def test_scipy_ks_statistic_matches_independent_merged_ecdf_for_discrete_samples(
    left: list[int], right: list[int]
) -> None:
    assert cast(Any, ks_2samp(left, right)).statistic == pytest.approx(_merged_ecdf_distance(left, right), abs=1e-15)


def _scalar_acf(values: list[float], lag: int) -> float:
    if all(value == values[0] for value in values):
        return 0.0
    mean = math.fsum(values) / len(values)
    denominator = math.fsum((value - mean) ** 2 for value in values)
    if denominator == 0.0:
        return 0.0
    numerator = math.fsum((values[index] - mean) * (values[index + lag] - mean) for index in range(len(values) - lag))
    return numerator / denominator


@given(st.lists(st.floats(-1_000.0, 1_000.0, allow_nan=False, allow_infinity=False), min_size=2, max_size=20))
def test_vectorized_acf_matches_scalar_whole_series_mean_oracle(values: list[float]) -> None:
    for lag in range(1, len(values)):
        assert sample_autocorrelation(values, lag) == pytest.approx(_scalar_acf(values, lag), abs=1e-12)


@given(
    st.lists(
        st.tuples(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000)),
        min_size=1,
        max_size=20,
    )
)
def test_exact_normalized_l1_matches_scalar_integer_accumulation(cells: list[tuple[int, int]]) -> None:
    left = [left for left, _ in cells]
    right = [right for _, right in cells]
    denominator = sum(left) + sum(right)
    expected = 0.0 if denominator == 0 else sum(abs(a - b) for a, b in zip(left, right, strict=True)) / denominator
    assert normalized_l1(left, right) == expected
