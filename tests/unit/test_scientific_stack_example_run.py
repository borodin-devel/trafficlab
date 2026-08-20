"""Durable real full-workflow example-run evidence contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Never

import pytest

from scripts import check_scientific_stack_example as example_run
from trafficlab.config import ExperimentConfig, MountConfig
from trafficlab.config_io import ConfigurationPair, load_configuration_pair, realize_configuration

_ROOT = Path(__file__).parents[2]
_EVIDENCE = _ROOT / "examples" / "scientific_stack" / "example_run.json"


def test_durable_example_tracks_all_nine_artifacts_for_clean_checkouts() -> None:
    artifact_root = "examples/scientific_stack/example_run_artifacts"
    required = {
        f"{artifact_root}/best_model.json",
        f"{artifact_root}/capture.json",
        f"{artifact_root}/checkpoint.json",
        f"{artifact_root}/experiment.toml",
        f"{artifact_root}/ga_history.csv",
        f"{artifact_root}/generated.pcapng",
        f"{artifact_root}/reference.pcapng",
        f"{artifact_root}/run.log",
        f"{artifact_root}/similarity.json",
    }

    completed = subprocess.run(
        ("git", "ls-files", "--", artifact_root),
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert set(completed.stdout.splitlines()) == required


def test_checked_example_run_recomputes_artifacts_and_result() -> None:
    content = _EVIDENCE.read_bytes()
    evidence = example_run.parse_and_validate_evidence(content, repository_root=_ROOT)

    assert len(evidence["source"]["commit"]) == 40
    assert evidence["source"]["source_clean"] is True
    assert evidence["execution"]["exit_status"] == 0
    assert evidence["execution"]["target_argv"][-2:] == ["--url", evidence["execution"]["url"]]
    assert evidence["result"]["enabled_families"] == ["poisson_empirical", "markov_renewal", "mmpp"]
    assert evidence["result"]["winner_family"] in evidence["result"]["enabled_families"]
    assert evidence["result"]["reference_packet_count"] > 0
    assert evidence["result"]["generated_packet_count"] > 0
    assert evidence["cleanup"]["containers"] == []
    assert evidence["cleanup"]["networks"] == []
    assert evidence["cleanup"]["volumes"] == []
    assert evidence["cleanup"]["verified"] is True
    assert content == example_run.canonical_json_bytes(evidence)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "exact",
        "command",
        "target_argv",
        "source_tree",
        "config",
        "lock",
        "inventory",
        "artifact",
        "aggregate",
        "cleanup",
    ],
)
def test_example_run_evidence_rejects_fabricated_or_unverifiable_facts(mutation: str) -> None:
    evidence = example_run.parse_and_validate_evidence(_EVIDENCE.read_bytes(), repository_root=_ROOT)
    changed = copy.deepcopy(evidence)
    if mutation == "unknown":
        changed["unknown"] = True
    elif mutation == "exact":
        changed["execution"]["command"] = tuple(changed["execution"]["command"])
    elif mutation == "command":
        changed["execution"]["command"] = ["true"]
    elif mutation == "target_argv":
        changed["execution"]["target_argv"] = ["--url", changed["execution"]["url"]]
    elif mutation == "source_tree":
        changed["source"]["tree"] = "0" * 40
    elif mutation == "config":
        changed["source"]["config_identity"]["sha256"] = "0" * 64
    elif mutation == "lock":
        changed["source"]["uv_lock_identity"]["sha256"] = "0" * 64
    elif mutation == "inventory":
        del changed["artifacts"]["ga_history.csv"]
    elif mutation == "artifact":
        changed["artifacts"]["similarity.json"]["sha256"] = "0" * 64
    elif mutation == "aggregate":
        changed["result"]["aggregate_score"] += 0.01
    else:
        changed["cleanup"]["project_name"] = "trafficlab-capture-00000000000000000000000000000000"

    with pytest.raises(ValueError):
        example_run.validate_evidence(changed, repository_root=_ROOT)


def test_example_run_parser_rejects_invalid_and_noncanonical_json() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        example_run.parse_and_validate_evidence(b"{")
    document = json.loads(_EVIDENCE.read_bytes())
    with pytest.raises(ValueError, match="canonical"):
        example_run.parse_and_validate_evidence(json.dumps(document, indent=2).encode("utf-8"), repository_root=_ROOT)


def test_example_run_recording_requires_a_clean_checkout_at_the_exact_source_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later clean-looking record must not rewrite whether the executed source snapshot was clean."""
    source_commit = "a" * 40

    def clean_git(_repository: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return source_commit
        if arguments == ("status", "--porcelain", "--untracked-files=all"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(example_run, "_git", clean_git)
    assert example_run._clean_source_record(tmp_path, source_commit) == {  # pyright: ignore[reportPrivateUsage]
        "source_clean": True,
        "state_note": "checkout was clean at the recorded source commit before the run; retained evidence was added afterward",
    }

    def dirty_git(_repository: Path, *arguments: str) -> str:
        return source_commit if arguments == ("rev-parse", "HEAD") else " M src/x.py"

    monkeypatch.setattr(example_run, "_git", dirty_git)
    with pytest.raises(ValueError, match="clean checkout"):
        example_run._clean_source_record(tmp_path, source_commit)  # pyright: ignore[reportPrivateUsage]


def test_example_run_validator_rejects_checked_config_policy_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    real_load = example_run.load_experiment

    def changed_load(path: Path) -> ExperimentConfig:
        config = real_load(path)
        if path.resolve() == example_run.CONFIG_PATH.resolve():
            return config.model_copy(
                update={"models": config.models.model_copy(update={"enabled": ("poisson_empirical",)})}
            )
        return config

    monkeypatch.setattr(example_run, "load_experiment", changed_load)
    evidence = json.loads(_EVIDENCE.read_bytes())
    with pytest.raises(ValueError, match="checked configuration"):
        example_run.validate_evidence(evidence, repository_root=_ROOT)


@pytest.mark.parametrize("mutation", ["families", "winner", "completed"])
def test_example_run_derivation_rejects_cross_artifact_contradictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)
    if mutation == "families":
        monkeypatch.setattr(example_run, "_FAMILIES", ("poisson_empirical",))
    else:
        path = copied / "run.log"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        event = "final_validation_succeeded" if mutation == "winner" else "run_completed"
        record = next(item for item in records if item["event"] == event)
        if mutation == "winner":
            record["family"] = "mmpp"
        else:
            record["reference_packet_count"] += 1
        path.write_bytes(b"".join(example_run.canonical_json_bytes(item) for item in records))

    with pytest.raises(ValueError):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]


