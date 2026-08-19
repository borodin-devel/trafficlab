"""Global compatibility marker for serialized scientific artifacts."""

from __future__ import annotations

from typing import Final, Literal

from trafficlab.errors import TrafficlabError

SCIENTIFIC_ARTIFACT_SCHEMA_VERSION: Final = 3


class ScientificArtifactSchemaError(TrafficlabError):
    """A well-formed artifact encodes scientific semantics this version cannot reuse."""


def require_current_scientific_schema(value: object, *, artifact: Literal["checkpoint", "best model"]) -> None:
    """Reject every absent or non-current scientific schema without migrating it."""
    if type(value) is int and value == SCIENTIFIC_ARTIFACT_SCHEMA_VERSION:
        return
    if artifact == "checkpoint":
        raise ScientificArtifactSchemaError(
            "checkpoint schema is incompatible",
            corrective_action="refit under the current schema in a new run directory",
        )
    raise ScientificArtifactSchemaError(
        "best model schema is incompatible",
        corrective_action="refit under the current schema",
    )
