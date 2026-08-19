"""Public schema registry coverage for core scientific artifacts."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS
from trafficlab.comparison import ComparisonResult, MethodComparison, MethodDiagnostic, parse_comparison_result
from trafficlab.errors import FailureOutcomeRecord
from trafficlab.models.registry import FamilyPayload

_SIMILARITY_FIXTURE = Path(__file__).parents[2] / "examples" / "data" / "similarity.json"


def test_public_core_artifact_roots_are_strict_frozen_pydantic_models() -> None:
    """Replacing a root with a permissive record would re-admit coercion and mutation."""
    assert type(PUBLIC_ARTIFACT_MODELS) is MappingProxyType
    assert tuple(PUBLIC_ARTIFACT_MODELS) == ("best_model", "comparison_result", "failure_outcome")

    for model in PUBLIC_ARTIFACT_MODELS.values():
        assert issubclass(model, BaseModel)
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("frozen") is True
        assert model.model_config.get("strict") is True
        assert model.model_config.get("allow_inf_nan") is False


def test_public_core_artifact_schemas_are_draft_2020_12_compatible() -> None:
    """A root without a closed object schema cannot be published for independent readers."""
    for name, model in PUBLIC_ARTIFACT_MODELS.items():
        schema = model.model_json_schema(mode="validation")
        assert schema["type"] == "object", name
        assert schema["additionalProperties"] is False, name
        assert schema["properties"], name


def test_family_and_method_payloads_publish_union_schemas() -> None:
    """Dropping variant unions would let family and diagnostic payloads drift independently."""
    family_schema = TypeAdapter(FamilyPayload).json_schema()
    method_schema = TypeAdapter(MethodDiagnostic).json_schema()

    assert len(family_schema["oneOf"]) == 3
    assert len(method_schema["oneOf"]) == 4


def test_failure_root_rejects_boolean_status_and_is_frozen() -> None:
    """Python bool is an int subclass and must not enter an integer status artifact field."""
    values: dict[str, object] = {
        "affected_evidence": "similarity.json",
        "authority": "primary",
        "corrective_action": "correct the input",
        "detail": "metric cannot be computed",
        "evidence_state": "not_published",
        "kind": "metric_infeasible",
        "stage": "compare",
        "status": True,
    }

    with pytest.raises(ValidationError):
        FailureOutcomeRecord.model_validate(values)

    values["status"] = 23
    outcome = FailureOutcomeRecord.model_validate(values)
    with pytest.raises(ValidationError):
        outcome.status = 24  # type: ignore[misc]


def test_method_union_accepts_a_validated_variant_and_rejects_a_wrong_mapping_discriminator() -> None:
    """A method key must not relabel another method's already validated diagnostics."""
    result = parse_comparison_result(_SIMILARITY_FIXTURE.read_bytes())
    frame_size = result.methods["frame_size_ks"]

    rebuilt = MethodComparison(
        method="frame_size_ks",
        score=frame_size.score,
        weight=frame_size.weight,
        diagnostics=frame_size.diagnostics,
    )
    assert rebuilt == frame_size

    with pytest.raises(ValidationError, match="wrong method discriminator"):
        MethodComparison(
            method="iat_ks",
            score=frame_size.score,
            weight=frame_size.weight,
            diagnostics=frame_size.diagnostics,
        )


def test_comparison_root_serializes_the_valid_prepublication_state() -> None:
    """In-process results may omit identities, but publication still requires them in as_dict()."""
    published = parse_comparison_result(_SIMILARITY_FIXTURE.read_bytes())
    evaluated = ComparisonResult(
        aggregate_score=published.aggregate_score,
        observation_window_seconds=published.observation_window_seconds,
        methods=published.methods,
        input_identities=None,
    )

    assert evaluated.model_dump(mode="json")["input_identities"] is None
    with pytest.raises(ValueError, match="identities are required"):
        evaluated.as_dict()
