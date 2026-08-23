"""Environment Boundaries behavior."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import pytest

import scripts.validation_study.audit.artifacts as vs_audit_artifacts
import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.lifecycle as vs_audit_lifecycle
import scripts.validation_study.audit.science as vs_audit_science
import scripts.validation_study.collection as vs_collection
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
import scripts.validation_study.records as vs_records
import scripts.validation_study.workloads as vs_workloads
import trafficlab.study_evidence.protocol as trafficlab_study_evidence_protocol
from tests.support.validation_study.artifacts import (
    candidate_index,
    rewrite_candidate_manifest,
    write_candidate_index,
    write_canonical_json,
)
from tests.support.validation_study.constants import CAPTURE_IMAGE_ID, ROOT
from tests.support.validation_study.repository import copy_validation_study_candidate, validation_study_fixture_identity
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner
from tests.unit.validation.study.audit._boundary_support import (
    rewrite_training_configuration_identities,
    write_candidate_lifecycle,
)
from trafficlab import USER_AGENT
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError


@pytest.mark.parametrize(
    ("case", "expected_kind", "expected_path"),
    (
        ("document", "artifact_corrupt", "prerequisites.json"),
        ("environment", "artifact_foreign", "prerequisites.json"),
        ("output_identity", "artifact_foreign", "prerequisites/docker_matrix.stdout"),
        ("command", "artifact_foreign", "prerequisites/docker_matrix.command.json"),
        ("status", "artifact_foreign", "prerequisites/docker_matrix.status.json"),
        ("utf8", "artifact_corrupt", "prerequisites/docker_matrix.stdout"),
        ("junit_invalid", "artifact_corrupt", "prerequisites/docker_matrix.junit.xml"),
        ("junit_counts", "artifact_foreign", "prerequisites/docker_matrix.junit.xml"),
    ),
)
def test_offline_auditor_covers_retained_prerequisite_rejection_branches(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    case: str,
    expected_kind: str,
    expected_path: str,
) -> None:
    """Retained prerequisite output evidence is independently checked through the public audit boundary."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    prerequisite_path = candidate / "prerequisites.json"
    document = vs_prereq_codec.parse_retained_prerequisites(prerequisite_path.read_bytes())
    command = next(
        item for item in cast(list[dict[str, object]], document["commands"]) if item["kind"] == "docker_matrix"
    )

    def replace_output(field: str, content: bytes) -> None:
        output = cast(dict[str, object], command[field])
        path = candidate / cast(str, output["path"])
        path.write_bytes(content)
        output["identity"] = identify_bytes(content).as_dict()

    render_document = True
    if case == "document":
        prerequisite_path.write_bytes(b"{}\n")
        render_document = False
    elif case == "environment":
        cast(dict[str, object], document["environment"])["source_tree"] = "c" * 40
    elif case == "output_identity":
        output = cast(dict[str, object], command["stdout"])
        (candidate / cast(str, output["path"])).write_bytes(b"changed stdout\n")
        render_document = False
    elif case == "command":
        replace_output("command", vs_common.canonical_json(cast(vs_common.JsonObject, {"argv": []})))
    elif case == "status":
        replace_output(
            "status",
            vs_common.canonical_json(
                cast(
                    vs_common.JsonObject,
                    {
                        "exit_status": 0,
                        "tests": {"errors": 0, "failed": 0, "passed": 999, "skipped": 0, "total": 999},
                    },
                )
            ),
        )
    elif case == "utf8":
        replace_output("stdout", b"\xff")
    elif case == "junit_invalid":
        replace_output("junit", b"<unexpected/>")
    else:
        replace_output("junit", b'<testsuite tests="2" failures="0" errors="0" skipped="0"/>')
    if render_document:
        prerequisite_path.write_bytes(vs_prereq_codec.render_retained_prerequisites(document))
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (expected_kind, "publication", expected_path, "not_published", "primary")