def test_example_run_derivation_rejects_a_structurally_valid_foreign_checkpoint_lineage(tmp_path: Path) -> None:
    """A valid checkpoint root with a changed reference SHA must fail compatibility reconstruction."""
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)
    checkpoint_path = copied / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint["reference_identity"]["sha256"] = "0" * 64
    checkpoint_path.write_bytes(example_run.canonical_json_bytes(checkpoint))

    with pytest.raises(ValueError, match="checkpoint"):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]


def test_example_run_derivation_rejects_effective_snapshot_policy_drift_with_matching_lineage(
    tmp_path: Path,
) -> None:
    """A self-consistent resume snapshot must still match the committed invoked configuration."""
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)
    experiment_path = copied / "experiment.toml"
    config = example_run.load_experiment(experiment_path)
    changed = config.model_copy(update={"genetic": config.genetic.model_copy(update={"resume": True})})
    snapshot = example_run.render_effective_config(changed)
    experiment_path.write_bytes(snapshot)
    experiment_identity = example_run.identify_bytes(snapshot)
    identity_document = {
        "sha256": experiment_identity.sha256,
        "size": experiment_identity.size,
    }

    checkpoint_path = copied / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint["experiment_identity"] = identity_document
    checkpoint["genetic"]["resume"] = True
    checkpoint_path.write_bytes(example_run.canonical_json_bytes(checkpoint))

    log_path = copied / "run.log"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    capture = next(item for item in records if item["event"] == "capture_published")
    capture["experiment_identity"] = identity_document
    log_path.write_bytes(b"".join(example_run.canonical_json_bytes(item) for item in records))

    with pytest.raises(ValueError, match="checked configuration"):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]


