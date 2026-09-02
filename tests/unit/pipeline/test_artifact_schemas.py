"""Public schema registry coverage for core scientific artifacts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, TypeAdapter, ValidationError

from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS
from trafficlab.common.errors import FailureOutcomeRecord
from trafficlab.common.scientific_schema import SCIENTIFIC_ARTIFACT_SCHEMA_VERSION
from trafficlab.comparison.codec import parse_comparison_result
from trafficlab.comparison.diagnostics import MethodDiagnostic
from trafficlab.comparison.schema import ComparisonResult, MethodComparison
from trafficlab.generation.models.fitted_schema import AcdPayload, FamilyPayload
from trafficlab.study_evidence.report import StudyBootstrapInterval

_ROOT = Path(__file__).parents[3]
_SIMILARITY_FIXTURE = Path(__file__).parents[3] / "examples" / "data" / "similarity.json"
_FAILURE_FIXTURE = _ROOT / "tests" / "fixtures" / "data" / "diagnostics" / "failure-outcomes.jsonl"


def _checked_study_artifacts(filename: str) -> list[object]:
    paths = [_ROOT / "tests" / "fixtures" / "data" / "validation_study" / "candidate" / filename]
    return [json.loads(path.read_bytes()) for path in paths]


def _current_artifacts(root: Path, filename: str) -> list[object]:
    historical = (_ROOT / "examples" / "validation_study" / "evidence").resolve()
    return [
        document
        for path in sorted(root.glob(f"**/{filename}"))
        if not path.resolve().is_relative_to(historical)
        for document in (json.loads(path.read_bytes()),)
        if document.get("scientific_artifact_schema") == SCIENTIFIC_ARTIFACT_SCHEMA_VERSION
    ]


def _has_current_comparison_methods(document: object) -> bool:
    if type(document) is not dict:
        return False
    methods = cast(dict[object, object], document).get("methods")
    return type(methods) is dict and len(cast(dict[object, object], methods)) == 8


def test_public_core_artifact_roots_are_strict_frozen_pydantic_models() -> None:
    """Replacing a root with a permissive record would re-admit coercion and mutation."""
    assert type(PUBLIC_ARTIFACT_MODELS) is MappingProxyType
    assert tuple(PUBLIC_ARTIFACT_MODELS) == (
        "best_model",
        "capture_metadata",
        "checkpoint",
        "comparison_result",
        "failure_outcome",
        "study_environment",
        "study_lifecycle",
        "study_lineage",
        "study_manifest",
        "study_prerequisite",
        "study_protocol",
        "study_report",
        "study_report_input",
    )

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


def test_every_checked_core_artifact_validates_against_its_published_schema() -> None:
    """A public schema must describe canonical persisted bytes rather than internal runtime fields."""
    checked = {
        "capture_metadata": [
            json.loads((_ROOT / "examples" / "data" / relative).read_bytes())
            for relative in ("capture.json", "fit/capture.json")
        ],
        "best_model": [
            document
            for root in (_ROOT / "examples", _ROOT / "tests" / "fixtures")
            for document in _current_artifacts(root, "best_model.json")
        ],
        "comparison_result": [
            document
            for root in (_ROOT / "examples", _ROOT / "tests" / "fixtures")
            for path in sorted(root.glob("**/similarity.json"))
            for document in (json.loads(path.read_bytes()),)
            if _has_current_comparison_methods(document)
        ],
        "checkpoint": [
            document
            for root in (_ROOT / "examples", _ROOT / "tests" / "fixtures")
            for document in _current_artifacts(root, "checkpoint.json")
        ],
        "failure_outcome": [json.loads(line) for line in _FAILURE_FIXTURE.read_text(encoding="utf-8").splitlines()],
        **{
            f"study_{name}": _checked_study_artifacts(filename)
            for name, filename in {
                "environment": "environment.json",
                "lifecycle": "lifecycle.json",
                "lineage": "index.json",
                "manifest": "manifest.json",
                "prerequisite": "prerequisites.json",
                "protocol": "protocol.json",
                "report": "report.json",
                "report_input": "report_inputs.json",
            }.items()
        },
    }

    assert all(checked.values())
    for name, documents in checked.items():
        model = PUBLIC_ARTIFACT_MODELS[name]
        schema = model.model_json_schema(mode="validation")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for document in documents:
            validator.validate(document)  # pyright: ignore[reportUnknownMemberType]
            model.model_validate(document)


def test_published_comparison_schema_rejects_nonpublishable_states() -> None:
    """The publication schema must require lineage and statically bind diagnostics to each method key."""
    document = json.loads(_SIMILARITY_FIXTURE.read_bytes())
    schema = PUBLIC_ARTIFACT_MODELS["comparison_result"].model_json_schema(mode="validation")
    validator = Draft202012Validator(schema)

    missing_lineage = copy.deepcopy(document)
    missing_lineage["input_identities"] = None
    assert not validator.is_valid(missing_lineage)  # pyright: ignore[reportUnknownMemberType]

    wrong_method = copy.deepcopy(document)
    wrong_method["methods"]["frame_size_ks"] = copy.deepcopy(wrong_method["methods"]["iat_ks"])
    assert not validator.is_valid(wrong_method)  # pyright: ignore[reportUnknownMemberType]


def test_family_and_method_payloads_publish_union_schemas() -> None:
    """Dropping variant unions would let family and diagnostic payloads drift independently."""
    family_schema = TypeAdapter(FamilyPayload).json_schema()
    method_schema = TypeAdapter(MethodDiagnostic).json_schema()

    assert len(family_schema["oneOf"]) == 5
    assert family_schema["oneOf"][-1] == {"$ref": "#/$defs/AcdPayload"}
    acd_schema = family_schema["$defs"][AcdPayload.__name__]
    assert acd_schema["title"] == "AcdPayload"
    assert acd_schema["additionalProperties"] is False
    assert acd_schema["required"] == ["omega", "alpha", "beta", "marks"]
    assert len(method_schema["oneOf"]) == 8


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
        score=frame_size.score,
        weight=frame_size.weight,
        diagnostics=frame_size.diagnostics,
    )
    assert rebuilt == frame_size

    wrong_methods = result.methods.model_dump(mode="python")
    wrong_methods["frame_size_ks"] = wrong_methods["iat_ks"]
    with pytest.raises(ValidationError, match="wrong method discriminator"):
        ComparisonResult.model_validate(
            {
                **result.model_dump(mode="python"),
                "methods": wrong_methods,
            }
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


def test_study_bootstrap_schema_rejects_changed_policy_and_inverted_bounds() -> None:
    document = _checked_study_artifacts("report_inputs.json")[0]
    report_inputs = cast(dict[str, object], document)
    training = cast(list[dict[str, object]], report_inputs["training"])
    runtime = cast(dict[str, object], training[0]["runtime_seconds"])
    bootstrap = cast(dict[str, object], runtime["bootstrap"])

    assert StudyBootstrapInterval.model_validate(bootstrap).confidence_level == 0.95
    changed_confidence = copy.deepcopy(bootstrap)
    changed_confidence["confidence_level"] = 0.9
    with pytest.raises(ValidationError, match="confidence_level"):
        StudyBootstrapInterval.model_validate(changed_confidence)

    inverted = copy.deepcopy(bootstrap)
    inverted["lower_bound"] = cast(float, bootstrap["upper_bound"]) + 1.0
    with pytest.raises(ValidationError, match="lower_bound"):
        StudyBootstrapInterval.model_validate(inverted)