@pytest.mark.parametrize("case", ("recorded_lock", "image_lock"))
def test_offline_auditor_rejects_environment_binding_after_the_first_identity_check(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    case: str,
) -> None:
    """The auditor separately binds current lock bytes and image-lock identities."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    if case == "recorded_lock":
        changed_lock = b"different committed lock\n"
        (repository / "uv.lock").write_bytes(changed_lock)
        environment["uv_lock_identity"] = identify_bytes(changed_lock).as_dict()
    else:
        environment["capture_image_id"] = f"sha256:{'e' * 64}"
    write_canonical_json(environment_path, environment)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "environment", "not_published", "primary")


def test_offline_auditor_rejects_training_configuration_with_foreign_image_lock_binding(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Every retained training configuration is bound to the prerequisite image references."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    target_reference = vs_common.TARGET_REFERENCE.encode("ascii")
    foreign_reference = b"curlimages/curl@sha256:" + b"1" * 64
    for name in ("configs/training-short-r1.portable.toml", "configs/training-short-r1.realized.toml"):
        path = candidate / name
        content = path.read_bytes()
        assert target_reference in content
        path.write_bytes(content.replace(target_reference, foreign_reference))
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "training/short/r1", "not_published", "primary")


def test_offline_auditor_rejects_training_configuration_without_the_frozen_curl_argv(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """A candidate cannot replace the frozen workload command while retaining the image lock."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    for name in ("configs/training-short-r1.portable.toml", "configs/training-short-r1.realized.toml"):
        path = candidate / name
        content = path.read_bytes()
        assert USER_AGENT.encode("ascii") in content
        path.write_bytes(content.replace(USER_AGENT.encode("ascii"), b"trafficlab/0.0 (+https://invalid.example)", 1))
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "training/short/r1", "not_published", "primary")


def test_offline_auditor_rejects_self_consistent_frozen_profile_mutations(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Internal portable/realized agreement cannot replace the frozen profile oracle."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    for relative in (
        "configs/training-short-r1.portable.toml",
        "configs/training-short-r1.realized.toml",
        "training/short/r1/experiment.toml",
    ):
        path = candidate / relative
        content = path.read_bytes()
        assert b"minimum_free_bytes = 1\n" in content
        path.write_bytes(content.replace(b"minimum_free_bytes = 1\n", b"minimum_free_bytes = 2\n", 1))
    rewrite_training_configuration_identities(candidate, workload="short", repeat=1)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "training/short/r1", "not_published", "primary")


def test_offline_auditor_requires_the_exact_frozen_model_family_set(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Every retained profile must retain the complete three-family comparison set."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_bytes()))
    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_bytes()))
    frozen = vs_audit_science.load_frozen_profiles(
        repository,
        environment=environment,
        protocol=protocol,
        url="https://downloads.example.test/object.bin",
    )["short"]
    document = frozen.model_dump(mode="python")
    models = cast(dict[str, object], document["models"])
    models["enabled"] = ["poisson_empirical", "markov_renewal"]
    del models["mmpp"]
    subset = ExperimentConfig.model_validate(document)

    with pytest.raises(vs_audit_common.Issue) as error:
        vs_audit_science._require_frozen_profile(  # pyright: ignore[reportPrivateUsage]
            subset,
            frozen,
            affected="held_out/short",
        )

    assert error.value.kind == "artifact_foreign"


def test_offline_auditor_rejects_one_generation_against_the_unpatched_frozen_profile() -> None:
    """The fast collection fixture must not relax the production two-generation oracle."""

    environment: dict[str, object] = {
        "capture_image_reference": "sha256:704e90f23055657bb8ad7108bf6650b5e83fb2b711a1168725441599b8a73859",
        "target_image_reference": (
            "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
        ),
    }
    frozen = vs_audit_science._validation_profile(  # pyright: ignore[reportPrivateUsage]
        workload="short",
        url="https://validation-study.example/object",
        environment=environment,
    )
    one_generation = frozen.model_copy(update={"genetic": frozen.genetic.model_copy(update={"generation_count": 1})})

    with pytest.raises(vs_audit_common.Issue) as error:
        vs_audit_science._require_frozen_profile(  # pyright: ignore[reportPrivateUsage]
            one_generation,
            frozen,
            affected="held_out/short",
        )

    assert error.value.kind == "artifact_foreign"