def test_example_run_rejects_arbitrary_run_directory_with_matching_artifact_lineage(tmp_path: Path) -> None:
    """Relocation may change only the checkout prefix, not the portable run-directory meaning."""
    repository = tmp_path / "repository"
    scientific_stack = repository / "examples" / "scientific_stack"
    scientific_stack.mkdir(parents=True)
    git_directory = subprocess.run(
        ("git", "rev-parse", "--absolute-git-dir"),
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repository / ".git").write_text(f"gitdir: {git_directory}\n", encoding="utf-8")
    shutil.copy2(_ROOT / "uv.lock", repository / "uv.lock")
    shutil.copy2(_ROOT / "examples" / "scientific_stack" / "experiment.toml", scientific_stack / "experiment.toml")
    artifacts = scientific_stack / "example_run_artifacts"
    shutil.copytree(_ROOT / "examples" / "scientific_stack" / "example_run_artifacts", artifacts)

    experiment_path = artifacts / "experiment.toml"
    config = example_run.load_experiment(experiment_path)
    arbitrary_directory = (tmp_path / "unrelated" / "output").resolve()
    changed = config.model_copy(update={"run": config.run.model_copy(update={"directory": arbitrary_directory})})
    snapshot = example_run.render_effective_config(changed)
    experiment_path.write_bytes(snapshot)
    experiment_identity = example_run.identify_bytes(snapshot)
    identity_document = {
        "sha256": experiment_identity.sha256,
        "size": experiment_identity.size,
    }

    checkpoint_path = artifacts / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint["experiment_identity"] = identity_document
    checkpoint_path.write_bytes(example_run.canonical_json_bytes(checkpoint))

    original_directory = str(config.run.directory)
    changed_directory = str(arbitrary_directory)
    log_path = artifacts / "run.log"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        for name, value in record.items():
            if isinstance(value, str) and value.startswith(original_directory):
                record[name] = changed_directory + value.removeprefix(original_directory)
    capture = next(item for item in records if item["event"] == "capture_published")
    capture["experiment_identity"] = identity_document
    log_path.write_bytes(b"".join(example_run.canonical_json_bytes(item) for item in records))

    evidence = json.loads(_EVIDENCE.read_bytes())
    evidence["artifacts"] = {
        name: {
            "sha256": hashlib.sha256((artifacts / name).read_bytes()).hexdigest(),
            "size": (artifacts / name).stat().st_size,
        }
        for name in evidence["artifacts"]
    }

    with pytest.raises(ValueError, match="checked configuration"):
        example_run.validate_evidence(evidence, repository_root=repository)


def test_example_run_configuration_comparison_rejects_a_different_mount_inventory(tmp_path: Path) -> None:
    config_path = _ROOT / "examples" / "scientific_stack" / "experiment.toml"
    checked = load_configuration_pair(config_path)
    snapshot = checked.realized.model_copy(
        update={
            "target": checked.realized.target.model_copy(
                update={"mounts": (MountConfig(source=tmp_path, target="/input"),)}
            )
        }
    )

    assert not example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        snapshot,
        checked,
        repository_root=_ROOT,
    )


