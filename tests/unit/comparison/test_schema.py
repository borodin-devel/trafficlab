import copy
import math
from typing import cast

import pytest

import trafficlab.comparison.schema as comparison_schema
from tests.support.comparison import settings as _settings
from tests.support.comparison import trace as _trace
from tests.support.comparison import valid_result_document
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.trace import TrafficTrace
from trafficlab.comparison.metrics import compare_traces, evaluate_postfit
from trafficlab.comparison.schema import ComparisonResult, FanoAllanDiagnostic

_smoothed_probabilities = comparison_schema._smoothed_probabilities  # pyright: ignore[reportPrivateUsage]


def _final_result(valid_config_data: dict[str, object]) -> ComparisonResult:
    settings = _settings(valid_config_data)
    trace = TrafficTrace.from_events(_trace())
    return (
        compare_traces(trace, trace, 3.0, settings)
        .with_postfit_diagnostics(evaluate_postfit(trace, trace, 3.0, settings))
        .with_input_identities(
            {
                "capture_json": ContentIdentity(size=1, sha256="a" * 64),
                "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
                "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
                "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
            }
        )
    )


def _replace_transition_with_one_state(diagnostics: dict[str, object]) -> None:
    """Build a hand-derived, arithmetically consistent one-event transition payload."""
    vocabulary = cast(list[object], diagnostics["vocabulary"])
    state = cast(list[object], cast(list[object], diagnostics["reference_states"])[0])
    state_index = vocabulary.index(state)
    state_count = len(vocabulary)
    pseudocount = cast(float, diagnostics["pseudocount"])
    occupancy_counts = [0] * state_count
    occupancy_counts[state_index] = 1
    occupancy_denominator = 1.0 + pseudocount * state_count
    occupancy_probabilities = [
        (count + pseudocount) / occupancy_denominator for count in occupancy_counts
    ]
    empty_counts = [0] * state_count
    uniform = [1.0 / state_count] * state_count
    diagnostics["reference_states"] = [state]
    diagnostics["generated_states"] = [state]
    diagnostics["occupancy"] = {
        "reference_counts": occupancy_counts,
        "generated_counts": occupancy_counts,
        "reference_probabilities": occupancy_probabilities,
        "generated_probabilities": occupancy_probabilities,
        "jsd": 0.0,
    }
    diagnostics["transitions"] = {
        "reference_counts": [empty_counts[:] for _ in vocabulary],
        "generated_counts": [empty_counts[:] for _ in vocabulary],
        "rows": [
            {
                "source": source,
                "reference_probabilities": uniform,
                "generated_probabilities": uniform,
                "jsd": 0.0,
            }
            for source in vocabulary
        ],
        "jsd": 0.0,
    }
    diagnostics["runs"] = {
        "vocabulary": [1, "overflow"],
        "reference_counts": [1, 0],
        "generated_counts": [1, 0],
        "reference_probabilities": [0.75, 0.25],
        "generated_probabilities": [0.75, 0.25],
        "jsd": 0.0,
    }
    diagnostics["component_jsd"] = {"occupancy": 0.0, "transition_rows": 0.0, "runs": 0.0}
    diagnostics["discrepancy"] = 0.0


def test_final_result_publishes_exact_typed_postfit_keys_and_shared_window(
    valid_config_data: dict[str, object],
) -> None:
    """The final artifact has one exact three-diagnostic namespace under the shared W."""
    result = _final_result(valid_config_data)
    document = result.as_dict()
    postfit = cast(dict[str, dict[str, object]], document["postfit_diagnostics"])

    assert tuple(document) == (
        "aggregate_score",
        "input_identities",
        "methods",
        "observation_window_seconds",
        "postfit_diagnostics",
    )
    assert tuple(postfit) == ("classical_c2st", "fano_allan", "transition_matrix")
    assert all(tuple(value) == ("diagnostics", "score") for value in postfit.values())
    assert all(cast(dict[str, object], value["diagnostics"])["observation_window_seconds"] == 3.0 for value in postfit.values())


