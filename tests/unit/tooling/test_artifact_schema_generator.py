"""Deterministic public JSON Schema publication contracts."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts import generate_artifact_schemas as schemas
from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS
from trafficlab.common.config import ExperimentConfig

REPOSITORY = Path(__file__).resolve().parents[3]
_RELEASE_FAMILIES = (
    "poisson_empirical",
    "markov_renewal",
    "mmpp",
    "nhpp",
    "acd",
    "markov_packet_train",
    "packet_hmm",
)
_FITNESS_METHODS = (
    "frame_size_ks",
    "iat_ks",
    "autocorrelation",
    "multiscale_rate",
    "cramer_von_mises",
    "anderson_darling",
    "jensen_shannon",
    "approximate_mmd",
)


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
    """Schema publication must expose every registered model and its strict fitted payload."""
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
    assert '"acd"' in best_model
    assert '"acd"' in checkpoint
    assert '"AcdPayload"' in best_model
    assert '"markov_packet_train"' in best_model
    assert '"markov_packet_train"' in checkpoint
    assert '"MarkovPacketTrainPayload"' in best_model
    assert '"packet_hmm"' in best_model
    assert '"packet_hmm"' in checkpoint
    assert '"PacketHmmPayload"' in best_model
    assert len(documents["best_model.schema.json"]["$defs"]["FamilyPayload"]["oneOf"]) == 7


def test_release_configs_enable_exactly_all_families_and_equal_fitness_weights() -> None:
    """Release templates must carry every configured family and one equal contribution per mandatory method."""
    for name in ("default.toml", "minimal.toml"):
        document = tomllib.loads((REPOSITORY / "examples" / "configs" / name).read_text(encoding="utf-8"))
        config = ExperimentConfig.model_validate(document)

        assert config.models.enabled == _RELEASE_FAMILIES
        assert (
            tuple(family for family in _RELEASE_FAMILIES if getattr(config.models, family) is not None)
            == _RELEASE_FAMILIES
        )
        assert config.genetic.population_size >= config.genetic.elite_count + len(_RELEASE_FAMILIES)
        assert tuple(config.similarity.method_weights.model_dump()) == _FITNESS_METHODS
        assert config.similarity.method_weights.model_dump() == dict.fromkeys(_FITNESS_METHODS, 0.125)
        assert config.similarity.mmd_feature_count <= 65_536
        assert config.similarity.postfit.c2st.maximum_window_count <= 65_536
        assert config.similarity.postfit.c2st.fold_count >= 2
        assert config.models.packet_hmm is not None
        assert config.models.packet_hmm.state_count.lower >= 2
        assert config.models.packet_hmm.state_count.upper <= 4


def test_candidate_catalog_does_not_duplicate_normative_algorithms() -> None:
    """Implemented algorithms belong to their normative documents, not the non-normative catalog."""
    content = (REPOSITORY / "architecture" / "CANDIDATES.md").read_text(encoding="utf-8")

    for heading in (
        "### Packet-level Hidden Markov Model",
        "### Autoregressive Conditional Duration",
        "### Non-homogeneous Poisson process",
        "| **Two-sample Cramér–von Mises** |",
        "| **Anderson–Darling** |",
        "| **Jensen–Shannon divergence** |",
        "| **Approximate joint MMD** |",
        "| **Fano- and Allan-factor curves** |",
        "| **Transition-matrix fidelity** |",
        "| **Classical classifier two-sample test** |",
    ):
        assert heading not in content


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