def test_example_run_configuration_comparison_binds_relative_mounts_to_one_relocated_root(tmp_path: Path) -> None:
    config_path = _ROOT / "examples" / "scientific_stack" / "experiment.toml"
    base = load_configuration_pair(config_path)
    portable = base.portable.model_copy(
        update={
            "target": base.portable.target.model_copy(
                update={"mounts": (MountConfig(source=Path("../../examples/data"), target="/input"),)}
            )
        }
    )
    checked = ConfigurationPair(
        portable=portable,
        realized=realize_configuration(portable, config_path.parent.resolve()),
    )
    relocated_root = tmp_path / "relocated-checkout"
    relocated = realize_configuration(portable, relocated_root / config_path.parent.relative_to(_ROOT))
    foreign_mount = relocated.model_copy(
        update={
            "target": relocated.target.model_copy(
                update={
                    "mounts": (
                        relocated.target.mounts[0].model_copy(
                            update={"source": tmp_path / "other-checkout" / "examples" / "data"}
                        ),
                    )
                }
            )
        }
    )

    assert example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        relocated,
        checked,
        repository_root=_ROOT,
    )
    assert not example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        foreign_mount,
        checked,
        repository_root=_ROOT,
    )


def test_example_run_configuration_comparison_preserves_absolute_portable_mounts(tmp_path: Path) -> None:
    config_path = _ROOT / "examples" / "scientific_stack" / "experiment.toml"
    base = load_configuration_pair(config_path)
    absolute_source = (tmp_path / "absolute-input").resolve()
    absolute_run = (tmp_path / "absolute-run").resolve()
    portable = base.portable.model_copy(
        update={
            "run": base.portable.run.model_copy(update={"directory": absolute_run}),
            "target": base.portable.target.model_copy(
                update={"mounts": (MountConfig(source=absolute_source, target="/input"),)}
            ),
        }
    )
    checked = ConfigurationPair(
        portable=portable,
        realized=realize_configuration(portable, config_path.parent.resolve()),
    )
    relocated = realize_configuration(
        portable,
        tmp_path / "relocated-checkout" / config_path.parent.relative_to(_ROOT),
    )
    foreign = relocated.model_copy(
        update={
            "target": relocated.target.model_copy(
                update={"mounts": (relocated.target.mounts[0].model_copy(update={"source": tmp_path / "other-input"}),)}
            )
        }
    )

    assert example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        relocated,
        checked,
        repository_root=_ROOT,
    )
    assert not example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        foreign,
        checked,
        repository_root=_ROOT,
    )


def test_example_run_configuration_comparison_relocates_relative_mount_with_absolute_run(tmp_path: Path) -> None:
    config_path = _ROOT / "examples" / "scientific_stack" / "experiment.toml"
    base = load_configuration_pair(config_path)
    portable = base.portable.model_copy(
        update={
            "run": base.portable.run.model_copy(update={"directory": (tmp_path / "absolute-run").resolve()}),
            "target": base.portable.target.model_copy(
                update={"mounts": (MountConfig(source=Path("../../examples/data"), target="/input"),)}
            ),
        }
    )
    checked = ConfigurationPair(
        portable=portable,
        realized=realize_configuration(portable, config_path.parent.resolve()),
    )
    relocated = realize_configuration(
        portable,
        tmp_path / "relocated-checkout" / config_path.parent.relative_to(_ROOT),
    )
    assert checked.realized.run.directory == relocated.run.directory
    assert checked.realized.target.mounts[0].source != relocated.target.mounts[0].source

    assert example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        relocated,
        checked,
        repository_root=_ROOT,
    )


def test_example_run_configuration_comparison_rejects_relative_mount_escape(tmp_path: Path) -> None:
    config_path = _ROOT / "examples" / "scientific_stack" / "experiment.toml"
    base = load_configuration_pair(config_path)
    portable = base.portable.model_copy(
        update={
            "target": base.portable.target.model_copy(
                update={"mounts": (MountConfig(source=Path("../../../outside-input"), target="/input"),)}
            )
        }
    )
    checked = ConfigurationPair(
        portable=portable,
        realized=realize_configuration(portable, config_path.parent.resolve()),
    )
    relocated = realize_configuration(
        portable,
        tmp_path / "relocated-checkout" / config_path.parent.relative_to(_ROOT),
    )
    assert not checked.realized.target.mounts[0].source.is_relative_to(_ROOT)
    assert not relocated.target.mounts[0].source.is_relative_to(tmp_path / "relocated-checkout")

    assert not example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        relocated,
        checked,
        repository_root=_ROOT,
    )


