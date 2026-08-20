"""Deterministic public JSON Schema publication contracts."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts import generate_artifact_schemas as schemas
from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS


def test_every_public_root_has_one_canonical_draft_2020_12_schema() -> None:
    """Omitting or renaming a public root would make independent artifact validation incomplete."""
    documents = schemas.build_schema_documents()
    expected_names = tuple(f"{name}.schema.json" for name in sorted(PUBLIC_ARTIFACT_MODELS))

    assert tuple(documents) == expected_names
    for filename, content in documents.items():
        document = json.loads(content)
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["$id"] == filename
        assert content == schemas.canonical_schema_bytes(document)
        Draft202012Validator.check_schema(document)


def test_schema_directory_check_rejects_changed_missing_and_foreign_files(tmp_path: Path) -> None:
    """A partial or hand-edited schema directory must not satisfy the deterministic check."""
    schemas.write_schema_directory(tmp_path)
    assert schemas.schema_directory_mismatches(tmp_path) == ()

    first = next(iter(schemas.build_schema_documents()))
    (tmp_path / first).write_text("{}\n", encoding="utf-8")
    (tmp_path / "foreign.schema.json").write_text("{}\n", encoding="utf-8")
    missing = tuple(schemas.build_schema_documents())[-1]
    (tmp_path / missing).unlink()

    assert schemas.schema_directory_mismatches(tmp_path) == (
        f"changed:{first}",
        "foreign:foreign.schema.json",
        f"missing:{missing}",
    )
