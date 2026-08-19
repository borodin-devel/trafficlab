"""Tests for deterministic descriptive bootstrap intervals."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import pytest

import trafficlab.statistics as statistics
from trafficlab.errors import TrafficlabError
from trafficlab.statistics import _validated_sample, bootstrap_interval  # pyright: ignore[reportPrivateUsage]


def test_bootstrap_interval_records_literal_pcg64_percentile_metadata_and_bytes() -> None:
    interval = bootstrap_interval([1.0, 2.0, 4.0, 8.0], seed=1729)

    assert interval.as_dict() == {
        "confidence_level": 0.95,
        "generator": "PCG64",
        "generator_state": {
            "bit_generator": "PCG64",
            "has_uint32": 0,
            "state": {"inc": 79125515514428683154904239957670779583, "state": 260275080868705179941444768562716442507},
            "uinteger": 0,
        },
        "lower_bound": 1.5,
        "method": "percentile",
        "n_resamples": 10_000,
        "sample_size": 4,
        "seed": 1729,
        "statistic": "mean",
        "upper_bound": 6.5,
    }
    assert json.dumps(interval.as_dict(), sort_keys=True, separators=(",", ":")).encode() == (
        b'{"confidence_level":0.95,"generator":"PCG64","generator_state":{"bit_generator":"PCG64","has_uint32":0,"state":{"inc":79125515514428683154904239957670779583,"state":260275080868705179941444768562716442507},"uinteger":0},"lower_bound":1.5,"method":"percentile","n_resamples":10000,"sample_size":4,"seed":1729,"statistic":"mean","upper_bound":6.5}'
    )


def test_bootstrap_interval_returns_the_exact_constant_sample_interval_without_scipy_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degenerate empirical distribution has a finite exact percentile interval."""

    def forbidden_bootstrap(*_args: object, **_kwargs: object) -> _FakeBootstrapResult:
        pytest.fail("constant sample reached SciPy's undefined standard-error path")

    monkeypatch.setattr(statistics, "_bootstrap", forbidden_bootstrap)
    interval = bootstrap_interval([0.25, 0.25, 0.25], seed=20260819)

    assert (interval.lower_bound, interval.upper_bound, interval.sample_size) == (0.25, 0.25, 3)


@pytest.mark.parametrize("values", [[], [1.0, float("nan")], [1.0, float("inf")]])
def test_bootstrap_interval_rejects_empty_or_nonfinite_samples(values: list[float]) -> None:
    with pytest.raises(TrafficlabError):
        bootstrap_interval(values, seed=1)


@pytest.mark.parametrize(
    "values", [cast(Iterable[object], 1), ["not-a-number"], [10**1000], [float("nan")], [float("inf")]]
)
def test_validated_bootstrap_sample_translates_unrepresentable_or_nonfinite_values(values: Iterable[object]) -> None:
    with pytest.raises(TrafficlabError):
        _validated_sample(values)


@pytest.mark.parametrize(
    ("seed", "n_resamples", "confidence_level"),
    [(True, 10_000, 0.95), (-1, 10_000, 0.95), (1, 0, 0.95), (1, 1.0, 0.95), (1, 10_000, 0.0), (1, 10_000, 1.0)],
)
def test_bootstrap_interval_rejects_invalid_settings(
    seed: object, n_resamples: object, confidence_level: object
) -> None:
    with pytest.raises(TrafficlabError):
        bootstrap_interval([1.0], seed=seed, n_resamples=n_resamples, confidence_level=confidence_level)


@dataclass(frozen=True)
class _FakeInterval:
    low: float
    high: float


@dataclass(frozen=True)
class _FakeBootstrapResult:
    confidence_interval: _FakeInterval


@pytest.mark.parametrize(("low", "high"), [(float("nan"), 1.0), (2.0, 1.0)])
def test_bootstrap_interval_rejects_nonfinite_or_inverted_scipy_bounds(
    low: float, high: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_bootstrap(*_args: object, **_kwargs: object) -> _FakeBootstrapResult:
        return _FakeBootstrapResult(_FakeInterval(low, high))

    monkeypatch.setattr(statistics, "_bootstrap", fake_bootstrap)

    with pytest.raises(TrafficlabError, match="nonfinite or inverted"):
        bootstrap_interval([1.0, 2.0], seed=1)


def test_bootstrap_interval_translates_scipy_evaluation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_bootstrap(*_args: object, **_kwargs: object) -> _FakeBootstrapResult:
        raise ValueError("controlled SciPy failure")

    monkeypatch.setattr(statistics, "_bootstrap", raising_bootstrap)

    with pytest.raises(TrafficlabError, match="could not be evaluated"):
        bootstrap_interval([1.0, 2.0], seed=1)