def test_fitness_result_cannot_publish_without_final_postfit_diagnostics(
    valid_config_data: dict[str, object],
) -> None:
    """Adding lineage to a GA-compatible fitness result must not turn it into a final artifact."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data)).with_input_identities(
        {
            "capture_json": ContentIdentity(size=1, sha256="a" * 64),
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        }
    )

    with pytest.raises(ValueError, match="post-fit diagnostics are required"):
        result.as_dict()


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "window",
        "score",
        "convergence",
        "extra",
        "fold-order",
        "fold-range",
        "guard-partition",
        "fano-curve",
        "transition-state-counts",
        "fano-count-length",
        "fano-direction-sum",
        "fano-width-shape",
        "fano-cell-count",
        "fano-scale-width",
        "fano-scale-weights",
        "fano-one-window",
        "transition-size-threshold",
        "transition-iat-threshold",
        "transition-active-count",
        "transition-cell-count",
        "transition-count-rows",
        "transition-rows-length",
        "transition-row-source",
        "transition-runs-shape",
        "transition-run-vocabulary",
        "transition-component-jsd",
        "transition-overflow-pseudocount",
        "fold-duplicate",
        "training-balance",
        "evaluation-balance",
        "feature-names",
        "window-cap",
        "fold-count",
        "oof-count",
        "coefficient-shape",
        "evaluation-coverage",
        "evaluation-order",
        "iteration-cap",
        "scale-shape",
        "c2st-window-width-count",
        "c2st-maximum-cap",
        "training-order",
        "guard-order",
        "noncanonical-fold-sizes",
        "fano-width-above-window",
        "fano-window-count",
        "transition-vocabulary",
        "transition-threshold-order",
        "transition-one-event",
        "transition-bin-cap",
    ],
)
def test_final_result_rejects_corrupt_postfit_arithmetic_or_shape(
    valid_config_data: dict[str, object], corruption: str
) -> None:
    """Post-fit fields cannot bypass exact keys, shared W, score mapping, or solver convergence."""
    document = copy.deepcopy(_final_result(valid_config_data).as_dict())
    postfit = cast(dict[str, object], document["postfit_diagnostics"])
    c2st = cast(dict[str, object], postfit["classical_c2st"])
    diagnostics = cast(dict[str, object], c2st["diagnostics"])
    if corruption == "missing":
        postfit.pop("transition_matrix")
    elif corruption == "window":
        diagnostics["observation_window_seconds"] = 4.0
    elif corruption == "score":
        c2st["score"] = 0.25
    elif corruption == "convergence":
        cast(list[dict[str, object]], diagnostics["folds"])[0]["converged"] = False
    elif corruption == "extra":
        diagnostics["unexpected"] = 1
    elif corruption == "fold-order":
        cast(list[dict[str, object]], diagnostics["folds"])[0]["fold_index"] = 1
    elif corruption == "fold-range":
        cast(list[int], cast(list[dict[str, object]], diagnostics["folds"])[0]["training_window_indexes"])[
            -1
        ] = 999
    elif corruption == "guard-partition":
        cast(list[int], cast(list[dict[str, object]], diagnostics["folds"])[0]["guard_window_indexes"])[0] = 999
    elif corruption == "fano-curve":
        fano = cast(dict[str, object], postfit["fano_allan"])
        fano_diagnostics = cast(dict[str, object], fano["diagnostics"])
        scale = cast(dict[str, object], cast(list[object], fano_diagnostics["scales"])[0])
        cast(dict[str, object], scale["reference_fano"])["total"] = 99.0
    elif corruption == "transition-state-counts":
        transition = cast(dict[str, object], postfit["transition_matrix"])
        transition_diagnostics = cast(dict[str, object], transition["diagnostics"])
        states = cast(list[object], transition_diagnostics["reference_states"])
        vocabulary = cast(list[object], transition_diagnostics["vocabulary"])
        states[0] = vocabulary[-1]
    elif corruption.startswith("fano-"):
        fano = cast(dict[str, object], postfit["fano_allan"])
        fano_diagnostics = cast(dict[str, object], fano["diagnostics"])
        scales = cast(list[dict[str, object]], fano_diagnostics["scales"])
        if corruption == "fano-count-length":
            cast(list[int], cast(dict[str, object], scales[0]["reference_counts"])["total"]).pop()
        elif corruption == "fano-direction-sum":
            cast(list[int], cast(dict[str, object], scales[0]["reference_counts"])["total"])[0] += 1
        elif corruption == "fano-width-shape":
            cast(list[float], fano_diagnostics["scale_differences"]).pop()
        elif corruption == "fano-cell-count":
            fano_diagnostics["total_direction_window_cells"] = cast(
                int, fano_diagnostics["total_direction_window_cells"]
            ) + 2
        elif corruption == "fano-scale-width":
            scales[0]["width_seconds"] = 0.5
        elif corruption == "fano-one-window":
            old_count = cast(int, scales[0]["window_count"])
            scales[0]["window_count"] = 1
            for side in ("reference_counts", "generated_counts"):
                counts = cast(dict[str, object], scales[0][side])
                for channel in ("total", "outbound", "inbound"):
                    counts[channel] = cast(list[int], counts[channel])[:1]
            for curve_name in ("reference_fano", "generated_fano", "reference_allan", "generated_allan"):
                scales[0][curve_name] = {"total": 0.0, "outbound": 0.0, "inbound": 0.0}
            scales[0]["component_differences"] = {"fano": 0.0, "allan": 0.0}
            scales[0]["discrepancy"] = 0.0
            cast(list[float], fano_diagnostics["scale_differences"])[0] = 0.0
            fano_diagnostics["total_direction_window_cells"] = cast(
                int, fano_diagnostics["total_direction_window_cells"]
            ) - 2 * (old_count - 1)
        elif corruption == "fano-width-above-window":
            cast(list[float], fano_diagnostics["widths"])[-1] = 4.0
            scales[-1]["width_seconds"] = 4.0
        elif corruption == "fano-window-count":
            cast(list[float], fano_diagnostics["widths"])[-1] = 1.5
            scales[-1]["width_seconds"] = 1.5
        else:
            fano_diagnostics["scale_weights"] = [1.0, 1.0]
    elif corruption.startswith("transition-"):
        transition = cast(dict[str, object], postfit["transition_matrix"])
        transition_diagnostics = cast(dict[str, object], transition["diagnostics"])
        transitions = cast(dict[str, object], transition_diagnostics["transitions"])
        if corruption == "transition-size-threshold":
            cast(list[float], transition_diagnostics["log_size_thresholds"]).pop()
        elif corruption == "transition-iat-threshold":
            cast(list[float], transition_diagnostics["log_iat_thresholds"]).pop()
        elif corruption == "transition-active-count":
            transition_diagnostics["active_state_count"] = cast(int, transition_diagnostics["active_state_count"]) + 1
        elif corruption == "transition-cell-count":
            transition_diagnostics["transition_cell_count"] = cast(int, transition_diagnostics["transition_cell_count"]) + 1
        elif corruption == "transition-count-rows":
            cast(list[int], cast(list[object], transitions["reference_counts"])[0])[0] += 1
        elif corruption == "transition-rows-length":
            cast(list[object], transitions["rows"]).pop()
        elif corruption == "transition-row-source":
            rows = cast(list[dict[str, object]], transitions["rows"])
            vocabulary = cast(list[object], transition_diagnostics["vocabulary"])
            rows[0]["source"] = vocabulary[-1]
        elif corruption == "transition-runs-shape":
            runs = cast(dict[str, object], transition_diagnostics["runs"])
            cast(list[object], runs["vocabulary"]).pop()
        elif corruption == "transition-run-vocabulary":
            runs = cast(dict[str, object], transition_diagnostics["runs"])
            cast(list[object], runs["vocabulary"])[0] = 2
        elif corruption == "transition-vocabulary":
            occupancy = cast(dict[str, object], transition_diagnostics["occupancy"])
            reference_counts = cast(list[int], occupancy["reference_counts"])
            generated_counts = cast(list[int], occupancy["generated_counts"])
            reference_rows = cast(list[list[int]], transitions["reference_counts"])
            generated_rows = cast(list[list[int]], transitions["generated_counts"])
            unused = next(
                index
                for index in range(len(reference_counts))
                if reference_counts[index] == generated_counts[index] == 0
                and not any(row[index] for row in (*reference_rows, *generated_rows))
                and not any((*reference_rows[index], *generated_rows[index]))
            )
            vocabulary = cast(list[list[object]], transition_diagnostics["vocabulary"])
            vocabulary[unused] = ["outbound", 99, "initial"]
            cast(list[dict[str, object]], transitions["rows"])[unused]["source"] = vocabulary[unused]
        elif corruption == "transition-threshold-order":
            cast(list[float], transition_diagnostics["log_size_thresholds"]).reverse()
        elif corruption == "transition-one-event":
            _replace_transition_with_one_state(transition_diagnostics)
        elif corruption == "transition-bin-cap":
            transition_diagnostics["size_bin_count"] = 38
            transition_diagnostics["log_size_thresholds"] = [float(index) for index in range(39)]
        elif corruption == "transition-component-jsd":
            cast(dict[str, object], transition_diagnostics["component_jsd"])["occupancy"] = 0.25
        else:
            transition_diagnostics["pseudocount"] = 1e308
    else:
        folds = cast(list[dict[str, object]], diagnostics["folds"])
        if corruption == "fold-duplicate":
            training = cast(list[int], folds[0]["training_window_indexes"])
            training.append(training[-1])
        elif corruption == "training-balance":
            folds[0]["training_reference_count"] = cast(int, folds[0]["training_reference_count"]) + 1
        elif corruption == "evaluation-balance":
            folds[0]["evaluation_generated_count"] = cast(int, folds[0]["evaluation_generated_count"]) + 1
        elif corruption == "feature-names":
            cast(list[str], diagnostics["feature_names"])[0] = "changed"
        elif corruption == "window-cap":
            diagnostics["maximum_window_count"] = 1
        elif corruption == "fold-count":
            diagnostics["fold_count"] = 4
        elif corruption == "oof-count":
            diagnostics["out_of_fold_reference_count"] = cast(int, diagnostics["out_of_fold_reference_count"]) + 1
        elif corruption == "coefficient-shape":
            cast(list[float], diagnostics["coefficients"]).pop()
        elif corruption == "evaluation-coverage":
            cast(list[int], folds[-1]["evaluation_window_indexes"]).pop()
            folds[-1]["evaluation_reference_count"] = cast(int, folds[-1]["evaluation_reference_count"]) - 1
            folds[-1]["evaluation_generated_count"] = cast(int, folds[-1]["evaluation_generated_count"]) - 1
        elif corruption == "evaluation-order":
            cast(list[int], folds[0]["evaluation_window_indexes"]).reverse()
        elif corruption == "iteration-cap":
            folds[0]["iterations"] = cast(int, diagnostics["maximum_iterations"]) + 1
        elif corruption == "c2st-window-width-count":
            diagnostics["window_width_seconds"] = 0.3
        elif corruption == "c2st-maximum-cap":
            diagnostics["maximum_window_count"] = 65_537
        elif corruption == "training-order":
            cast(list[int], folds[0]["training_window_indexes"]).reverse()
        elif corruption == "guard-order":
            cast(list[int], folds[1]["guard_window_indexes"]).reverse()
        elif corruption == "noncanonical-fold-sizes":
            partitions = (
                ((0, 1, 2), (3,), tuple(range(4, 12))),
                ((3, 4, 5, 6, 7), (2, 8), (0, 1, 9, 10, 11)),
                ((8, 9, 10, 11), (7,), tuple(range(7))),
            )
            for fold, (evaluation, guard, training) in zip(folds, partitions, strict=True):
                fold["evaluation_window_indexes"] = list(evaluation)
                fold["guard_window_indexes"] = list(guard)
                fold["training_window_indexes"] = list(training)
                fold["evaluation_reference_count"] = len(evaluation)
                fold["evaluation_generated_count"] = len(evaluation)
                fold["training_reference_count"] = len(training)
                fold["training_generated_count"] = len(training)
        else:
            cast(list[float], folds[0]["reference_training_scale"]).pop()

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


def test_fano_allan_schema_rejects_direction_cells_above_fixed_cap() -> None:
    """Self-consistent vectors must not bypass the documented 65,536 direction-cell cap."""
    window_count = 32_769
    zeros = [0] * window_count
    counts = {"total": zeros, "outbound": zeros, "inbound": zeros}
    curves = {"total": 0.0, "outbound": 0.0, "inbound": 0.0}
    scale = {
        "width_seconds": 1.0,
        "window_count": window_count,
        "reference_counts": counts,
        "generated_counts": counts,
        "reference_fano": curves,
        "generated_fano": curves,
        "reference_allan": curves,
        "generated_allan": curves,
        "component_differences": {"fano": 0.0, "allan": 0.0},
        "discrepancy": 0.0,
    }

    with pytest.raises(ValueError, match="65536|direction-window.*cap"):
        FanoAllanDiagnostic.model_validate(
            {
                "observation_window_seconds": 32_769.0,
                "widths": [1.0],
                "scale_weights": [1.0],
                "component_weights": {"fano": 0.5, "allan": 0.5},
                "total_direction_window_cells": 65_538,
                "scales": [scale],
                "component_differences": {"fano": 0.0, "allan": 0.0},
                "scale_differences": [0.0],
                "discrepancy": 0.0,
            }
        )


def test_smoothed_probabilities_reject_overflow_before_division() -> None:
    """A finite huge pseudocount must fail before producing zero or nonfinite probabilities."""
    with pytest.raises(ValueError, match="pseudocount.*safely"):
        _smoothed_probabilities((0, 0), 1e308)


def test_similarity_artifact_retains_exact_nested_input_content_identities() -> None:
    """Digest-only lineage cannot prove the authoritative bytes used by comparison."""
    document = valid_result_document()
    identities = cast(dict[str, object], document["input_identities"])

    assert tuple(identities) == (
        "capture_json",
        "generated_pcapng",
        "reference_pcapng",
        "similarity_settings",
    )
    assert all(set(cast(dict[str, object], value)) == {"size", "sha256"} for value in identities.values())
    assert "input_sha256" not in document


@pytest.mark.parametrize("score", [-0.01, 1.01, math.inf, math.nan])
def test_comparison_result_rejects_an_unbounded_or_nonfinite_aggregate(score: float) -> None:
    """An invalid aggregate must never cross the typed artifact boundary."""
    with pytest.raises(ValueError, match="aggregate_score"):
        ComparisonResult.model_validate(
            {
                "aggregate_score": score,
                "observation_window_seconds": 3.0,
                "methods": {},
                "input_identities": None,
            }
        )


def test_comparison_result_requires_exact_method_names(valid_config_data: dict[str, object]) -> None:
    """A missing method must not cross the typed result boundary even when remaining methods are valid."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))
    incomplete = dict(result.methods)
    incomplete.pop("iat_ks")

    with pytest.raises(ValueError, match="methods.iat_ks"):
        ComparisonResult.model_validate(
            {
                "aggregate_score": result.aggregate_score,
                "observation_window_seconds": 3.0,
                "methods": incomplete,
                "input_identities": None,
            }
        )


