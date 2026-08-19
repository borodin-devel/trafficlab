"""Tests for deterministic descriptive bootstrap intervals."""

import json

import pytest

from trafficlab.errors import TrafficlabError
from trafficlab.statistics import bootstrap_interval


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


@pytest.mark.parametrize("values", [[], [1.0, float("nan")], [1.0, float("inf")]])
def test_bootstrap_interval_rejects_empty_or_nonfinite_samples(values: list[float]) -> None:
    with pytest.raises(TrafficlabError):
        bootstrap_interval(values, seed=1)
