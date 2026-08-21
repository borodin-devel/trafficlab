import copy
import math
from collections.abc import Callable, Iterable
from typing import cast

import pytest

import trafficlab.comparison.metrics as comparison
from tests.support.comparison import settings as _settings
from tests.support.comparison import trace as _trace
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import TraceEvent, TrafficTrace
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.comparison.similarity.common import SimilarityResult


def test_compare_traces_accepts_equivalent_event_and_traffic_trace_inputs(valid_config_data: dict[str, object]) -> None:
    events = _trace()
    settings = _settings(valid_config_data)

    event_result = compare_traces(events, events, 3.0, settings)
    trace = TrafficTrace.from_events(events)
    trace_result = compare_traces(trace, trace, 3.0, settings)

    assert trace_result == event_result


def test_compare_traces_reuses_exact_traffic_trace_inputs(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = TrafficTrace.from_events(_trace())

    def reject_reconversion(_cls: type[TrafficTrace], _events: Iterable[TraceEvent]) -> TrafficTrace:
        raise AssertionError("already canonical TrafficTrace inputs must not be reconverted")

    monkeypatch.setattr(TrafficTrace, "from_events", classmethod(reject_reconversion))

    result = compare_traces(trace, trace, 3.0, _settings(valid_config_data))

    assert result.aggregate_score == 1.0


def test_compare_traces_runs_all_four_metrics_without_event_materialization(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One columnar comparison must never rebuild event tuples for any mandatory metric."""
    trace = TrafficTrace.from_events(_trace())

    def reject_event_materialization(_trace: TrafficTrace) -> tuple[TraceEvent, ...]:
        raise AssertionError("comparison materialized TraceEvent objects")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)

    result = compare_traces(trace, trace, 3.0, _settings(valid_config_data))

    assert result.aggregate_score == 1.0
    assert result.methods.keys() == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")


def test_compare_traces_uses_every_setting_and_retains_exact_component_results(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping a method, its weight, its settings, or diagnostics would change the configured comparison."""
    data = copy.deepcopy(valid_config_data)
    similarity_data = cast(dict[str, object], data["similarity"])
    similarity_data["method_weights"] = {
        "frame_size_ks": 0.1,
        "iat_ks": 0.2,
        "autocorrelation": 0.3,
        "multiscale_rate": 0.4,
    }
    settings = _settings(data)
    reference = _trace()
    generated = _trace()
    window = 3.0
    calls: list[tuple[str, tuple[object, ...]]] = []
    baseline = compare_traces(reference, generated, window, settings)
    components = {
        name: SimilarityResult(method.score, cast(dict[str, object], method.as_dict()["diagnostics"]))
        for name, method in baseline.methods.items()
    }

    def frame_size(reference_arg: object, generated_arg: object, window_arg: object) -> SimilarityResult:
        calls.append(("frame_size_ks", (reference_arg, generated_arg, window_arg)))
        return components["frame_size_ks"]

    def iat(reference_arg: object, generated_arg: object, window_arg: object, quantile: object) -> SimilarityResult:
        calls.append(("iat_ks", (reference_arg, generated_arg, window_arg, quantile)))
        return components["iat_ks"]

    def acf(*args: object) -> SimilarityResult:
        calls.append(("autocorrelation", args))
        return components["autocorrelation"]

    def multiscale(*args: object) -> SimilarityResult:
        calls.append(("multiscale_rate", args))
        return components["multiscale_rate"]

    monkeypatch.setattr(comparison, "frame_size_ks", frame_size)
    monkeypatch.setattr(comparison, "iat_ks", iat)
    monkeypatch.setattr(comparison, "autocorrelation_similarity", acf)
    monkeypatch.setattr(comparison, "multiscale_rate_similarity", multiscale)

    result = compare_traces(reference, generated, window, settings)

    assert isinstance(result, ComparisonResult)
    assert result.aggregate_score == pytest.approx(
        math.fsum(settings.method_weights.model_dump()[name] * components[name].score for name in components)
    )
    assert result.observation_window_seconds == 3.0
    assert result.input_sha256 is None
    assert result.methods.keys() == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert {name: method.score for name, method in result.methods.items()} == {
        name: component.score for name, component in components.items()
    }
    assert {name: method.weight for name, method in result.methods.items()} == {
        "autocorrelation": 0.3,
        "frame_size_ks": 0.1,
        "iat_ks": 0.2,
        "multiscale_rate": 0.4,
    }
    assert result.methods["autocorrelation"].diagnostics == baseline.methods["autocorrelation"].diagnostics
    assert all(type(arguments[0]) is TrafficTrace and type(arguments[1]) is TrafficTrace for _, arguments in calls)
    assert calls == [
        ("frame_size_ks", (reference, generated, 3.0)),
        ("iat_ks", (reference, generated, 3.0, 0.95)),
        ("autocorrelation", (reference, generated, 3.0, (1,), (1.0,), 0.5, 0.5)),
        (
            "multiscale_rate",
            (reference, generated, 3.0, (0.1, 1.0), (0.5, 0.5), 0.5, 0.5, 100_000),
        ),
    ]


@pytest.mark.parametrize(
    "method_weights",
    [
        {"frame_size_ks": 1.0, "iat_ks": 0.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 1.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 0.0, "autocorrelation": 1.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 0.0, "autocorrelation": 0.0, "multiscale_rate": 1.0},
        {"frame_size_ks": 0.1, "iat_ks": 0.2, "autocorrelation": 0.3, "multiscale_rate": 0.4},
        {"frame_size_ks": 0.0, "iat_ks": 0.5, "autocorrelation": 0.5, "multiscale_rate": 0.0},
    ],
)
def test_compare_traces_eagerly_retains_all_four_methods_for_every_weight_case(
    valid_config_data: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    method_weights: dict[str, float],
) -> None:
    """Aggregation weights must never choose which mandatory comparisons execute or appear in the artifact."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["similarity"])["method_weights"] = method_weights
    baseline = compare_traces(_trace(), _trace(), 3.0, _settings(data))
    scores = {name: method.score for name, method in baseline.methods.items()}
    calls: list[str] = []

    def component(name: str) -> Callable[..., SimilarityResult]:
        def evaluate(*_args: object) -> SimilarityResult:
            calls.append(name)
            method = baseline.methods[name]
            return SimilarityResult(method.score, cast(dict[str, object], method.as_dict()["diagnostics"]))

        return evaluate

    monkeypatch.setattr(comparison, "frame_size_ks", component("frame_size_ks"))
    monkeypatch.setattr(comparison, "iat_ks", component("iat_ks"))
    monkeypatch.setattr(comparison, "autocorrelation_similarity", component("autocorrelation"))
    monkeypatch.setattr(comparison, "multiscale_rate_similarity", component("multiscale_rate"))

    result = compare_traces(_trace(), _trace(), 3.0, _settings(data))

    assert calls == ["frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"]
    assert result.methods.keys() == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert {name: method.diagnostics for name, method in result.methods.items()} == {
        name: method.diagnostics for name, method in baseline.methods.items()
    }
    expected_aggregate = math.fsum(method_weights[name] * scores[name] for name in scores)
    assert result.aggregate_score == expected_aggregate
    if 1.0 in method_weights.values():
        selected_method = next(name for name, weight in method_weights.items() if weight == 1.0)
        assert result.aggregate_score == scores[selected_method]
    artifact = result.with_input_identities(
        {
            "capture_json": ContentIdentity(size=1, sha256="a" * 64),
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        }
    ).as_dict()
    assert tuple(artifact) == ("aggregate_score", "input_identities", "methods", "observation_window_seconds")
    methods_document = cast(dict[str, dict[str, object]], artifact["methods"])
    assert all(tuple(method) == ("diagnostics", "score", "weight") for method in methods_document.values())


@pytest.mark.parametrize(
    ("zero_weight_method", "component_name"),
    [
        ("frame_size_ks", "frame_size_ks"),
        ("iat_ks", "iat_ks"),
        ("autocorrelation", "autocorrelation_similarity"),
        ("multiscale_rate", "multiscale_rate_similarity"),
    ],
)
def test_compare_traces_propagates_each_zero_weight_component_failure(
    valid_config_data: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    zero_weight_method: str,
    component_name: str,
) -> None:
    """A failed zero-weight component is evidence failure, not a score that can be ignored."""
    data = copy.deepcopy(valid_config_data)
    weights = {name: 0.0 for name in ("frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate")}
    weights[next(name for name in weights if name != zero_weight_method)] = 1.0
    cast(dict[str, object], data["similarity"])["method_weights"] = weights
    failure = TrafficlabError(f"{zero_weight_method} failed", corrective_action="repair the component")

    def fail(*_args: object) -> SimilarityResult:
        raise failure

    monkeypatch.setattr(comparison, component_name, fail)

    with pytest.raises(TrafficlabError) as captured:
        compare_traces(_trace(), _trace(), 3.0, _settings(data))

    assert captured.value is failure


def test_compare_traces_propagates_a_component_failure(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Converting evaluator failure into a low score would disguise broken infrastructure as poor science."""
    primary = TrafficlabError("injected evaluator failure", corrective_action="repair the evaluator")

    def fail_metric(*_args: object) -> SimilarityResult:
        raise primary

    monkeypatch.setattr(comparison, "iat_ks", fail_metric)

    with pytest.raises(TrafficlabError) as error:
        compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    assert error.value is primary


def test_compare_traces_translates_invalid_component_result_assembly(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A result-invariant defect must use the comparison stage's corrective package error boundary."""

    def invalid_window_type(*_args: object) -> SimilarityResult:
        return SimilarityResult(1.0, {"observation_window_seconds": 3})

    monkeypatch.setattr(comparison, "frame_size_ks", invalid_window_type)

    with pytest.raises(TrafficlabError, match="invalid comparison result") as error:
        compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    assert error.value.corrective_action == "report the comparison result assembly defect"


def test_compare_traces_clamps_only_accepted_weight_sum_roundoff(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid configuration within sum tolerance must not leak a raw aggregate range ValueError."""
    data = copy.deepcopy(valid_config_data)
    similarity_data = cast(dict[str, object], data["similarity"])
    similarity_data["method_weights"] = {
        "frame_size_ks": 0.25,
        "iat_ks": 0.25,
        "autocorrelation": 0.25,
        "multiscale_rate": 0.2500000000005,
    }

    baseline = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    def identical(name: str) -> Callable[..., SimilarityResult]:
        def evaluate(*_args: object) -> SimilarityResult:
            method = baseline.methods[name]
            return SimilarityResult(method.score, cast(dict[str, object], method.as_dict()["diagnostics"]))

        return evaluate

    monkeypatch.setattr(comparison, "frame_size_ks", identical("frame_size_ks"))
    monkeypatch.setattr(comparison, "iat_ks", identical("iat_ks"))
    monkeypatch.setattr(comparison, "autocorrelation_similarity", identical("autocorrelation"))
    monkeypatch.setattr(comparison, "multiscale_rate_similarity", identical("multiscale_rate"))

    result = compare_traces(_trace(), _trace(), 3.0, _settings(data))

    assert result.aggregate_score == 1.0
