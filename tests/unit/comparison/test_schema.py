import math
from typing import cast

import pytest

from tests.support.comparison import settings as _settings
from tests.support.comparison import trace as _trace
from tests.support.comparison import valid_result_document
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult


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
