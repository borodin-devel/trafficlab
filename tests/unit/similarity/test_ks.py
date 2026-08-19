"""Unit tests for exact frame-size and inter-arrival KS similarity."""

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any, cast

import pytest
from scipy.stats import ks_2samp  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

import trafficlab.similarity.ks as ks_module
from trafficlab.errors import TrafficlabError
from trafficlab.similarity.common import JsonDiagnostics, SimilarityResult
from trafficlab.similarity.ks import frame_size_ks, iat_ks
from trafficlab.trace import Direction, TraceEvent


def _events(*timestamps: float, lengths: tuple[int, ...] | None = None) -> tuple[TraceEvent, ...]:
    """Create outbound canonical events for one compact hand-calculation trace."""
    event_lengths = lengths if lengths is not None else tuple(100 for _ in timestamps)
    return tuple(
        TraceEvent(timestamp=timestamp, direction=Direction.OUTBOUND, frame_length=length)
        for timestamp, length in zip(timestamps, event_lengths, strict=True)
    )


def _invalid_event(*, timestamp: object = 0.0, frame_length: object = 100) -> TraceEvent:
    """Construct a malformed event to prove metric boundary validation."""
    event = object.__new__(TraceEvent)
    object.__setattr__(event, "timestamp", timestamp)
    object.__setattr__(event, "direction", Direction.OUTBOUND)
    object.__setattr__(event, "frame_length", frame_length)
    return event


def _merged_ecdf_oracle(left: list[int | float], right: list[int | float]) -> float:
    """Independently scan the merged ECDF, consuming all tied observations."""
    left_values = sorted(left)
    right_values = sorted(right)
    distance = 0.0
    for value in sorted(set((*left_values, *right_values))):
        left_fraction = sum(item <= value for item in left_values) / len(left_values)
        right_fraction = sum(item <= value for item in right_values) / len(right_values)
        distance = max(distance, abs(left_fraction - right_fraction))
    return distance


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ([1, 1, 2], [1, 2, 2]),
        ([0.1, 0.1, 0.3, 0.5], [0.1, 0.2, 0.2, 0.5]),
        ([1, 2], [1, 3]),
    ],
)
def test_scipy_ks_statistic_matches_the_independent_tied_sample_oracle(
    left: list[int | float], right: list[int | float]
) -> None:
    assert cast(Any, ks_2samp(left, right)).statistic == _merged_ecdf_oracle(left, right)


def test_metric_uses_only_scipy_ks_statistic_for_tied_frame_and_iat_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], tuple[object, ...]]] = []
    scipy_ks: Any = cast(Any, ks_2samp)

    def statistic_only(left: object, right: object, **_kwargs: object) -> object:
        calls.append((tuple(cast(Iterable[object], left)), tuple(cast(Iterable[object], right))))
        return scipy_ks(left, right)

    monkeypatch.setattr(ks_module.scipy_stats, "ks_2samp", statistic_only)

    frame_result = frame_size_ks(
        _events(0.0, 1.0, 2.0, lengths=(1, 1, 2)),
        _events(0.0, 1.0, 2.0, lengths=(1, 2, 2)),
        2.0,
    )
    iat_result = iat_ks(_events(0.0, 0.0, 1.0, 2.0), _events(0.0, 1.0, 1.0, 2.0), 2.0, 0.95)

    assert frame_result.diagnostics["distance"] == pytest.approx(1.0 / 3.0)
    assert iat_result.diagnostics["distance"] == 0.0
    assert calls == [((1, 1, 2), (1, 2, 2)), ((0.0, 1.0, 1.0), (1.0, 0.0, 1.0))]


def test_frame_size_ks_returns_identical_score_and_complete_diagnostics() -> None:
    reference = _events(0.0, 1.0, lengths=(100, 200))
    generated = _events(0.0, 1.0, lengths=(200, 100))
    result = frame_size_ks(reference, generated, 3.0)

    assert result.score == 1.0
    assert result.diagnostics == {
        "observation_window_seconds": 3.0,
        "distance": 0.0,
        "reference_count": 2,
        "generated_count": 2,
        "reference_minimum_length": 100,
        "reference_maximum_length": 200,
        "generated_minimum_length": 100,
        "generated_maximum_length": 200,
    }


