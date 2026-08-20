"""Columnar production paths used by the scientific-stack performance gate."""

from __future__ import annotations

import numpy as np
import pytest

import trafficlab.similarity.multiscale as multiscale_module
from trafficlab.errors import TrafficlabError
from trafficlab.similarity.autocorrelation import autocorrelation_similarity, sample_autocorrelation
from trafficlab.similarity.multiscale import multiscale_rate_similarity
from trafficlab.trace import TrafficTrace, align_generated, normalize_reference


def _trace() -> TrafficTrace:
    return TrafficTrace(
        np.array([10.0, 11.0, 12.5, 14.0], dtype=np.float64),
        np.array([0, 1, 0, 1], dtype=np.uint8),
        np.array([64, 128, 64, 256], dtype=np.uint32),
    )


def _forbid_event_materialization(_trace: TrafficTrace) -> tuple[object, ...]:
    raise AssertionError("columnar production path materialized TraceEvent objects")


def _empty_trace() -> TrafficTrace:
    return TrafficTrace(
        np.array([], dtype=np.float64),
        np.array([], dtype=np.uint8),
        np.array([], dtype=np.uint32),
    )


def test_normalization_and_alignment_keep_an_existing_traffic_trace_columnar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Converting an owned trace back to events would erase the NumPy normalization benefit."""
    trace = _trace()
    monkeypatch.setattr(TrafficTrace, "to_events", _forbid_event_materialization)

    normalized, window = normalize_reference(trace)
    aligned = align_generated(trace, 2.0)

    assert window == 4.0
    assert np.array_equal(normalized.timestamps, np.array([0.0, 1.0, 2.5, 4.0]))
    assert np.array_equal(aligned.timestamps, np.array([0.0, 1.0]))


def test_columnar_normalization_and_alignment_retain_minimum_length_errors() -> None:
    """The fast path must not bypass the established nonempty/two-event preconditions."""
    empty = _empty_trace()
    one = TrafficTrace(
        np.array([0.0], dtype=np.float64),
        np.array([0], dtype=np.uint8),
        np.array([64], dtype=np.uint32),
    )

    with pytest.raises(TrafficlabError, match="at least two"):
        normalize_reference(empty)
    with pytest.raises(TrafficlabError, match="at least two"):
        normalize_reference(one)
    with pytest.raises(TrafficlabError, match="at least one"):
        align_generated(empty, 1.0)
    with pytest.raises(TrafficlabError, match="observation window"):
        align_generated(one, 0.0)


def test_selected_lag_acf_consumes_numeric_trace_columns_directly() -> None:
    """Rejecting a uint32 trace column would force one Python tuple reconstruction per lag."""
    assert sample_autocorrelation(_trace().frame_lengths, 1) == pytest.approx(-1.0 / 3.0)


def test_autocorrelation_similarity_keeps_existing_traces_columnar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The complete metric must not rebuild event tuples after compare_traces canonicalizes inputs."""
    trace = _trace()
    monkeypatch.setattr(TrafficTrace, "to_events", _forbid_event_materialization)

    result = autocorrelation_similarity(trace, trace, 4.0, (1,), (1.0,), 0.5, 0.5)

    assert result.score == 1.0


def test_columnar_autocorrelation_retains_the_two_event_precondition() -> None:
    """Shared array validation must still reject an insufficient direct trace."""
    one = TrafficTrace(
        np.array([0.0], dtype=np.float64),
        np.array([0], dtype=np.uint8),
        np.array([64], dtype=np.uint32),
    )
    with pytest.raises(TrafficlabError, match="at least two"):
        autocorrelation_similarity(one, one, 1.0, (1,), (1.0,), 0.5, 0.5)


def test_autocorrelation_legacy_boundary_rejects_a_non_event_after_length_validation() -> None:
    """The columnar fast path must not weaken generic iterable validation."""
    trace = _trace().to_events()
    invalid = (object(), trace[1])
    with pytest.raises(TrafficlabError, match="TraceEvent"):
        autocorrelation_similarity(invalid, trace, 4.0, (1,), (1.0,), 0.5, 0.5)  # type: ignore[arg-type]


def test_multiscale_similarity_keeps_existing_traces_columnar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-scale event reconstruction would dominate the vectorized bincount kernel."""
    trace, _window = normalize_reference(_trace())
    monkeypatch.setattr(TrafficTrace, "to_events", _forbid_event_materialization)

    result = multiscale_rate_similarity(trace, trace, 4.0, (0.5, 2.0), (0.5, 0.5), 0.5, 0.5, 20)

    assert result.score == 1.0


def test_columnar_multiscale_retains_nonempty_and_window_preconditions() -> None:
    """The fast path must reject empty and out-of-window traces before bin allocation."""
    valid = TrafficTrace(
        np.array([0.0], dtype=np.float64),
        np.array([0], dtype=np.uint8),
        np.array([64], dtype=np.uint32),
    )
    outside = TrafficTrace(
        np.array([2.0], dtype=np.float64),
        np.array([0], dtype=np.uint8),
        np.array([64], dtype=np.uint32),
    )
    arguments = (1.0, (1.0,), (1.0,), 0.5, 0.5, 2)

    with pytest.raises(TrafficlabError, match="at least one"):
        multiscale_rate_similarity(_empty_trace(), valid, *arguments)
    with pytest.raises(TrafficlabError, match="inside"):
        multiscale_rate_similarity(outside, valid, *arguments)


def test_columnar_multiscale_exact_byte_fallback_preserves_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overflow-safe path must keep exact Python integer byte accumulation."""
    trace = TrafficTrace(
        np.array([0.0, 1.0], dtype=np.float64),
        np.array([0, 1], dtype=np.uint8),
        np.array([2, 3], dtype=np.uint32),
    )

    class _TinyIntegerInfo:
        max = 1

    def tiny_iinfo(_dtype: object) -> _TinyIntegerInfo:
        return _TinyIntegerInfo()

    monkeypatch.setattr(multiscale_module.np, "iinfo", tiny_iinfo)
    assert multiscale_module._binned_trace_features(  # pyright: ignore[reportPrivateUsage]
        trace, width=1.0, bins_per_direction=2
    ) == ((1, 0, 0, 1), (2, 0, 0, 3))
