"""Tests for deterministic descriptive bootstrap intervals."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import pytest

import trafficlab.common.statistics as statistics
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.statistics import _validated_sample, bootstrap_interval  # pyright: ignore[reportPrivateUsage]


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


def test_bootstrap_interval_executes_the_locked_scipy_protocol_for_a_constant_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degenerate sample must not claim 10,000 resamples unless SciPy actually executes them."""
    real_bootstrap = statistics._bootstrap  # pyright: ignore[reportPrivateUsage]
    calls: list[dict[str, object]] = []

    def observed_bootstrap(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return real_bootstrap(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(statistics, "_bootstrap", observed_bootstrap)
    interval = bootstrap_interval([0.25, 0.25, 0.25], seed=20260819)

    assert len(calls) == 1
    assert calls[0]["n_resamples"] == 10_000
    assert calls[0]["confidence_level"] == 0.95
    assert calls[0]["method"] == "percentile"
    assert isinstance(calls[0]["rng"], statistics.np.random.Generator)
    assert type(calls[0]["rng"].bit_generator) is statistics.np.random.PCG64  # type: ignore[union-attr]
    assert interval.as_dict()["n_resamples"] == 10_000
    assert (interval.lower_bound, interval.upper_bound, interval.sample_size) == (0.25, 0.25, 3)


def test_bootstrap_interval_returns_exact_bounds_after_executing_a_nonbinary_constant_sample() -> None:
    """Roundoff in SciPy's retained replicates must not invert a mathematically exact constant interval."""
    value = 0.4418506419447859

    interval = bootstrap_interval([value, value, value], seed=20260819)

    assert (interval.lower_bound, interval.upper_bound) == (value, value)
    assert interval.n_resamples == 10_000


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