def test_frame_size_ks_matches_documented_half_score() -> None:
    result = frame_size_ks(_events(0.0, 1.0, lengths=(1, 2)), _events(0.0, 1.0, lengths=(1, 3)), 1.0)

    assert result.diagnostics["distance"] == 0.5
    assert result.score == 0.5


def test_frame_size_ks_reordering_does_not_change_score() -> None:
    result = frame_size_ks(
        _events(0.0, 1.0, 2.0, lengths=(3, 1, 2)),
        _events(0.0, 1.0, 2.0, lengths=(2, 3, 1)),
        2.0,
    )

    assert result.score == 1.0


@pytest.mark.parametrize(
    ("reference", "generated"),
    [
        ([], _events(0.0)),
        (_events(0.0), []),
        ([_invalid_event(frame_length=0)], _events(0.0)),
        ([_invalid_event(frame_length="100")], _events(0.0)),
    ],
)
def test_frame_size_ks_rejects_empty_nonpositive_or_noninteger_lengths(
    reference: list[TraceEvent] | tuple[TraceEvent, ...], generated: list[TraceEvent] | tuple[TraceEvent, ...]
) -> None:
    with pytest.raises(TrafficlabError):
        frame_size_ks(reference, generated, 1.0)


def test_frame_size_ks_rejects_a_noniterable_trace() -> None:
    with pytest.raises(TrafficlabError):
        frame_size_ks(cast(Iterable[TraceEvent], 1), _events(0.0), 1.0)


def test_frame_size_ks_rejects_a_noncanonical_trace_member() -> None:
    with pytest.raises(TrafficlabError):
        frame_size_ks(cast(Iterable[TraceEvent], [object()]), _events(0.0), 1.0)


@pytest.mark.parametrize("window", [0.0, -1.0, math.inf, 1])
def test_frame_size_ks_rejects_an_invalid_observation_window(window: object) -> None:
    with pytest.raises(TrafficlabError):
        frame_size_ks(_events(0.0), _events(0.0), window)


def test_iat_ks_derives_documented_iats_and_uses_same_window() -> None:
    result = iat_ks(_events(0.0, 1.0, 3.0), _events(5.0, 6.0, 8.0), 7.0, 0.95)

    assert result.score == 1.0
    assert result.diagnostics["observation_window_seconds"] == 7.0
    assert result.diagnostics["reference_iat_count"] == 2
    assert result.diagnostics["generated_iat_count"] == 2
    assert result.diagnostics["reference_median_iat_seconds"] == 1.5
    assert result.diagnostics["generated_median_iat_seconds"] == 1.5


def test_iat_ks_retains_zero_iats() -> None:
    result = iat_ks(_events(0.0, 0.0, 1.0), _events(0.0, 1.0, 2.0), 2.0, 0.5)

    assert result.diagnostics["reference_zero_iat_count"] == 1
    assert result.diagnostics["generated_zero_iat_count"] == 0
    assert result.diagnostics["distance"] == 0.5
    assert result.score == 0.5


def test_iat_ks_uses_nearest_rank_for_exact_quantile_rank() -> None:
    result = iat_ks(_events(0.0, 1.0, 3.0, 6.0, 10.0), _events(0.0, 1.0, 3.0, 6.0, 10.0), 3.0, 0.5)

    assert result.diagnostics["reference_quantile_iat_seconds"] == 2.0
    assert result.diagnostics["generated_quantile_iat_seconds"] == 2.0


def test_iat_ks_uses_nearest_rank_for_nonexact_quantile_rank() -> None:
    result = iat_ks(_events(0.0, 1.0, 3.0, 6.0), _events(0.0, 1.0, 3.0, 6.0), 3.0, 0.7)

    assert result.diagnostics["reference_quantile_iat_seconds"] == 3.0


def test_iat_ks_uses_conventional_even_sample_median() -> None:
    result = iat_ks(_events(0.0, 1.0, 3.0, 6.0, 10.0), _events(0.0, 1.0, 3.0, 6.0, 10.0), 4.0, 0.5)

    assert result.diagnostics["reference_median_iat_seconds"] == 2.5


def test_iat_ks_ignores_iat_order() -> None:
    result = iat_ks(_events(0.0, 1.0, 3.0), _events(0.0, 2.0, 3.0), 3.0, 0.5)

    assert result.score == 1.0


