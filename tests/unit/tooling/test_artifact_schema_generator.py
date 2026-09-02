"""Deterministic public JSON Schema publication contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts import generate_artifact_schemas as schemas
from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS


def test_every_public_root_has_one_canonical_draft_2020_12_schema() -> None:
    """Omitting or renaming a public root would make independent artifact validation incomplete."""
    documents = schemas.build_schema_documents()
    expected_names = tuple(f"{name}.schema.json" for name in sorted(PUBLIC_ARTIFACT_MODELS))

    assert len(documents) == 13
    assert tuple(documents) == expected_names
    for filename, content in documents.items():
        document = json.loads(content)
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["$id"] == filename
        assert content == schemas.canonical_schema_bytes(document)
        Draft202012Validator.check_schema(document)


def test_schema_five_directory_contains_current_fitness_and_model_roots() -> None:
    """Schema publication must expose every registered model and exclude future-only names."""
    assert schemas.OUTPUT_DIRECTORY.name == "scientific-artifact-v5"
    documents = {name: json.loads(content) for name, content in schemas.build_schema_documents().items()}
    checkpoint = json.dumps(documents["checkpoint.schema.json"], sort_keys=True)
    best_model = json.dumps(documents["best_model.schema.json"], sort_keys=True)
    comparison = json.dumps(documents["comparison_result.schema.json"], sort_keys=True)
    assert '"const": 5' in checkpoint
    assert '"const": 5' in best_model
    for method in (
        "autocorrelation",
        "frame_size_ks",
        "iat_ks",
        "multiscale_rate",
        "cramer_von_mises",
        "anderson_darling",
        "jensen_shannon",
        "approximate_mmd",
    ):
        assert f'"{method}"' in comparison
    assert '"nhpp"' in best_model
    for future_family in ("packet_hmm", "markov_packet_train", "acd"):
        assert f'"{future_family}"' not in best_model


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


def test_schema_directory_preserves_its_readme(tmp_path: Path) -> None:
    """Regenerating schemas must not delete the documentation stored beside them."""
    readme = tmp_path / "README.md"
    readme.write_text("# Public schemas\n", encoding="utf-8")

    schemas.write_schema_directory(tmp_path)

    assert readme.read_text(encoding="utf-8") == "# Public schemas\n"
    assert schemas.schema_directory_mismatches(tmp_path) == ()


def test_schema_directory_check_rejects_symlink_and_nonregular_entries(tmp_path: Path) -> None:
    """Following a link or ignoring a special file would make the checked schema tree nonportable."""
    schemas.write_schema_directory(tmp_path)
    first = next(iter(schemas.build_schema_documents()))
    expected_path = tmp_path / first
    outside = tmp_path.parent / "linked-schema.json"
    outside.write_bytes(expected_path.read_bytes())
    expected_path.unlink()
    expected_path.symlink_to(outside)

    assert schemas.schema_directory_mismatches(tmp_path) == (f"nonregular:{first}",)

    expected_path.unlink()
    expected_path.write_bytes(schemas.build_schema_documents()[first])
    os.mkfifo(tmp_path / "foreign.pipe")

    assert schemas.schema_directory_mismatches(tmp_path) == ("nonregular:foreign.pipe",)