@pytest.mark.parametrize("mutation", ["checked_outside_root", "relative_snapshot"])
def test_example_run_configuration_comparison_rejects_paths_without_one_checkout_root(
    tmp_path: Path,
    mutation: str,
) -> None:
    config_path = _ROOT / "examples" / "scientific_stack" / "experiment.toml"
    checked = load_configuration_pair(config_path)
    snapshot = checked.realized
    if mutation == "checked_outside_root":
        checked = ConfigurationPair(
            portable=checked.portable,
            realized=checked.realized.model_copy(
                update={"run": checked.realized.run.model_copy(update={"directory": tmp_path / "outside" / "run"})}
            ),
        )
    else:
        snapshot = snapshot.model_copy(
            update={"run": snapshot.run.model_copy(update={"directory": Path("relative/run")})}
        )

    assert not example_run._matches_checked_configuration(  # pyright: ignore[reportPrivateUsage]
        snapshot,
        checked,
        repository_root=_ROOT,
    )


def test_example_run_rejects_coherent_reference_derived_fitted_payload_replacement(tmp_path: Path) -> None:
    """Rebuilding every dependent artifact must not authenticate a foreign empirical fit."""
    repository = tmp_path / "repository"
    scientific_stack = repository / "examples" / "scientific_stack"
    scientific_stack.mkdir(parents=True)
    git_directory = subprocess.run(
        ("git", "rev-parse", "--absolute-git-dir"),
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repository / ".git").write_text(f"gitdir: {git_directory}\n", encoding="utf-8")
    shutil.copy2(_ROOT / "uv.lock", repository / "uv.lock")
    shutil.copy2(_ROOT / "examples" / "scientific_stack" / "experiment.toml", scientific_stack / "experiment.toml")
    artifacts = scientific_stack / "example_run_artifacts"
    shutil.copytree(_ROOT / "examples" / "scientific_stack" / "example_run_artifacts", artifacts)

    best_path = artifacts / "best_model.json"
    best_document = json.loads(best_path.read_bytes())
    assert best_document["family"] == "markov_renewal"
    for state in best_document["fitted"]["states"]:
        state["frame_lengths"].reverse()
    best_content = example_run.canonical_json_bytes(best_document)
    best_path.write_bytes(best_content)
    best = example_run.load_best_model(best_content, source=best_path)

    capture_path = artifacts / "capture.json"
    capture_content = capture_path.read_bytes()
    metadata = example_run.parse_capture_metadata(capture_content, source=capture_path)
    _, _, generated_content = example_run.reproduce_generated_pcapng(best, metadata, clock=lambda: 0.0)
    generated_path = artifacts / "generated.pcapng"
    assert generated_content != generated_path.read_bytes()
    generated_path.write_bytes(generated_content)

    reference_path = artifacts / "reference.pcapng"
    reference_content = reference_path.read_bytes()
    raw_reference = example_run.parse_pcapng_bytes_trace(reference_content, metadata, source=reference_path)
    reference, window = example_run.normalize_reference(raw_reference)
    generated = example_run.parse_pcapng_bytes_trace(generated_content, metadata, source=generated_path)
    aligned = example_run.align_generated(generated, window)
    config = example_run.load_experiment(artifacts / "experiment.toml")
    comparison = example_run.compare_traces(reference, aligned, window, config.similarity).with_input_identities(
        {
            "capture_json": example_run.identify_bytes(capture_content),
            "generated_pcapng": example_run.identify_bytes(generated_content),
            "reference_pcapng": example_run.identify_bytes(reference_content),
            "similarity_settings": example_run.similarity_settings_identity(config.similarity),
        }
    )
    (artifacts / "similarity.json").write_bytes(example_run.render_comparison_result(comparison))

    log_path = artifacts / "run.log"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    generated_record = next(
        item for item in records if item["event"] in {"generated_pcapng_published", "generated_pcapng_reused"}
    )
    generated_record["packet_count"] = len(generated)
    completed = next(item for item in records if item["event"] == "run_completed")
    completed["aggregate_score"] = comparison.aggregate_score
    completed["generated_packet_count"] = len(generated)
    log_path.write_bytes(b"".join(example_run.canonical_json_bytes(item) for item in records))

    changed_evidence = json.loads(_EVIDENCE.read_bytes())

    def identity_document(path: Path) -> dict[str, int | str]:
        content = path.read_bytes()
        return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}

    changed_evidence["artifacts"] = {
        name: identity_document(artifacts / name) for name in changed_evidence["artifacts"]
    }
    changed_evidence["result"]["aggregate_score"] = comparison.aggregate_score
    changed_evidence["result"]["generated_packet_count"] = len(generated)
    changed_evidence["result"]["method_scores"] = {
        name: comparison.methods[name].score
        for name in ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    }

    with pytest.raises(ValueError, match="fitted model"):
        example_run.validate_evidence(changed_evidence, repository_root=repository)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("best_noncanonical", "best model is not canonical"),
        ("experiment_noncanonical", "experiment snapshot is not canonical"),
        ("history", "history is not the exact checkpoint projection"),
        ("best_lineage", "winner and best-model lineage"),
        ("generated", "generated trace does not match"),
        ("similarity_noncanonical", "similarity is not canonical"),
    ],
)
def test_example_run_derivation_rejects_noncanonical_or_foreign_artifacts(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)
    if mutation == "best_noncanonical":
        path = copied / "best_model.json"
        path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2), encoding="utf-8")
    elif mutation == "experiment_noncanonical":
        path = copied / "experiment.toml"
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "history":
        path = copied / "ga_history.csv"
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "best_lineage":
        path = copied / "best_model.json"
        document = json.loads(path.read_bytes())
        document["capture_identity"]["sha256"] = "0" * 64
        path.write_bytes(example_run.canonical_json_bytes(document))
    elif mutation == "generated":
        shutil.copy2(copied / "reference.pcapng", copied / "generated.pcapng")
    else:
        path = copied / "similarity.json"
        path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("boundary", "message"),
    [
        ("fit", "fitted model cannot be reconstructed"),
        ("generation", "generated trace cannot be reconstructed"),
    ],
)
def test_example_run_derivation_translates_reconstruction_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    message: str,
) -> None:
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)

    def fail(*_args: object, **_kwargs: object) -> Never:
        raise example_run.TrafficlabError("injected reconstruction failure", corrective_action="restore evidence")

    if boundary == "fit":
        monkeypatch.setattr(example_run, "make_best_model", fail)
    else:
        monkeypatch.setattr(example_run, "reproduce_generated_pcapng", fail)

    with pytest.raises(ValueError, match=message):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("mutation", ["components", "aggregate"])
def test_example_run_derivation_recomputes_similarity_instead_of_trusting_valid_local_arithmetic(
    tmp_path: Path, mutation: str
) -> None:
    """Locally valid changed scores and matching log claims must still fail recomputation from retained traces."""
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)
    similarity_path = copied / "similarity.json"
    similarity = json.loads(similarity_path.read_bytes())
    methods = similarity["methods"]
    frame = methods["frame_size_ks"]
    if mutation == "components":
        delta = 0.01
        iat = methods["iat_ks"]
        frame["diagnostics"]["distance"] -= delta
        frame["score"] += delta
        iat["diagnostics"]["distance"] += delta
        iat["score"] -= delta
    else:
        frame["diagnostics"]["distance"] = 0.0
        frame["score"] = 1.0
        similarity["aggregate_score"] = sum(item["weight"] * item["score"] for item in methods.values())
        log_path = copied / "run.log"
        records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        next(item for item in records if item["event"] == "run_completed")["aggregate_score"] = similarity[
            "aggregate_score"
        ]
        log_path.write_bytes(b"".join(example_run.canonical_json_bytes(item) for item in records))
    similarity_path.write_bytes(example_run.canonical_json_bytes(similarity))

    with pytest.raises(ValueError, match="similarity"):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]