@pytest.mark.parametrize(
    "trace",
    [
        _events(0.0),
        [_invalid_event(timestamp=1.0), _invalid_event(timestamp=0.0)],
        [_invalid_event(timestamp=0.0), _invalid_event(timestamp=math.nan)],
    ],
)
def test_iat_ks_rejects_one_packet_decreasing_or_nonfinite_timestamps(
    trace: list[TraceEvent] | tuple[TraceEvent, ...],
) -> None:
    with pytest.raises(TrafficlabError):
        iat_ks(trace, _events(0.0, 1.0), 1.0, 0.5)


def test_frame_size_ks_translates_incomplete_trace_events_to_trafficlab_error() -> None:
    event = object.__new__(TraceEvent)
    object.__setattr__(event, "timestamp", 0.0)

    with pytest.raises(TrafficlabError):
        frame_size_ks([event], _events(0.0), 1.0)


@pytest.mark.parametrize("quantile", [0.0, 1.0, -0.1, math.inf, 1])
def test_iat_ks_rejects_invalid_diagnostic_quantiles(quantile: object) -> None:
    with pytest.raises(TrafficlabError):
        iat_ks(_events(0.0, 1.0), _events(0.0, 1.0), 1.0, quantile)


def test_similarity_results_are_immutable_and_ranges_are_bounded() -> None:
    result = frame_size_ks(_events(0.0, lengths=(1,)), _events(0.0, lengths=(2,)), 1.0)

    assert 0.0 <= result.score <= 1.0
    distance = result.diagnostics["distance"]
    assert isinstance(distance, float)
    assert 0.0 <= distance <= 1.0
    with pytest.raises(AttributeError):
        result.score = 0.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.diagnostics["distance"] = 0.0  # type: ignore[index]
    assert json.dumps(result.as_dict())


def test_similarity_result_freezes_nested_diagnostics_without_source_aliasing() -> None:
    source: dict[str, object] = {"vector": [1, {"state": "original"}]}
    result = SimilarityResult(score=0.5, diagnostics=cast(JsonDiagnostics, source))
    source_vector = cast(list[object], source["vector"])
    source_vector[1] = {"state": "changed"}

    assert result.diagnostics["vector"] == (1, {"state": "original"})


def test_similarity_result_as_dict_returns_detached_nested_json_data() -> None:
    result = SimilarityResult(score=0.5, diagnostics={"vector": [1, {"state": "original"}]})
    rendered = result.as_dict()
    diagnostics = cast(dict[str, object], rendered["diagnostics"])
    vector = cast(list[object], diagnostics["vector"])
    nested = cast(dict[str, str], vector[1])
    nested["state"] = "changed"

    assert result.as_dict()["diagnostics"] == {"vector": [1, {"state": "original"}]}
    assert json.dumps(rendered, allow_nan=False)


def test_similarity_result_preserves_all_json_scalar_types_and_freezes_tuples() -> None:
    result = SimilarityResult(
        score=0.5,
        diagnostics={"none": None, "boolean": True, "string": "text", "integer": 2, "tuple": (1, 2)},
    )

    assert result.diagnostics["tuple"] == (1, 2)
    assert result.as_dict()["diagnostics"] == {
        "none": None,
        "boolean": True,
        "string": "text",
        "integer": 2,
        "tuple": [1, 2],
    }


def test_similarity_result_requires_a_diagnostic_mapping() -> None:
    with pytest.raises(ValueError):
        SimilarityResult(score=0.5, diagnostics=cast(Mapping[str, object], ["not a mapping"]))


@pytest.mark.parametrize(
    "diagnostics",
    [
        cast(JsonDiagnostics, {1: "not a string key"}),
        cast(JsonDiagnostics, {"unsupported": object()}),
        cast(JsonDiagnostics, {"nonfinite": math.nan}),
        cast(JsonDiagnostics, {"nested": [math.inf]}),
    ],
)
def test_similarity_result_rejects_invalid_diagnostic_json_values(diagnostics: JsonDiagnostics) -> None:
    with pytest.raises(ValueError):
        SimilarityResult(score=0.5, diagnostics=diagnostics)


@pytest.mark.parametrize("score", [-0.1, 1.1, math.inf, 1])
def test_similarity_result_rejects_out_of_range_or_nonfloat_scores(score: object) -> None:
    with pytest.raises(ValueError):
        SimilarityResult(score=cast(float, score), diagnostics={})
