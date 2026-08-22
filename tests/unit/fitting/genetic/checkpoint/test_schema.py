"""Direct schema checkpoint behavior tests."""

import json
import math
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from tests.support.checkpoint import (
    VALID_STATE,
    decoded_checkpoint,
    replace,
)
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointArtifact,
    render_checkpoint,
)


def test_checkpoint_publication_root_is_strict_and_schema_describes_every_variant() -> None:
    """A generic candidate object would hide status and failure payload drift from readers."""
    assert issubclass(CheckpointArtifact, BaseModel)
    assert CheckpointArtifact.model_config.get("extra") == "forbid"
    assert CheckpointArtifact.model_config.get("frozen") is True
    assert CheckpointArtifact.model_config.get("strict") is True
    assert CheckpointArtifact.model_config.get("allow_inf_nan") is False
    assert CheckpointArtifact.model_config.get("revalidate_instances") == "always"

    schema = CheckpointArtifact.model_json_schema(mode="validation")
    Draft202012Validator.check_schema(schema)
    encoded = json.loads(render_checkpoint(VALID_STATE))
    Draft202012Validator(schema).validate(encoded)  # pyright: ignore[reportUnknownMemberType]
    validated = CheckpointArtifact.model_validate(encoded)
    assert validated.model_dump(mode="json", by_alias=True) == encoded

    schema_text = json.dumps(schema, sort_keys=True)
    for status in ("pending", "valid", "invalid"):
        assert f'"const": "{status}"' in schema_text
    for kind in (
        "repair",
        "fit",
        "generation",
        "incomplete_generation",
        "similarity_precondition",
        "nonfinite_score",
    ):
        assert f'"const": "{kind}"' in schema_text
    assert '"const": "numpy.random.Generator/PCG64"' in schema_text


@pytest.mark.parametrize(
    "mutation",
    (
        "negative-core-state",
        "core-state-overflow",
        "invalid-has-uint32",
        "uinteger-overflow",
        "bit-generator",
        "empty-trial-seeds",
        "empty-families",
        "empty-family-priority",
        "empty-population",
        "empty-history",
        "empty-gene-order",
        "empty-coordinates",
        "schema-2",
    ),
)
def test_independent_checkpoint_schema_rejects_invalid_required_array_cardinality_and_schema(
    mutation: str,
) -> None:
    """Draft 2020-12 readers must enforce the same fixed and nonempty wire arrays as Pydantic."""
    document = decoded_checkpoint()
    if mutation in {
        "negative-core-state",
        "core-state-overflow",
        "invalid-has-uint32",
        "uinteger-overflow",
        "bit-generator",
    }:
        rng = cast(dict[str, object], document["rng"])
        state = cast(dict[str, object], rng["state"])
        core = cast(dict[str, object], state["state"])
        if mutation == "negative-core-state":
            core["state"] = -1
        elif mutation == "core-state-overflow":
            core["state"] = 2**128
        elif mutation == "invalid-has-uint32":
            state["has_uint32"] = 2
        elif mutation == "uinteger-overflow":
            state["uinteger"] = 2**32
        else:
            state["bit_generator"] = "Philox"
    elif mutation == "empty-gene-order":
        cast(list[dict[str, object]], document["families"])[0]["gene_order"] = []
    elif mutation == "empty-coordinates":
        cast(list[dict[str, object]], document["families"])[0]["coordinates"] = []
    elif mutation == "schema-2":
        document["scientific_artifact_schema"] = 2
    else:
        document[mutation.removeprefix("empty-").replace("-", "_")] = []

    schema = CheckpointArtifact.model_json_schema(mode="validation")
    validator = Draft202012Validator(schema)
    assert not validator.is_valid(cast(Any, document))  # pyright: ignore[reportUnknownMemberType]


def test_checkpoint_schema_revalidates_nested_instances_from_primitives() -> None:
    """Constructed nested models must not carry bypassed invalid values into publication."""
    document = decoded_checkpoint()
    validated = CheckpointArtifact.model_validate(document)
    poisoned = replace(validated.population[0], fitness=math.inf)
    with pytest.raises(Exception, match="fitness"):
        CheckpointArtifact.model_validate(
            {
                **validated.model_dump(mode="python", by_alias=True),
                "population": [poisoned, *validated.population[1:]],
            }
        )


def test_checkpoint_registry_contains_publication_root() -> None:
    """Schema consumers need the checkpoint root in the shared deterministic registry."""
    from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS

    assert PUBLIC_ARTIFACT_MODELS["checkpoint"] is CheckpointArtifact


def test_checkpoint_persists_exact_content_identities_and_trial_limits() -> None:
    """Hash-only lineage or omitted trial limits cannot establish compatible resume."""
    document = decoded_checkpoint()

    assert document["experiment_identity"] == {"size": 101, "sha256": "a" * 64}
    assert document["reference_identity"] == {"size": 102, "sha256": "b" * 64}
    assert document["capture_identity"] == {"size": 103, "sha256": "c" * 64}
    assert document["trial_limits"] == {
        "max_output_bytes": 2_000,
        "max_packets": 1_000,
        "max_wall_seconds": 3.0,
    }
    assert not {"experiment_sha256", "reference_sha256", "capture_sha256"} & set(document)