def test_offline_auditor_rejects_legacy_short_transfer_against_its_reloaded_independent_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A producer profile regression cannot alter any auditor-owned short contract."""

    environment: dict[str, object] = {
        "capture_image_reference": "sha256:704e90f23055657bb8ad7108bf6650b5e83fb2b711a1168725441599b8a73859",
        "target_image_reference": (
            "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
        ),
    }
    producer_workload_specs = vs_workloads.workload_specs

    def legacy_workload_specs(url: str) -> tuple[vs_workloads.WorkloadSpec, ...]:
        specifications = producer_workload_specs(url)
        return tuple(
            replace(
                specification,
                argv=tuple(
                    "0-262143" if item == "0-1048575" else "262144" if item == "1048576" else item
                    for item in specification.argv
                ),
                transfers=((0, 262143, "short.headers"),),
            )
            if specification.name == "short"
            else specification
            for specification in specifications
        )

    monkeypatch.setattr(vs_workloads, "workload_specs", legacy_workload_specs)
    try:
        assert {
            (binding.requested_start, binding.requested_end, binding.filename)
            for binding in vs_audit_common.TRANSFER_BINDINGS
            if binding.workload == "short"
        } == {(0, 1_048_575, "short.headers")}
        frozen = vs_audit_science._validation_profile(  # pyright: ignore[reportPrivateUsage]
            workload="short",
            url="https://validation-study.example/object",
            environment=environment,
        )
        source_commit, _source_tree = validation_study_fixture_identity()
        fixture = vs_audit_artifacts.fixture_profile(
            ROOT,
            source_commit=source_commit,
            workload="short",
            url=vs_audit_common.FIXTURE_URL,
            environment=environment,
        )
        assert "0-1048575" in frozen.target.argv
        assert "1048576" in frozen.target.argv
        assert fixture.target.argv == frozen.target.argv[:-1] + (vs_audit_common.FIXTURE_URL,)
        legacy = frozen.model_copy(
            update={
                "target": frozen.target.model_copy(
                    update={
                        "argv": tuple(
                            "0-262143" if item == "0-1048575" else "262144" if item == "1048576" else item
                            for item in frozen.target.argv
                        )
                    }
                )
            }
        )

        with pytest.raises(vs_audit_common.Issue) as error:
            vs_audit_science._require_frozen_profile(  # pyright: ignore[reportPrivateUsage]
                legacy,
                frozen,
                affected="held_out/short",
            )
        assert error.value.kind == "artifact_foreign"

        with pytest.raises(vs_audit_common.Issue) as error:
            vs_audit_science._require_config_workload_argv(  # pyright: ignore[reportPrivateUsage]
                legacy,
                workload="short",
                url="https://validation-study.example/object",
                affected="held_out/short/experiment.toml",
            )
        assert error.value.kind == "artifact_foreign"
    finally:
        monkeypatch.undo()


def test_offline_auditor_reconstructs_nonfixture_profiles_and_rejects_a_missing_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-fixture profile oracle is independent of candidate-generated bytes."""

    environment: dict[str, object] = {
        "capture_image_reference": "sha256:704e90f23055657bb8ad7108bf6650b5e83fb2b711a1168725441599b8a73859",
        "source_commit": "3a3c401c9e4a55115a66c879d719180c6d1ddffc",
        "target_image_reference": (
            "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
        ),
    }
    protocol: dict[str, object] = {"study_id": "profile-oracle"}
    url = "https://validation-study.example/object"
    profiles = vs_audit_science.load_frozen_profiles(
        ROOT,
        environment=environment,
        protocol=protocol,
        url=url,
    )
    assert tuple(profiles) == ("short", "streaming", "bursty")
    frozen = profiles["short"]
    document = frozen.model_dump(mode="python")
    models = cast(dict[str, object], document["models"])
    models["enabled"] = ["poisson_empirical", "markov_renewal"]
    del models["mmpp"]
    subset = ExperimentConfig.model_validate(document)

    def invalid_profile(**_kwargs: object) -> ExperimentConfig:
        return subset

    monkeypatch.setattr(vs_audit_science, "_validation_profile", invalid_profile)
    with pytest.raises(vs_audit_common.Issue) as error:
        vs_audit_science.load_frozen_profiles(
            ROOT,
            environment=environment,
            protocol=protocol,
            url=url,
        )

    assert error.value.kind == "scientific_semantics_incompatible"


def test_offline_auditor_requires_study_bound_collection_lifecycle(tmp_path: Path) -> None:
    """A complete candidate is not auditable without retained collection cleanup proof."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    (candidate / "lifecycle.json").unlink(missing_ok=True)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_missing", "publication", "lifecycle.json", "not_published", "primary")


def test_offline_auditor_accepts_complete_study_bound_collection_lifecycle(tmp_path: Path) -> None:
    """The public auditor reconstructs cleanup proof from retained collection bytes."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    lifecycle = write_candidate_lifecycle(candidate)

    assert vs_audit_lifecycle.audit_bundle(candidate, repository=repository).bundle == candidate
    assert lifecycle["phase_capture_image"] == {
        "capture_image_id": CAPTURE_IMAGE_ID,
        "cleanup_verified": True,
        "post_cleanup_inspect_exit_status": 1,
        "tag": "trafficlab-validation-fixture-study:collection-capture",
    }


