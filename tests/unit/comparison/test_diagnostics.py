"""Direct diagnostic-schema immutability and revalidation tests."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from typing import cast

import pytest

import trafficlab.comparison.diagnostics as diagnostics
from tests.support.comparison import settings as _settings
from tests.support.comparison import trace as _trace
from tests.support.comparison import valid_result_document
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonMethods, ComparisonResult


def _invert_frame_size_range(document: dict[str, object]) -> None:
    document["reference_minimum_length"] = cast(int, document["reference_maximum_length"]) + 1


def _exceed_iat_sample_count(document: dict[str, object]) -> None:
    document["reference_zero_iat_count"] = cast(int, document["reference_iat_count"]) + 1


def _change_acf_discrepancy(document: dict[str, object]) -> None:
    document["discrepancy"] = 0.123


def _change_multiscale_cell_total(document: dict[str, object]) -> None:
    document["total_direction_bin_cells"] = cast(int, document["total_direction_bin_cells"]) + 1


@pytest.mark.parametrize(
    ("method_name", "model", "mutation", "error"),
    [
        ("frame_size_ks", diagnostics.FrameSizeDiagnostic, _invert_frame_size_range, "minimum lengths"),
        ("iat_ks", diagnostics.IatDiagnostic, _exceed_iat_sample_count, "zero-IAT counts"),
        ("autocorrelation", diagnostics.AutocorrelationDiagnostic, _change_acf_discrepancy, "discrepancy"),
        ("multiscale_rate", diagnostics.MultiscaleDiagnostic, _change_multiscale_cell_total, "cell"),
    ],
    ids=["frame-size-range", "iat-zero-count", "acf-arithmetic", "multiscale-cell-total"],
)
def test_direct_diagnostic_models_reject_their_owned_invariants(
    method_name: str,
    model: type[diagnostics.StrictArtifactModel],
    mutation: Callable[[dict[str, object]], None],
    error: str,
) -> None:
    """Each diagnostic owner must reject a locally inconsistent otherwise-shaped record."""
    methods = cast(dict[str, object], valid_result_document()["methods"])
    method = cast(dict[str, object], methods[method_name])
    document = copy.deepcopy(cast(dict[str, object], method["diagnostics"]))
    mutation(document)

    with pytest.raises(ValueError, match=error):
        model.model_validate(document)


def test_comparison_result_is_deeply_immutable(valid_config_data: dict[str, object]) -> None:
    """Mutation after evaluation could make the reported aggregate disagree with retained diagnostics."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    with pytest.raises(TypeError):
        result.methods["frame_size_ks"] = result.methods["iat_ks"]  # type: ignore[index]
    assert isinstance(result.methods, ComparisonMethods)
    with pytest.raises(TypeError):
        result.methods["frame_size_ks"].diagnostics["distance"] = 99.0  # type: ignore[index]


@pytest.mark.parametrize("diagnostic_window", [0.0, math.nan], ids=["nonpositive", "nonfinite"])
def test_comparison_result_defensively_rejects_corrupted_method_windows(
    valid_config_data: dict[str, object], diagnostic_window: float
) -> None:
    """A corrupted in-memory method object must not bypass the finite positive diagnostic-W invariant."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))
    original = result.methods["autocorrelation"]
    corrupted_diagnostics = original.diagnostics.model_copy(update={"observation_window_seconds": diagnostic_window})
    corrupted = original.model_copy(update={"diagnostics": corrupted_diagnostics})
    methods = dict(result.methods)
    methods["autocorrelation"] = corrupted

    with pytest.raises(ValueError, match="observation_window_seconds|finite"):
        ComparisonResult.model_validate(
            {
                "aggregate_score": result.aggregate_score,
                "observation_window_seconds": 3.0,
                "methods": methods,
                "input_identities": None,
            }
        )