def test_comparison_result_requires_typed_method_values(valid_config_data: dict[str, object]) -> None:
    """A mapping-shaped substitute must not bypass method score, weight, and diagnostic validation."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))
    invalid_methods: dict[str, object] = dict(result.methods)
    invalid_methods["iat_ks"] = {"score": 1.0, "weight": 0.25, "diagnostics": {}}

    with pytest.raises(ValueError, match="methods.iat_ks"):
        ComparisonResult.model_validate(
            {
                "aggregate_score": result.aggregate_score,
                "observation_window_seconds": 3.0,
                "methods": invalid_methods,
                "input_identities": None,
            }
        )


@pytest.mark.parametrize(
    "identities",
    [
        {
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        },
        {
            "capture_json": 1,
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        },
    ],
    ids=["missing-input", "non-string-input"],
)
def test_comparison_result_defensively_rejects_invalid_identity_mappings(
    valid_config_data: dict[str, object], identities: dict[str, object]
) -> None:
    """Direct typed-result construction must enforce the complete lowercase SHA-256 identity map."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    with pytest.raises(ValueError, match="input_identities"):
        ComparisonResult.model_validate(
            {
                "aggregate_score": result.aggregate_score,
                "observation_window_seconds": 3.0,
                "methods": result.methods,
                "input_identities": identities,
            }
        )