def test_collection_lifecycle_guards_reject_missing_owner_and_malformed_rows(tmp_path: Path) -> None:
    """Lifecycle finalization and reconstruction reject incomplete ownership proof."""

    with pytest.raises(ValueError, match="collection finalization requires its owned capture image"):
        vs_collection._finalize_collection_lifecycle(  # pyright: ignore[reportPrivateUsage]
            candidate=tmp_path / "candidate",
            environment={},
            held_out=(),
            owned_capture_image=None,
            repository_root=tmp_path,
            runner=cast(vs_records.CommandRunner, object()),
            study_id="study-1",
            training=(),
        )

    with pytest.raises(vs_audit_common.Issue) as project_name:
        vs_audit_lifecycle._lifecycle_project_name(  # pyright: ignore[reportPrivateUsage]
            b'{"event":"capture_published","project_name":"foreign-project"}\n',
            name="training/short/r1/run.log",
        )
    assert project_name.value.kind == "artifact_foreign"

    with pytest.raises(vs_audit_common.Issue) as rows:
        vs_audit_lifecycle._lifecycle_rows({}, expected=(), name="training")  # pyright: ignore[reportPrivateUsage]
    assert rows.value.kind == "artifact_corrupt"


@pytest.mark.parametrize(
    "mutation",
    ("study_id", "phase_cleanup", "training_cleanup", "held_out_cleanup", "duplicate_training", "wrong_run"),
)
def test_offline_auditor_rejects_collection_lifecycle_identity_and_cleanup_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Lifecycle booleans and run bindings are independently required before publication."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    lifecycle = write_candidate_lifecycle(candidate)
    if mutation == "study_id":
        lifecycle["study_id"] = "other-study"
    elif mutation == "phase_cleanup":
        cast(dict[str, object], lifecycle["phase_capture_image"])["cleanup_verified"] = False
    elif mutation == "training_cleanup":
        cast(list[dict[str, object]], lifecycle["training"])[0]["cleanup_verified"] = False
    elif mutation == "held_out_cleanup":
        cast(list[dict[str, object]], lifecycle["held_out"])[0]["cleanup_verified"] = False
    elif mutation == "duplicate_training":
        rows = cast(list[dict[str, object]], lifecycle["training"])
        rows[1] = dict(rows[0])
    else:
        cast(list[dict[str, object]], lifecycle["held_out"])[0]["run_id"] = "held-out-other"
    write_canonical_json(candidate / "lifecycle.json", lifecycle)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "lifecycle.json", "not_published", "primary")


@pytest.mark.parametrize("content", (b"not-json\n", b'{"study_id":"fixture-study"}\n'))
def test_offline_auditor_rejects_malformed_collection_lifecycle(tmp_path: Path, content: bytes) -> None:
    """Lifecycle proof uses one closed, canonical schema rather than a producer extension bag."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    write_candidate_lifecycle(candidate)
    (candidate / "lifecycle.json").write_bytes(content)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_corrupt", "publication", "lifecycle.json", "not_published", "primary")


def test_offline_auditor_rejects_a_wrong_collection_lifecycle_schema_version(tmp_path: Path) -> None:
    """A closed lifecycle schema cannot silently accept a future producer shape."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    lifecycle = write_candidate_lifecycle(candidate)
    lifecycle["schema_version"] = 2
    write_canonical_json(candidate / "lifecycle.json", lifecycle)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_corrupt", "publication", "lifecycle.json", "not_published", "primary")


def test_offline_auditor_rejects_duplicate_study_keys_before_pydantic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON decoder, not Pydantic's last-key-wins parser, owns duplicate-key rejection."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    environment_path = candidate / "environment.json"
    content = environment_path.read_bytes()
    environment_path.write_bytes(
        content.replace(b'"source_commit":', b'"source_commit":"duplicate","source_commit":', 1)
    )
    rewrite_candidate_manifest(candidate)

    def unexpected_validation(_cls: type[object], _value: object) -> object:
        raise AssertionError("Pydantic must not observe duplicate-key JSON")

    monkeypatch.setattr(
        trafficlab_study_evidence_protocol.ValidationStudyEnvironment,
        "model_validate",
        classmethod(unexpected_validation),
    )

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.affected_evidence == "environment.json"
    assert "duplicate JSON key" in outcome.detail


