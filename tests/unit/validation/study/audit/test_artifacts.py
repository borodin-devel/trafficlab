"""Artifacts behavior."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.validation_study.audit.artifacts as vs_audit_artifacts
import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.environment as vs_audit_environment
import scripts.validation_study.audit.lifecycle as vs_audit_lifecycle
import scripts.validation_study.audit.science as vs_audit_science
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
import scripts.validation_study.results.reproduction as vs_results_reproduction
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.artifacts import (
    candidate_index,
    rewrite_candidate_manifest,
    tree_inventory,
    write_candidate_index,
    write_canonical_json,
)
from tests.support.validation_study.repository import copy_validation_study_candidate
from tests.unit.validation.study.audit._audit_support import (
    NONOPERATIONAL_CONFIG_MUTATIONS,
    NONOPERATIONAL_REALIZED_CONFIG_MUTATIONS,
    auditor_semantics_fixture_config,
    candidate_bytes,
    config_semantic_leaf_paths,
    config_semantic_path_value,
    config_semantic_replacements,
    nonoperational_config_mutation,
    set_config_semantic_path_value,
)
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import FailureOutcome, TrafficlabError
from trafficlab.generation.models.fitted_model import load_best_model, rebuild_best_model, render_best_model


def test_auditor_sample_summary_rejects_wrong_cardinality_and_nonfinite_values() -> None:
    with pytest.raises(vs_audit_common.Issue, match="requires finite observations"):
        vs_audit_science._sample_summary([1.0, 2.0], name="runtime")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(vs_audit_common.Issue, match="requires finite observations"):
        vs_audit_science._sample_summary(  # pyright: ignore[reportPrivateUsage]
            [1.0, math.nan, 3.0], name="runtime"
        )


def test_offline_auditor_allows_a_clean_committed_accepted_bundle(tmp_path: Path) -> None:
    """A relocated descendant may check accepted evidence in without making its worktree dirty."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    accepted = repository / "examples" / "validation_study" / "evidence" / candidate.name
    accepted.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, accepted)
    shutil.rmtree(candidate)
    relative = accepted.relative_to(repository).as_posix()
    for command in (
        ("git", "add", "--", relative),
        (
            "git",
            "-c",
            "user.name=Trafficlab Test",
            "-c",
            "user.email=trafficlab-test@example.invalid",
            "commit",
            "-m",
            "test accepted evidence",
        ),
    ):
        subprocess.run(command, cwd=repository, check=True, capture_output=True)
    assert not subprocess.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert vs_audit_lifecycle.audit_bundle(accepted, repository=repository).bundle == accepted


def test_offline_auditor_does_not_exempt_an_external_staged_source_candidate(tmp_path: Path) -> None:
    """Only source candidates beneath the relocated repository can suppress worktree evidence."""

    repository, candidate = copy_validation_study_candidate(tmp_path)

    assert (
        vs_audit_lifecycle.audit_staged_bundle(
            candidate,
            repository=repository,
            source_candidate=tmp_path / "external-candidate",
        ).bundle
        == candidate
    )