@pytest.mark.parametrize(
    ("root_name", "expected_affected"),
    (
        ("index", "index.json"),
        ("manifest", "manifest.json"),
        ("report_inputs", "report_inputs.json"),
        ("report", "report.json"),
    ),
)
def test_offline_auditor_validates_local_root_before_cross_record_mismatch(
    tmp_path: Path,
    root_name: str,
    expected_affected: str,
) -> None:
    """Malformed local roots are corruption even when the same mutation also breaks a linked claim."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    if root_name == "index":
        index = candidate_index(candidate)
        cast(list[dict[str, object]], index["training"])[0]["repeat"] = True
        cast(dict[str, object], index["ownership"])["environment.json"] = "foreign-owner"
        write_candidate_index(candidate, index)
        rewrite_candidate_manifest(candidate)
    elif root_name == "manifest":
        manifest_path = candidate / "manifest.json"
        manifest = cast(dict[str, object], json.loads(manifest_path.read_bytes()))
        cast(list[dict[str, object]], manifest["files"])[0]["lineage"] = "malformed-lineage"
        write_canonical_json(manifest_path, manifest)
    elif root_name == "report_inputs":
        report_inputs_path = candidate / "report_inputs.json"
        report_inputs = cast(dict[str, object], json.loads(report_inputs_path.read_bytes()))
        report_inputs["formula"] = "not-arithmetic"
        write_canonical_json(report_inputs_path, report_inputs)
        rewrite_candidate_manifest(candidate)
    else:
        report_path = candidate / "report.json"
        report = cast(dict[str, object], json.loads(report_path.read_bytes()))
        del report["formula"]
        write_canonical_json(report_path, report)
        rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_corrupt", "publication", expected_affected, "not_published", "primary")


def test_offline_auditor_preserves_manifest_schema_precedence(tmp_path: Path) -> None:
    """Manifest version incompatibility is classified before local Pydantic decoding."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    manifest_path = candidate / "manifest.json"
    manifest = cast(dict[str, object], json.loads(manifest_path.read_bytes()))
    manifest["schema_version"] = 1
    write_canonical_json(manifest_path, manifest)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == ("artifact_corrupt", "manifest.json")


def test_offline_auditor_recomputes_report_after_local_validation(tmp_path: Path) -> None:
    """A locally valid report with a foreign report-input identity remains a cross-record mismatch."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    report_path = candidate / "report.json"
    report = cast(dict[str, object], json.loads(report_path.read_bytes()))
    cast(dict[str, object], report["report_inputs_identity"])["sha256"] = "0" * 64
    write_canonical_json(report_path, report)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == ("artifact_foreign", "report.json")


@pytest.mark.parametrize(
    "mutation",
    ("schema_version", "phase_cleanup", "phase_inspect", "training_cleanup", "held_out_cleanup"),
)
def test_offline_auditor_rejects_collection_lifecycle_scalar_type_spoofs(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Cleanup facts use closed JSON scalar types before their exact value binding."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    lifecycle = write_candidate_lifecycle(candidate)
    if mutation == "schema_version":
        lifecycle["schema_version"] = True
    elif mutation == "phase_cleanup":
        cast(dict[str, object], lifecycle["phase_capture_image"])["cleanup_verified"] = 1
    elif mutation == "phase_inspect":
        cast(dict[str, object], lifecycle["phase_capture_image"])["post_cleanup_inspect_exit_status"] = True
    elif mutation == "training_cleanup":
        cast(list[dict[str, object]], lifecycle["training"])[0]["cleanup_verified"] = 1
    else:
        cast(list[dict[str, object]], lifecycle["held_out"])[0]["cleanup_verified"] = 1
    write_canonical_json(candidate / "lifecycle.json", lifecycle)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_corrupt", "publication", "lifecycle.json", "not_published", "primary")


def test_offline_auditor_rejects_a_noninteger_collection_index_schema_version(tmp_path: Path) -> None:
    """The schema-4 index version is an integer fact rather than an equality alias."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    write_candidate_lifecycle(candidate)
    index = candidate_index(candidate)
    index["schema_version"] = 4.0
    write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_corrupt", "publication", "index.json", "not_published", "primary")


@pytest.mark.parametrize(
    ("mutation", "affected_evidence"),
    (
        ("missing_creation", "training/short/r1/run.log"),
        ("mismatched_creation", "training/short/r1/run.log"),
        ("late_creation", "training/short/r1/run.log"),
        ("duplicate_project", "lifecycle.json"),
    ),
)
def test_offline_auditor_binds_collection_projects_to_unique_created_projects(
    tmp_path: Path,
    mutation: str,
    affected_evidence: str,
) -> None:
    """Lifecycle cleanup claims bind the created Compose project for every capture."""

    repository, candidate = copy_validation_study_candidate(tmp_path)

    def load(relative: str) -> list[dict[str, object]]:
        return [cast(dict[str, object], json.loads(line)) for line in (candidate / relative).read_bytes().splitlines()]

    def write(relative: str, records: list[dict[str, object]]) -> None:
        (candidate / relative).write_bytes(
            b"".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
                for record in records
            )
        )

    def record(records: list[dict[str, object]], event: str) -> dict[str, object]:
        return next(item for item in records if item["event"] == event)

    def created(records: list[dict[str, object]]) -> dict[str, object]:
        published = record(records, "capture_published")
        try:
            return record(records, "capture_project_created")
        except StopIteration:
            item = {
                "event": "capture_project_created",
                "project_name": published["project_name"],
                "stage": "capture",
            }
            records.insert(records.index(published), item)
            return item

    first_relative = "training/short/r1/run.log"
    first = load(first_relative)
    if mutation == "missing_creation":
        first[:] = [item for item in first if item["event"] != "capture_project_created"]
        write(first_relative, first)
    elif mutation == "mismatched_creation":
        created(first)["project_name"] = "trafficlab-capture-mismatched"
        write(first_relative, first)
    elif mutation == "late_creation":
        creation = created(first)
        first.remove(creation)
        first.insert(first.index(record(first, "capture_published")) + 1, creation)
        write(first_relative, first)
    else:
        second_relative = "training/short/r2/run.log"
        second = load(second_relative)
        project_name = record(first, "capture_published")["project_name"]
        created(first)["project_name"] = project_name
        record(second, "capture_published")["project_name"] = project_name
        created(second)["project_name"] = project_name
        write(first_relative, first)
        write(second_relative, second)
    write_candidate_lifecycle(candidate)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", affected_evidence, "not_published", "primary")


def test_offline_auditor_rejects_a_fixture_profile_with_a_foreign_url(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """The narrow fixture compatibility path cannot become a generic profile bypass."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_bytes()))
    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_bytes()))

    with pytest.raises(vs_audit_common.Issue) as error:
        vs_audit_science.load_frozen_profiles(
            repository,
            environment=environment,
            protocol=protocol,
            url="https://invalid.example/",
        )

    assert error.value.kind == "artifact_foreign"


@pytest.mark.parametrize(
    ("stdout", "expected"),
    (
        (bytes((255, 0)), "path is not UTF-8"),
        (b"foreign\0foreign\0", "paths must be unique"),
        (b"elsewhere\0", "paths do not match the inspected worktree"),
    ),
)
def test_prerequisite_ignored_path_parser_rejects_invalid_match_records(
    tmp_path: Path,
    stdout: bytes,
    expected: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert argv == ("git", "check-ignore", "-z", "--stdin")
        assert input == b"foreign\0"
        assert cwd == repository
        assert check is False
        assert capture_output is True
        assert shell is False
        assert timeout == vs_common.SUBPROCESS_TIMEOUTS["git_or_version"]
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=stdout, stderr=b"")

    assert vs_prereq_commands._ignored_prerequisite_worktree_paths(repository, (), runner=runner) == frozenset()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match=expected):
        vs_prereq_commands._ignored_prerequisite_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository, ("foreign",), runner=runner
        )


def test_prerequisite_cleanliness_continues_past_permitted_ignored_special_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runner = ScriptedPrerequisiteRunner(repository)

    def entries(_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (("first", "second"), ("first", "second"))

    def ignored_paths(_root: Path, _paths: Sequence[str], *, runner: Any) -> frozenset[str]:
        return frozenset({"first"})

    def permitted_path(path: str) -> bool:
        return path == "first"

    monkeypatch.setattr(vs_prereq_commands, "_prerequisite_worktree_entries", entries)
    monkeypatch.setattr(vs_prereq_commands, "_ignored_prerequisite_worktree_paths", ignored_paths)
    monkeypatch.setattr(vs_prereq_commands, "_permitted_ignored_prerequisite_worktree_path", permitted_path)

    with pytest.raises(ValueError, match="non-regular entry: second"):
        vs_prereq_commands.require_clean_prerequisite_worktree(repository, runner=runner)