def test_offline_auditor_never_treats_the_repository_root_as_a_candidate_exemption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller cannot hide every source path by naming the repository as its candidate."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def source_paths(_repository: Path) -> tuple[str, ...]:
        return ("source.py",)

    monkeypatch.setattr(vs_audit_environment, "_relocated_worktree_paths", source_paths)

    with pytest.raises(vs_audit_common.Issue, match="non-evidence working-tree change") as captured:
        vs_audit_environment.require_permitted_relocated_worktree(
            repository,
            candidate=repository,
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_foreign", "environment")


@pytest.mark.parametrize("case", ("symlink", "nonregular"))
def test_offline_auditor_rejects_untracked_nonregular_source_paths(tmp_path: Path, case: str) -> None:
    """Filesystem special entries outside retained evidence cannot become audit inputs."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    source = repository / f"foreign-{case}"
    if case == "symlink":
        source.symlink_to("scripts/audit_validation_study.py")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("nonregular FIFO entries require POSIX")
        os.mkfifo(source)

    with pytest.raises(TrafficlabError) as captured:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_foreign",
        "environment",
        "not_published",
    )


@pytest.mark.parametrize("case", ("directory", "entry"))
def test_offline_auditor_covers_special_entry_scan_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Unreadable worktree directories and entries have canonical local diagnostics."""

    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "source.py"
    source.write_text("sentinel = True\n", encoding="utf-8")
    original_iterdir = Path.iterdir
    original_lstat = Path.lstat

    def failing_iterdir(path: Path) -> Any:
        if case == "directory" and path == repository:
            raise OSError("synthetic directory failure")
        return original_iterdir(path)

    def failing_lstat(path: Path) -> os.stat_result:
        if case == "entry" and path == source:
            raise OSError("synthetic entry failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    monkeypatch.setattr(Path, "lstat", failing_lstat)

    with pytest.raises(vs_audit_common.Issue, match="could not inspect relocated working-tree") as captured:
        vs_audit_environment._relocated_worktree_entry_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            candidate_paths=(),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_rejects_a_non_utf8_special_entry_path(tmp_path: Path) -> None:
    """Filesystem paths that cannot be rendered into Git's UTF-8 protocol remain corrupt."""

    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(vs_audit_common.Issue, match="working-tree path is not UTF-8") as captured:
        vs_audit_environment._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("bad\udcff",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


@pytest.mark.parametrize("mismatch", ("protocol", "prerequisites"))
def test_offline_auditor_covers_root_study_identity_rejections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """The public bundle checker rejects conflicting candidate, protocol, and prerequisite IDs first."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    entries = vs_audit_artifacts.verify_inventory(
        candidate,
        (candidate / "manifest.json").read_bytes(),
    )

    def empty_environment(_content: bytes, *, repository: Path) -> dict[str, object]:
        return {}

    def mismatched_prerequisites(*_args: object, **_kwargs: object) -> tuple[dict[str, object], set[str]]:
        return {"study_id": "fixture-study"}, set()

    def wrong_protocol(_content: bytes) -> dict[str, object]:
        return {"study_id": "other-study"}

    def matching_protocol(_content: bytes) -> dict[str, object]:
        return {"study_id": "fixture-study"}

    def wrong_prerequisites(*_args: object, **_kwargs: object) -> tuple[dict[str, object], set[str]]:
        return {"study_id": "other-study"}, set()

    monkeypatch.setattr(vs_audit_lifecycle, "load_environment", empty_environment)
    if mismatch == "protocol":
        monkeypatch.setattr(vs_audit_lifecycle, "load_protocol", wrong_protocol)
        monkeypatch.setattr(vs_audit_lifecycle, "load_prerequisites", mismatched_prerequisites)
        expected = "protocol destination ID"
    else:
        monkeypatch.setattr(vs_audit_lifecycle, "load_protocol", matching_protocol)
        monkeypatch.setattr(vs_audit_lifecycle, "load_prerequisites", wrong_prerequisites)
        expected = "retained prerequisites must bind"

    with pytest.raises(vs_audit_common.Issue, match=expected):
        vs_audit_lifecycle._audit(  # pyright: ignore[reportPrivateUsage]
            candidate, repository, entries
        )


def test_offline_auditor_config_semantics_masks_only_declared_operational_paths() -> None:
    """Relocation may alter only the run directory and host-side mount source."""

    baseline = auditor_semantics_fixture_config()
    relocated_mount = baseline.target.mounts[0].model_copy(update={"source": Path("/relocated/mount")})
    relocated_target = baseline.target.model_copy(update={"mounts": (relocated_mount,)})
    relocated = baseline.model_copy(
        update={
            "run": baseline.run.model_copy(update={"directory": Path("/relocated/run")}),
            "target": relocated_target,
        }
    )

    assert vs_audit_environment.config_semantics(relocated) == vs_audit_environment.config_semantics(baseline)


@pytest.mark.parametrize("case", NONOPERATIONAL_CONFIG_MUTATIONS)
def test_offline_auditor_config_semantics_rejects_each_nonoperational_mutation(case: str) -> None:
    """Every scientific/workload field remains part of the retained config identity."""

    baseline = auditor_semantics_fixture_config()
    mutated = nonoperational_config_mutation(baseline, case)

    assert vs_audit_environment.config_semantics(mutated) != vs_audit_environment.config_semantics(baseline)


def test_offline_auditor_config_semantics_retains_every_nonoperational_control() -> None:
    """Only the two documented host-path classes are removed from config comparison."""

    baseline = auditor_semantics_fixture_config()
    document = baseline.model_dump(mode="json")
    paths = config_semantic_leaf_paths(document)
    assert paths
    for path in paths:
        value = config_semantic_path_value(document, path)
        for replacement in config_semantic_replacements(path, value):
            mutated_document = copy.deepcopy(document)
            set_config_semantic_path_value(mutated_document, path, replacement)
            try:
                mutated = ExperimentConfig.model_validate(mutated_document)
            except ValueError:
                continue
            assert vs_audit_environment.config_semantics(mutated) != vs_audit_environment.config_semantics(baseline)
            break
        else:
            raise AssertionError(f"no valid semantic mutation for config path {path}")


@pytest.mark.parametrize("case", NONOPERATIONAL_REALIZED_CONFIG_MUTATIONS)
def test_offline_auditor_rejects_each_nonoperational_realized_config_mutation(
    tmp_path: Path,
    case: str,
) -> None:
    """A portable/realized pair rejects every non-operational relocation mutation."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    realized_path = candidate / "configs" / "training-short-r1.realized.toml"
    original = realized_path.read_bytes()
    baseline = ExperimentConfig.model_validate(tomllib.loads(original.decode("utf-8")))
    realized_path.write_bytes(render_effective_config(nonoperational_config_mutation(baseline, case)))
    rewrite_candidate_manifest(candidate)

    with pytest.raises(
        TrafficlabError, match="realized configuration does not match its portable configuration"
    ) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (
        "artifact_foreign",
        "publication",
        "configs/training-short-r1.realized.toml",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize("target", ("manifest", "run-log"))
def test_offline_bundle_audit_rejects_duplicate_json_keys_at_the_owned_boundary(
    tmp_path: Path,
    target: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    if target == "manifest":
        (candidate / "manifest.json").write_bytes(b'{"files":[],"files":[],"schema_version":2}\n')
    else:
        log_path = candidate / "training" / "short" / "r1" / "run.log"
        log_path.write_bytes(b'{"event":"fixture","event":"duplicate","stage":"fit"}\n')
        rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.stage == "publication"
    assert outcome.authority == "primary"


def test_offline_bundle_audit_derives_w_from_the_normalized_reference(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    model_path = candidate / "training" / "short" / "r1" / "best_model.json"
    model = load_best_model(model_path.read_bytes(), source=model_path)
    model_path.write_bytes(
        render_best_model(rebuild_best_model(model, observation_window_seconds=model.observation_window_seconds + 1.0))
    )
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
    ) == (
        "scientific_semantics_incompatible",
        "publication",
        "best model final controls do not match normalized training reference",
        "training/short/r1",
        "not_published",
        "restore frozen training evidence",
        "primary",
    )


@pytest.mark.parametrize(
    ("relative", "content"),
    (
        ("training/short/r1/experiment.toml", b"[run\n"),
        ("training/short/r1/run.log", b"\xff\n"),
        ("training/short/r1/run.log", b'{"event": "fixture"}\n'),
    ),
)
def test_offline_bundle_audit_rejects_noncanonical_owned_artifact_boundaries(
    tmp_path: Path,
    relative: str,
    content: bytes,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    (candidate / relative).write_bytes(content)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.stage == "publication"
    assert outcome.evidence_state == "not_published"


@pytest.mark.parametrize(
    ("content", "detail"),
    (
        (b"", "run log must be nonempty canonical JSONL with a terminal newline"),
        (b'{}\r{"event":"fixture"}\n', "run log must use LF-terminated records"),
    ),
)
def test_offline_bundle_audit_covers_the_remaining_canonical_jsonl_boundaries(
    tmp_path: Path,
    content: bytes,
    detail: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    (candidate / "training" / "short" / "r1" / "run.log").write_bytes(content)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.detail, outcome.affected_evidence) == (
        "artifact_corrupt",
        detail,
        "training/short/r1/run.log",
    )


@pytest.mark.parametrize("case", ("stored_record", "identity"))
def test_offline_bundle_audit_covers_fresh_simulation_record_boundaries(tmp_path: Path, case: str) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    record = cast(list[dict[str, object]], index["fresh_simulation"])[0]
    path = candidate / cast(str, record["path"])
    if case == "stored_record":
        stored = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        stored["seed"] = 98
        write_canonical_json(path, stored)
    else:
        identity = cast(dict[str, object], record["reference_identity"])
        identity["sha256"] = "0" * 64
        write_canonical_json(path, record)
        write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == ("artifact_foreign", cast(str, record["path"]))


@pytest.mark.parametrize(
    ("relative", "owner", "relation"),
    (
        (
            "prerequisites/docker_matrix.command.json",
            "prerequisite:docker_matrix:command.json",
            {"relation": "prerequisite", "record": "docker_matrix.command.json"},
        ),
        (
            "headers/prerequisites/00-prerequisites/capability.headers",
            "transfer-header:prerequisites:00-prerequisites:0",
            {
                "filename": "capability.headers",
                "relation": "transfer-header",
                "requested_end": 0,
                "requested_start": 0,
                "run_id": "00-prerequisites",
                "scope": "prerequisites",
                "transfer_index": 0,
                "workload": "prerequisites",
            },
        ),
        (
            "observations/held_out/held-out-streaming/streaming.headers.json",
            "external-observation:held_out:held-out-streaming:0",
            {
                "filename": "streaming.headers",
                "relation": "external-observation",
                "requested_end": 4_194_303,
                "requested_start": 0,
                "run_id": "held-out-streaming",
                "scope": "held_out",
                "transfer_index": 0,
                "workload": "streaming",
            },
        ),
        (
            "configs/training-short-r1.portable.toml",
            "configuration:training-short-r1.portable",
            {"relation": "configuration", "name": "training-short-r1.portable"},
        ),
        (
            "training/bursty/r2/run.log",
            "training:bursty:r2",
            {"relation": "run.log", "repeat": 2, "workload": "bursty"},
        ),
        (
            "fresh_simulation/short/r3.json",
            "fresh-simulation:short:r3",
            {"relation": "fresh_simulation", "repeat": 3, "workload": "short"},
        ),
        ("held_out/bursty/reference.pcapng", "held-out:bursty", {"relation": "reference.pcapng", "workload": "bursty"}),
    ),
)
def test_schema_owner_and_lineage_mapping_cover_every_retained_evidence_family(
    relative: str,
    owner: str,
    relation: dict[str, object],
) -> None:
    assert vs_audit_artifacts.owner_for_path(relative) == owner
    assert vs_audit_artifacts.lineage_for_path(relative) == relation


@pytest.mark.parametrize(
    "relative",
    (
        "prerequisites/unknown.command.json",
        "headers/unknown.headers",
        "observations/unknown.json",
        "not-documented.bin",
    ),
)
def test_schema_owner_mapping_rejects_partial_or_unknown_paths(relative: str) -> None:
    with pytest.raises(Exception, match="documented owner"):
        vs_audit_artifacts.owner_for_path(relative)


def test_schema_lineage_mapping_rejects_unknown_path_family() -> None:
    with pytest.raises(Exception, match="documented lineage"):
        vs_audit_artifacts.lineage_for_path("not-documented.bin")


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("duplicate_fresh", "artifact_foreign"),
        ("missing_schema_path", "artifact_missing"),
        ("unlisted_schema_path", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_internal_complete_schema_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    if case == "duplicate_fresh":

        def duplicate_fresh(*_args: object, **_kwargs: object) -> str:
            return "fresh_simulation/short/r1.json"

        monkeypatch.setattr(vs_audit_lifecycle, "rebuild_fresh", duplicate_fresh)
    else:
        original = vs_audit_artifacts.build_expected_paths

        def altered_expected(
            index: dict[str, object],
            protocol: dict[str, object],
            prerequisite_paths: set[str],
            training: Sequence[Any],
            fresh_paths: set[str],
            held_paths: set[str],
        ) -> set[str]:
            result = original(index, protocol, prerequisite_paths, training, fresh_paths, held_paths)
            if case == "missing_schema_path":
                return result | {"missing-schema-path.json"}
            return result - {"report.json"}

        monkeypatch.setattr(vs_audit_lifecycle, "build_expected_paths", altered_expected)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


def test_audit_bundle_wraps_an_unclassified_owner_error_and_preserves_a_classified_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    unclassified = TrafficlabError("unclassified owner error", corrective_action="repair source evidence")

    def raise_unclassified(*_args: object, **_kwargs: object) -> object:
        raise unclassified

    monkeypatch.setattr(vs_audit_lifecycle, "_audit", raise_unclassified)
    with pytest.raises(TrafficlabError) as first:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)
    first_outcome = first.value.failure_outcome
    assert first.value is unclassified
    assert first_outcome is not None
    assert (
        first_outcome.kind,
        first_outcome.affected_evidence,
        first_outcome.corrective_action,
        first_outcome.authority,
    ) == ("artifact_corrupt", "candidate evidence", "repair source evidence", "primary")

    classified_outcome = FailureOutcome(
        kind="artifact_missing",
        stage="fit",
        detail="classified owner error",
        affected_evidence="best_model.json",
        evidence_state="not_published",
        corrective_action="restore best model",
        authority="primary",
    )
    classified = TrafficlabError("classified owner error", corrective_action="restore best model")
    classified.failure_outcomes = (classified_outcome,)
    classified.failure_outcome = classified_outcome

    def raise_classified(*_args: object, **_kwargs: object) -> object:
        raise classified

    monkeypatch.setattr(vs_audit_lifecycle, "_audit", raise_classified)
    with pytest.raises(TrafficlabError) as second:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)
    assert second.value is classified
    assert second.value.failure_outcomes == (classified_outcome,)


def test_offline_bundle_fixture_carries_complete_real_program_validation_evidence_and_reconstructs_it(
    tmp_path: Path,
) -> None:
    """A retained candidate distinguishes training, fresh simulation, and independent held-out evidence."""
    repository, candidate = copy_validation_study_candidate(tmp_path)
    before = candidate_bytes(candidate)
    index = json.loads((candidate / "index.json").read_text(encoding="utf-8"))

    assert index["schema_version"] == 4
    assert set(index) == {
        "environment",
        "fresh_simulation",
        "held_out",
        "lifecycle",
        "lineage",
        "ownership",
        "prerequisites",
        "protocol",
        "report",
        "report_inputs",
        "schema_version",
        "training",
    }
    assert index["lifecycle"] == "lifecycle.json"
    expected_training = {(workload, repeat) for workload in ("short", "streaming", "bursty") for repeat in (1, 2, 3)}
    training = index["training"]
    assert {(item["workload"], item["repeat"]) for item in training} == expected_training
    assert {(item["workload"], item["repeat"]) for item in index["fresh_simulation"]} == expected_training
    assert {item["workload"] for item in index["held_out"]} == {"short", "streaming", "bursty"}

    training_reference_identities = {item["reference_identity"]["sha256"] for item in training}
    assert len(training_reference_identities) == len(expected_training)
    assert all(
        json.loads((candidate / item["directory"] / "record.json").read_text(encoding="utf-8"))["reference_identity"][
            "sha256"
        ]
        not in training_reference_identities
        for item in index["held_out"]
    )
    for item in training:
        directory = candidate / item["directory"]
        lines = (directory / "run.log").read_bytes().splitlines(keepends=True)
        assert lines
        assert all(
            line
            == json.dumps(
                json.loads(line.decode("utf-8")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
            for line in lines
        )

    result = vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    assert result.file_count == len(before) - 1
    assert candidate_bytes(candidate) == before


def test_retained_prerequisite_codec_freezes_all_output_identities_and_aggregates_production_junit() -> None:
    """Runner, generator, and auditor share one exact retained prerequisite contract."""
    url = "https://downloads.example.test/object.bin"
    study_id = "fixture-study"
    outputs = {
        "docker_matrix": {
            "stdout": b"docker passed\n",
            "stderr": b"",
            "junit": b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/><testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>',
        },
        "internet_smoke": {
            "stdout": b"internet passed\n",
            "stderr": b"",
            "junit": b'<testsuite tests="1" failures="0" errors="0" skipped="0"/>',
        },
    }
    commands: list[dict[str, object]] = []
    for kind in ("docker_matrix", "internet_smoke"):
        values = outputs[kind]
        argv = list(vs_prereq_commands.prerequisite_command_argv(kind, study_id=study_id, url=url))
        tests = vs_prereq_commands.prerequisite_junit_counts(values["junit"])
        commands.append(
            {
                "argv": argv,
                "command": {
                    "identity": identify_bytes(
                        json.dumps({"argv": argv}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
                    ).as_dict(),
                    "path": f"prerequisites/{kind}.command.json",
                },
                "exit_status": 0,
                "junit": {
                    "identity": identify_bytes(values["junit"]).as_dict(),
                    "path": f"prerequisites/{kind}.junit.xml",
                },
                "kind": kind,
                "status": {
                    "identity": identify_bytes(
                        json.dumps({"exit_status": 0, "tests": tests}, sort_keys=True, separators=(",", ":")).encode(
                            "utf-8"
                        )
                        + b"\n"
                    ).as_dict(),
                    "path": f"prerequisites/{kind}.status.json",
                },
                "stderr": {
                    "identity": identify_bytes(values["stderr"]).as_dict(),
                    "path": f"prerequisites/{kind}.stderr",
                },
                "stdout": {
                    "identity": identify_bytes(values["stdout"]).as_dict(),
                    "path": f"prerequisites/{kind}.stdout",
                },
                "tests": tests,
            }
        )
    capability_header = b"HTTP/1.1 206 Partial Content\r\nContent-Length: 1\r\nContent-Range: bytes 0-0/4194304\r\n\r\n"
    document = {
        "capability": {
            "canary_sha256": hashlib.sha256(capability_header).hexdigest(),
            "content_length": 1,
            "content_range": "bytes 0-0/4194304",
            "object_size_bytes": 4_194_304,
            "status": 206,
        },
        "commands": commands,
        "environment": {
            "capture_image_id": f"sha256:{'d' * 64}",
            "capture_image_reference": f"trafficlab-capture@sha256:{'c' * 64}",
            "capture_tool_version": "4.0.17",
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "target_image_id": f"sha256:{vs_common.TARGET_REFERENCE.rsplit(':', 1)[-1]}",
            "target_image_reference": vs_common.TARGET_REFERENCE,
            "uv_lock_identity": identify_bytes(b"locked\n").as_dict(),
        },
        "schema_version": 4,
        "study_id": study_id,
        "url": url,
    }

    rendered = vs_prereq_codec.render_retained_prerequisites(document)
    parsed = vs_prereq_codec.parse_retained_prerequisites(rendered)

    assert vs_prereq_codec.render_retained_prerequisites(parsed) == rendered
    commands = cast(list[dict[str, object]], parsed["commands"])
    assert commands[0]["tests"] == {"errors": 0, "failed": 0, "passed": 3, "skipped": 0, "total": 3}


def test_simultaneous_evidence_mismatches_preserve_the_first_complete_primary_and_all_inventories(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    missing = candidate / "training" / "short" / "r1" / "best_model.json"
    missing.unlink()
    (candidate / "training" / "short" / "r1" / "checkpoint.json").write_bytes(b"corrupt\n")
    (candidate / "foreign.bin").write_bytes(b"foreign\n")
    (candidate / "training" / "short" / "r1" / "generated.pcapng").write_bytes(
        (candidate / "training" / "short" / "r2" / "generated.pcapng").read_bytes()
    )
    evidence_root = repository / "examples" / "validation_study" / "evidence"
    destination = evidence_root / "fixture-study"
    (repository / "inventory-sentinel").symlink_to("candidate")
    candidate_before = tree_inventory(candidate)
    evidence_before = tree_inventory(evidence_root)
    repository_before = tree_inventory(repository)
    assert repository_before["inventory-sentinel"] == ("symlink", "candidate")

    with pytest.raises(TrafficlabError) as error:
        vs_results_reproduction.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
    ) == (
        "artifact_missing",
        "publication",
        "training/short/r1/best_model.json is missing from the retained bundle",
        "training/short/r1/best_model.json",
        "not_published",
        "restore the exact retained artifact",
        "primary",
    )
    assert tree_inventory(candidate) == candidate_before
    assert tree_inventory(evidence_root) == evidence_before
    assert tree_inventory(repository) == repository_before
    assert not destination.exists()


def test_retained_prerequisite_codec_rejects_invalid_public_forms() -> None:
    """The public retained codec rejects unsupported roots, kinds, and noncanonical bytes."""
    content = (VALIDATION_STUDY_CANDIDATE / "prerequisites.json").read_bytes()
    noncanonical = content.replace(b"{", b"{ ", 1)
    assert noncanonical != content

    with pytest.raises(ValueError, match="root must be testsuite or testsuites"):
        vs_prereq_commands.prerequisite_junit_counts(b"<unexpected/>")
    with pytest.raises(ValueError, match="prerequisite kind"):
        vs_prereq_commands.prerequisite_command_argv(
            "unsupported", study_id="fixture-study", url="https://downloads.example.test/object.bin"
        )
    with pytest.raises(ValueError, match="prerequisite kind"):
        vs_prereq_commands.validate_frozen_prerequisite_command(
            "unsupported",
            (),
            0,
            {},
            study_id="fixture-study",
            url="https://downloads.example.test/object.bin",
        )
    with pytest.raises(ValueError, match="canonical sorted readable"):
        vs_prereq_codec.parse_retained_prerequisites(noncanonical)
