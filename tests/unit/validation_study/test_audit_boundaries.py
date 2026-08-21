from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from statistics import fmean
from typing import Any, Literal, cast

import pytest

from scripts import audit_validation_study as auditor
from scripts import run_validation_study as study
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study import (
    CAPTURE_BYTES,
    CAPTURE_IMAGE_ID,
    FIT_FIXTURE,
    ROOT,
    ScriptedPrerequisiteRunner,
    candidate_index,
    copy_validation_study_candidate,
    rewrite_candidate_manifest,
    validation_study_fixture_identity,
    write_candidate_index,
    write_canonical_json,
)
from trafficlab import USER_AGENT
from trafficlab.comparison import ComparisonResult, compare_traces
from trafficlab.compatibility import identify_bytes
from trafficlab.config import ExperimentConfig, SimilarityConfig
from trafficlab.config_io import load_configuration_pair
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.checkpoint import CheckpointState
from trafficlab.trace import Direction, TraceEvent, align_generated, normalize_reference, parse_capture_metadata


def write_candidate_lifecycle(candidate: Path) -> dict[str, object]:
    """Add the complete collection-cleanup contract without calling the producer."""

    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_bytes()))
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_bytes()))
    study_id = cast(str, protocol["study_id"])

    def row(relative: str, run_id: str) -> dict[str, object]:
        records = [json.loads(line) for line in (candidate / relative / "run.log").read_bytes().splitlines()]
        capture = next(record for record in records if record["event"] == "capture_published")
        project_name = capture["project_name"]
        assert isinstance(project_name, str)
        return {
            "cleanup_verified": True,
            "directory": relative,
            "project_name": project_name,
            "run_id": run_id,
        }

    lifecycle: dict[str, object] = {
        "held_out": [
            row(f"held_out/{workload}", f"held-out-{workload}") for workload in ("short", "streaming", "bursty")
        ],
        "phase_capture_image": {
            "capture_image_id": environment["capture_image_id"],
            "cleanup_verified": True,
            "post_cleanup_inspect_exit_status": 1,
            "tag": f"trafficlab-validation-{study_id}:collection-capture",
        },
        "schema_version": 1,
        "study_id": study_id,
        "training": [
            row(f"training/{workload}/r{repeat}", run_id) for _order, run_id, workload, repeat in study.PRIMARY_ORDER
        ],
    }
    write_canonical_json(candidate / "lifecycle.json", lifecycle)
    index = candidate_index(candidate)
    ownership = cast(dict[str, str], index["ownership"])
    lineage = cast(dict[str, object], index["lineage"])
    ownership["lifecycle.json"] = "study-lifecycle"
    lineage["lifecycle.json"] = {"relation": "lifecycle"}
    index["lifecycle"] = "lifecycle.json"
    index["ownership"] = ownership
    index["lineage"] = lineage
    index["schema_version"] = 4
    write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)
    return lifecycle


def rewrite_training_configuration_identities(candidate: Path, *, workload: str, repeat: int) -> None:
    """Keep an intentional configuration mutation internally self-consistent."""

    index = candidate_index(candidate)
    record = next(
        item
        for item in cast(list[dict[str, object]], index["training"])
        if item["workload"] == workload and item["repeat"] == repeat
    )
    directory = f"training/{workload}/r{repeat}"
    for field, relative in (
        ("portable_config_identity", f"configs/training-{workload}-r{repeat}.portable.toml"),
        ("realized_config_identity", f"configs/training-{workload}-r{repeat}.realized.toml"),
        ("run_config_identity", f"{directory}/experiment.toml"),
    ):
        record[field] = identify_bytes((candidate / relative).read_bytes()).as_dict()
    checkpoint_path = candidate / directory / "checkpoint.json"
    checkpoint = cast(dict[str, object], json.loads(checkpoint_path.read_bytes()))
    checkpoint["experiment_identity"] = identify_bytes(
        (candidate / directory / "experiment.toml").read_bytes()
    ).as_dict()
    write_canonical_json(checkpoint_path, checkpoint)
    write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)


def test_cold_capture_build_argv_freezes_task9_reproducibility_controls(tmp_path: Path) -> None:
    """Study prerequisites use the same cold locked capture-build contract as the Docker owner."""

    assert study.cold_capture_build_argv(
        "trafficlab-validation-study-1:capture",
        tmp_path / "capture.iid",
    ) == (
        "docker",
        "build",
        "--pull",
        "--no-cache",
        "--provenance=false",
        "--platform",
        "linux/amd64",
        "--output",
        "type=image,rewrite-timestamp=true,unpack=false",
        "--tag",
        "trafficlab-validation-study-1:capture",
        "--iidfile",
        str(tmp_path / "capture.iid"),
        "docker/capture",
    )


@pytest.mark.parametrize(
    ("tag", "iidfile", "error", "message"),
    (
        (object(), Path("capture.iid"), ValueError, "capture image tag must be a nonempty string"),
        ("trafficlab-validation-study-1:capture", object(), TypeError, "iidfile must be a pathlib.Path"),
    ),
)
def test_cold_capture_build_argv_rejects_invalid_boundary_types(
    tag: object,
    iidfile: object,
    error: type[Exception],
    message: str,
) -> None:
    """The public cold-build boundary retains deterministic runtime validation."""

    with pytest.raises(error, match=message):
        study.cold_capture_build_argv(tag, iidfile)  # pyright: ignore[reportPrivateUsage]


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
    document = study.parse_retained_prerequisites(prerequisite_path.read_bytes())
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
        replace_output("command", b'{"argv":[]}\n')
    elif case == "status":
        replace_output(
            "status",
            b'{"exit_status":0,"tests":{"errors":0,"failed":0,"passed":999,"skipped":0,"total":999}}\n',
        )
    elif case == "utf8":
        replace_output("stdout", b"\xff")
    elif case == "junit_invalid":
        replace_output("junit", b"<unexpected/>")
    else:
        replace_output("junit", b'<testsuite tests="2" failures="0" errors="0" skipped="0"/>')
    if render_document:
        prerequisite_path.write_bytes(study.render_retained_prerequisites(document))
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
    target_reference = study.TARGET_REFERENCE.encode("ascii")
    foreign_reference = b"curlimages/curl@sha256:" + b"1" * 64
    for name in ("configs/training-short-r1.portable.toml", "configs/training-short-r1.realized.toml"):
        path = candidate / name
        content = path.read_bytes()
        assert target_reference in content
        path.write_bytes(content.replace(target_reference, foreign_reference))
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
    frozen = auditor._frozen_profiles(  # pyright: ignore[reportPrivateUsage]
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

    with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
        auditor._require_frozen_profile(  # pyright: ignore[reportPrivateUsage]
            subset,
            frozen,
            affected="held_out/short",
        )

    assert error.value.kind == "artifact_foreign"


def test_offline_auditor_rejects_one_generation_against_the_unpatched_frozen_profile() -> None:
    """The fast collection fixture must not relax the production two-generation oracle."""

    environment: dict[str, object] = {
        "capture_image_reference": "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c",
        "target_image_reference": (
            "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
        ),
    }
    frozen = auditor._validation_profile(  # pyright: ignore[reportPrivateUsage]
        workload="short",
        url="https://validation-study.example/object",
        environment=environment,
    )
    one_generation = frozen.model_copy(update={"genetic": frozen.genetic.model_copy(update={"generation_count": 1})})

    with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
        auditor._require_frozen_profile(  # pyright: ignore[reportPrivateUsage]
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
        "capture_image_reference": "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c",
        "target_image_reference": (
            "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
        ),
    }
    producer_workload_specs = study.workload_specs

    def legacy_workload_specs(url: str) -> tuple[study.WorkloadSpec, ...]:
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

    monkeypatch.setattr(study, "workload_specs", legacy_workload_specs)
    try:
        importlib.reload(auditor)
        assert {
            (binding.requested_start, binding.requested_end, binding.filename)
            for binding in auditor._TRANSFER_BINDINGS  # pyright: ignore[reportPrivateUsage]
            if binding.workload == "short"
        } == {(0, 1_048_575, "short.headers")}
        frozen = auditor._validation_profile(  # pyright: ignore[reportPrivateUsage]
            workload="short",
            url="https://validation-study.example/object",
            environment=environment,
        )
        source_commit, _source_tree = validation_study_fixture_identity()
        fixture = auditor._fixture_profile(  # pyright: ignore[reportPrivateUsage]
            ROOT,
            source_commit=source_commit,
            workload="short",
            url=auditor._FIXTURE_URL,  # pyright: ignore[reportPrivateUsage]
            environment=environment,
        )
        assert "0-1048575" in frozen.target.argv
        assert "1048576" in frozen.target.argv
        assert fixture.target.argv == frozen.target.argv[:-1] + (auditor._FIXTURE_URL,)  # pyright: ignore[reportPrivateUsage]
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

        with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
            auditor._require_frozen_profile(  # pyright: ignore[reportPrivateUsage]
                legacy,
                frozen,
                affected="held_out/short",
            )
        assert error.value.kind == "artifact_foreign"

        with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
            auditor._require_config_workload_argv(  # pyright: ignore[reportPrivateUsage]
                legacy,
                workload="short",
                url="https://validation-study.example/object",
                affected="held_out/short/experiment.toml",
            )
        assert error.value.kind == "artifact_foreign"
    finally:
        monkeypatch.undo()
        importlib.reload(auditor)


def test_offline_auditor_reconstructs_nonfixture_profiles_and_rejects_a_missing_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-fixture profile oracle is independent of candidate-generated bytes."""

    environment: dict[str, object] = {
        "capture_image_reference": "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c",
        "source_commit": "3a3c401c9e4a55115a66c879d719180c6d1ddffc",
        "target_image_reference": (
            "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
        ),
    }
    protocol: dict[str, object] = {"study_id": "profile-oracle"}
    url = "https://validation-study.example/object"
    profiles = auditor._frozen_profiles(  # pyright: ignore[reportPrivateUsage]
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

    monkeypatch.setattr(auditor, "_validation_profile", invalid_profile)
    with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
        auditor._frozen_profiles(  # pyright: ignore[reportPrivateUsage]
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
        auditor.audit_bundle(candidate, repository=repository)

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

    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate
    assert lifecycle["phase_capture_image"] == {
        "capture_image_id": CAPTURE_IMAGE_ID,
        "cleanup_verified": True,
        "post_cleanup_inspect_exit_status": 1,
        "tag": "trafficlab-validation-fixture-study:collection-capture",
    }


def test_collection_lifecycle_guards_reject_missing_owner_and_malformed_rows(tmp_path: Path) -> None:
    """Lifecycle finalization and reconstruction reject incomplete ownership proof."""

    with pytest.raises(ValueError, match="collection finalization requires its owned capture image"):
        study._finalize_collection_lifecycle(  # pyright: ignore[reportPrivateUsage]
            candidate=tmp_path / "candidate",
            environment={},
            held_out=(),
            owned_capture_image=None,
            repository_root=tmp_path,
            runner=cast(study.CommandRunner, object()),
            study_id="study-1",
            training=(),
        )

    with pytest.raises(auditor._Issue) as project_name:  # pyright: ignore[reportPrivateUsage]
        auditor._lifecycle_project_name(  # pyright: ignore[reportPrivateUsage]
            b'{"event":"capture_published","project_name":"foreign-project"}\n',
            name="training/short/r1/run.log",
        )
    assert project_name.value.kind == "artifact_foreign"

    with pytest.raises(auditor._Issue) as rows:  # pyright: ignore[reportPrivateUsage]
        auditor._lifecycle_rows({}, expected=(), name="training")  # pyright: ignore[reportPrivateUsage]
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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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

    monkeypatch.setattr(auditor.ValidationStudyEnvironment, "model_validate", classmethod(unexpected_validation))

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

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

    with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
        auditor._frozen_profiles(  # pyright: ignore[reportPrivateUsage]
            repository,
            environment=environment,
            protocol=protocol,
            url="https://invalid.example/",
        )

    assert error.value.kind == "artifact_foreign"


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (("different", "artifact_foreign"), ("invalid", "artifact_corrupt"), ("noncanonical", "artifact_foreign")),
)
def test_offline_auditor_rejects_untrusted_fixture_profile_source_bytes(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    """Fixture compatibility derives its profile from checked source bytes, never candidate bytes."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_bytes()))
    source = repository / "examples/data/fit/experiment.toml"
    original = source.read_bytes()
    if case == "different":
        source.write_bytes(b"different\n")
    else:
        replacement = b"invalid = [\n" if case == "invalid" else b"# retained comment\n" + original
        source.write_bytes(replacement)

        def recorded_fixture_profile(*_args: object, **_kwargs: object) -> bytes:
            return replacement

        monkeypatch.setattr(auditor, "_git_bytes", recorded_fixture_profile)

    with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
        auditor._fixture_profile(  # pyright: ignore[reportPrivateUsage]
            repository,
            source_commit=cast(str, environment["source_commit"]),
            workload="short",
            url="https://downloads.example.test/object.bin",
            environment=environment,
        )

    assert error.value.kind == expected_kind


@pytest.mark.parametrize(
    ("relative", "record"),
    (
        ("training/short/r1/run.log", b'{"event":"capture_published","stage":"capture","workload":"short"}\n'),
        ("held_out/short/run.log", b'{"event":"held_out_evaluated","stage":"compare","workload":"short"}\n'),
    ),
)
def test_offline_auditor_rejects_contradictory_required_run_log_records(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    relative: str,
    record: bytes,
) -> None:
    """Run logs are retained lineage, not merely syntax-valid diagnostics."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    with (candidate / relative).open("ab") as stream:
        stream.write(record)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", relative, "not_published", "primary")


@pytest.mark.parametrize(
    ("relative", "field"),
    (("training/short/r1/run.log", "reused"), ("held_out/short/run.log", "capture_identity")),
)
def test_offline_auditor_rejects_required_run_log_identity_and_status_mismatches(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    relative: str,
    field: str,
) -> None:
    """Required run-log records bind both capture identity and completion status."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    path = candidate / relative
    records = [cast(dict[str, object], json.loads(line)) for line in path.read_bytes().splitlines()]
    capture = next(record for record in records if record["event"] == "capture_published")
    if field == "reused":
        capture["reused"] = True
    else:
        identity = cast(dict[str, object], capture["capture_identity"])
        identity["sha256"] = "0" * 64
    path.write_bytes(
        b"".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            + b"\n"
            for record in records
        )
    )
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", relative, "not_published", "primary")


def test_offline_auditor_accepts_fixture_canonical_capture_platform_log_lineage() -> None:
    """The deterministic fixture records the frozen platform and successful lineage sequences."""

    bundle = VALIDATION_STUDY_CANDIDATE
    environment = cast(dict[str, object], json.loads((bundle / "environment.json").read_bytes()))
    sequences = (
        (
            "training/short/r1/run.log",
            (
                "capture_environment_identity",
                "capture_published",
                "best_model_published",
                "generated_pcapng_published",
                "comparison_succeeded",
                "run_completed",
                "validation_study_training_completed",
            ),
            ("run_completed", "validation_study_training_completed"),
        ),
        (
            "held_out/short/run.log",
            ("capture_environment_identity", "capture_published", "held_out_evaluated"),
            ("held_out_evaluated",),
        ),
    )
    for relative, events, terminal in sequences:
        records = auditor._run_log_records(  # pyright: ignore[reportPrivateUsage]
            (bundle / relative).read_bytes(),
            name=relative,
        )
        auditor._require_successful_log_status(records, name=relative)  # pyright: ignore[reportPrivateUsage]
        auditor._require_terminal_log_events(records, events=terminal, name=relative)  # pyright: ignore[reportPrivateUsage]
        auditor._require_ordered_log_events(records, events=events, name=relative)  # pyright: ignore[reportPrivateUsage]

    directory = bundle / "training" / "short" / "r1"
    records = auditor._run_log_records(  # pyright: ignore[reportPrivateUsage]
        (directory / "run.log").read_bytes(), name="training/short/r1/run.log"
    )

    auditor._require_capture_log_lineage(  # pyright: ignore[reportPrivateUsage]
        records,
        name="training/short/r1/run.log",
        environment=environment,
        capture=(directory / "capture.json").read_bytes(),
        reference=(directory / "reference.pcapng").read_bytes(),
        experiment=(directory / "experiment.toml").read_bytes(),
        packet_count=None,
    )


def test_offline_auditor_rejects_each_contradictory_run_log_lineage_mutation(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Every contradictory successful-run claim independently fails the public audit."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    cases = (
        ("capture_reused", "training/short/r1/run.log"),
        ("run_failed", "held_out/short/run.log"),
        ("reused_field", "training/short/r1/run.log"),
        ("training_after_terminal", "training/short/r1/run.log"),
        ("held_out_after_terminal", "held_out/short/run.log"),
        ("training_order", "training/short/r1/run.log"),
        ("held_out_order", "held_out/short/run.log"),
    )
    for case, relative in cases:
        path = candidate / relative
        original = path.read_bytes()
        records = [json.loads(line) for line in original.splitlines()]
        if case == "capture_reused":
            records.append({"event": "capture_reused", "stage": "capture"})
        elif case == "run_failed":
            records.append({"event": "run_failed", "stage": "run"})
        elif case == "reused_field":
            next(record for record in records if record["event"] == "best_model_published")["reused"] = True
        elif case.endswith("after_terminal"):
            records.append({"event": "diagnostic", "stage": "run"})
        else:
            first_event, second_event = (
                ("capture_published", "best_model_published")
                if case == "training_order"
                else ("capture_environment_identity", "capture_published")
            )
            first = next(index for index, record in enumerate(records) if record["event"] == first_event)
            second = next(index for index, record in enumerate(records) if record["event"] == second_event)
            records[first], records[second] = records[second], records[first]
        path.write_bytes(
            b"".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                    "utf-8"
                )
                + b"\n"
                for record in records
            )
        )
        rewrite_candidate_manifest(candidate)
        try:
            with pytest.raises(TrafficlabError) as error:
                auditor.audit_bundle(candidate, repository=repository)
            outcome = error.value.failure_outcome
            assert outcome is not None
            assert (outcome.kind, outcome.affected_evidence) == ("artifact_foreign", relative)
        finally:
            path.write_bytes(original)
            rewrite_candidate_manifest(candidate)


def test_offline_auditor_reports_a_missing_ordered_run_log_event() -> None:
    """The ordering guard keeps its canonical error if a caller omits a required stage."""

    with pytest.raises(auditor._Issue) as error:  # pyright: ignore[reportPrivateUsage]
        auditor._require_ordered_log_events(  # pyright: ignore[reportPrivateUsage]
            ({"event": "capture_environment_identity"},),
            events=("capture_environment_identity", "capture_published"),
            name="training/short/r1/run.log",
        )

    assert error.value.kind == "artifact_foreign"
    assert error.value.affected == "training/short/r1/run.log"


def test_offline_auditor_rejects_incomplete_capture_log_records() -> None:
    """Canonical JSON alone is insufficient when retained capture-lineage fields are absent."""

    environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_bytes()),
    )
    capture = b"capture"
    reference = b"reference"
    experiment = b"experiment"
    environment_fields = auditor._capture_log_environment(environment)  # pyright: ignore[reportPrivateUsage]
    records = (
        {"event": "capture_environment_identity", "stage": "preflight", **environment_fields},
        {
            "capture_identity": identify_bytes(capture).as_dict(),
            "event": "capture_published",
            "experiment_identity": identify_bytes(experiment).as_dict(),
            "reference_identity": identify_bytes(reference).as_dict(),
            "reused": False,
            "stage": "capture",
        },
    )
    with pytest.raises(auditor._Issue) as incomplete:  # pyright: ignore[reportPrivateUsage]
        auditor._require_capture_log_lineage(  # pyright: ignore[reportPrivateUsage]
            records,
            name="run.log",
            environment=environment,
            capture=capture,
            reference=reference,
            experiment=experiment,
            packet_count=None,
        )
    assert incomplete.value.kind == "artifact_foreign"


@pytest.mark.parametrize(
    ("section", "expected_directory"),
    (("training", "training/short/r1"), ("held_out", "held_out/short")),
)
def test_offline_auditor_rejects_capture_lineage_that_disagrees_with_retained_bytes(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    section: str,
    expected_directory: str,
) -> None:
    """Training and held-out capture provenance cannot be substituted after capture validation."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    index = candidate_index(candidate)
    record = cast(list[dict[str, object]], index[section])[0]
    cast(dict[str, object], record["capture_lineage"])["capture_tool_version"] = "tampered"
    write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", expected_directory, "not_published", "primary")


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("rule", "scientific_semantics_incompatible"),
        ("count", "artifact_corrupt"),
        ("duplicate", "artifact_foreign"),
        ("mismatch", "artifact_foreign"),
        ("order", "artifact_foreign"),
    ),
)
def test_offline_auditor_reconstructs_all_training_model_selection_rejections(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    case: str,
    expected_kind: str,
) -> None:
    """The protocol's retained training-only selection is recomputed before held-out evaluation."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    protocol_path = candidate / "protocol.json"
    protocol = cast(dict[str, object], json.loads(protocol_path.read_text(encoding="utf-8")))
    selection = cast(dict[str, object], protocol["model_selection"])
    selected = cast(list[dict[str, object]], selection["selected"])
    if case == "rule":
        selection["rule"] = "first_training_record"
    elif case == "count":
        selection["selected"] = []
    elif case == "duplicate":
        selected[1] = copy.deepcopy(selected[0])
    elif case == "mismatch":
        selected_repeat = cast(int, selected[0]["repeat"])
        selected[0]["repeat"] = 1 if selected_repeat != 1 else 2
    else:
        selection["selected"] = list(reversed(selected))
    write_canonical_json(protocol_path, protocol)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (expected_kind, "publication", "protocol", "not_published", "primary")


def test_candidate_natural_variation_derives_each_directional_reference_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Natural variation compares repeated captures at each reference-derived W, not a metric bin width."""
    base_config = load_configuration_pair(FIT_FIXTURE / "experiment.toml").realized
    config = base_config.model_copy(
        update={
            "similarity": base_config.similarity.model_copy(
                update={"max_direction_bin_cells": 2_000, "multiscale_widths_seconds": (0.001, 0.01)}
            )
        }
    )
    frozen_bin_width = max(config.similarity.multiscale_widths_seconds)
    assert frozen_bin_width == 0.01

    def trace(spacing: float) -> tuple[TraceEvent, ...]:
        return tuple(
            TraceEvent(
                timestamp=index * spacing,
                direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                frame_length=128 + index,
            )
            for index in range(20)
        )

    metadata = parse_capture_metadata(CAPTURE_BYTES, source=FIT_FIXTURE / "capture.json")

    configurations = tuple(config.model_copy(deep=True) for _ in range(3))
    assert configurations[0].similarity is not configurations[1].similarity

    def training(
        repeat: int,
        raw_reference: tuple[TraceEvent, ...],
        configuration: ExperimentConfig,
    ) -> study._CandidateTraining:  # pyright: ignore[reportPrivateUsage]
        reference, window = normalize_reference(raw_reference)
        return study._CandidateTraining(  # pyright: ignore[reportPrivateUsage]
            workload="short",
            repeat=repeat,
            directory=tmp_path / f"r{repeat}",
            config=configuration,
            contents={},
            metadata=metadata,
            reference=reference,
            observation_window_seconds=window,
            runtime_seconds=0.0,
            checkpoint=cast(CheckpointState, object()),
            comparison=cast(ComparisonResult, object()),
        )

    records = tuple(
        training(repeat, raw_reference, configurations[repeat - 1])
        for repeat, raw_reference in enumerate((trace(0.005), trace(0.03), trace(0.025)), start=1)
    )
    with pytest.raises(TrafficlabError, match="invalid generated trace: at least two events"):
        compare_traces(
            align_generated(records[0].reference, frozen_bin_width),
            align_generated(records[1].reference, frozen_bin_width),
            frozen_bin_width,
            config.similarity,
        )

    first_reference, forward_window = normalize_reference(records[0].reference)
    second_reference, reverse_window = normalize_reference(records[1].reference)
    forward = compare_traces(
        first_reference,
        align_generated(records[1].reference, forward_window),
        forward_window,
        config.similarity,
    )
    reverse = compare_traces(
        second_reference,
        align_generated(records[0].reference, reverse_window),
        reverse_window,
        config.similarity,
    )

    settings_calls: list[SimilarityConfig] = []
    original_compare = study.compare_traces

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        settings_calls.append(settings)
        return original_compare(reference, generated, window, settings)

    monkeypatch.setattr(study, "compare_traces", comparison_spy)
    result = study._candidate_natural_variation(records)  # pyright: ignore[reportPrivateUsage]
    assert settings_calls[:2] == [records[0].config.similarity, records[1].config.similarity]
    assert settings_calls[0] is records[0].config.similarity
    assert settings_calls[1] is records[1].config.similarity
    first_pair = cast(dict[str, object], cast(list[object], result["pairs"])[0])
    forward_score = cast(dict[str, object], first_pair["forward"])
    reverse_score = cast(dict[str, object], first_pair["reverse"])
    symmetric = cast(dict[str, object], first_pair["symmetric_mean"])
    assert forward_score == study._candidate_score(forward)  # pyright: ignore[reportPrivateUsage]
    assert reverse_score == study._candidate_score(reverse)  # pyright: ignore[reportPrivateUsage]
    assert symmetric["aggregate"] == fmean((forward.aggregate_score, reverse.aggregate_score))
    for method in ("frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"):
        assert cast(dict[str, float], symmetric["methods"])[method] == fmean(
            (forward.methods[method].score, reverse.methods[method].score)
        )


def test_offline_auditor_uses_each_directional_similarity_settings_instance(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent reconstruction applies the settings belonging to each reference trace."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    original_training = auditor._training  # pyright: ignore[reportPrivateUsage]
    original_report_inputs = auditor._report_inputs  # pyright: ignore[reportPrivateUsage]
    original_compare = auditor.compare_traces
    expected_settings: dict[tuple[tuple[float, Direction, int], ...], SimilarityConfig] = {}
    calls: list[tuple[tuple[tuple[float, Direction, int], ...], SimilarityConfig]] = []
    recording = False

    def trace_key(events: Sequence[TraceEvent]) -> tuple[tuple[float, Direction, int], ...]:
        return tuple((event.timestamp, event.direction, event.frame_length) for event in events)

    def isolated_training(*args: Any, **kwargs: Any) -> auditor._Training:  # pyright: ignore[reportPrivateUsage]
        item = original_training(*args, **kwargs)
        return replace(item, config=item.config.model_copy(deep=True))

    def report_inputs_spy(*args: Any, **kwargs: Any) -> dict[str, object]:
        nonlocal recording
        training = cast(Sequence[auditor._Training], args[0])  # pyright: ignore[reportPrivateUsage]
        expected_settings.update(
            {trace_key(normalize_reference(item.reference)[0]): item.config.similarity for item in training}
        )
        recording = True
        try:
            return original_report_inputs(*args, **kwargs)
        finally:
            recording = False

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        if recording:
            calls.append((trace_key(reference), settings))
        return original_compare(reference, generated, window, settings)

    monkeypatch.setattr(auditor, "_training", isolated_training)
    monkeypatch.setattr(auditor, "_report_inputs", report_inputs_spy)
    monkeypatch.setattr(auditor, "compare_traces", comparison_spy)

    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate
    assert len(calls) == 18
    assert all(settings is expected_settings[reference] for reference, settings in calls)


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
        assert timeout == study.SUBPROCESS_TIMEOUTS["git_or_version"]
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=stdout, stderr=b"")

    assert study._ignored_prerequisite_worktree_paths(repository, (), runner=runner) == frozenset()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match=expected):
        study._ignored_prerequisite_worktree_paths(repository, ("foreign",), runner=runner)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("failure", ("directory", "entry"))
def test_prerequisite_worktree_entry_scan_rejects_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["directory", "entry"],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    entry = repository / "source.py"
    entry.write_text("pass\n", encoding="utf-8")

    if failure == "directory":

        def unavailable_iterdir(path: Path) -> Any:
            assert path == repository
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "iterdir", unavailable_iterdir)
    else:

        def one_entry(path: Path) -> Any:
            assert path == repository
            return iter((entry,))

        def unavailable_lstat(path: Path) -> Any:
            assert path == entry
            raise OSError("entry unavailable")

        monkeypatch.setattr(Path, "iterdir", one_entry)
        monkeypatch.setattr(Path, "lstat", unavailable_lstat)

    with pytest.raises(ValueError):
        study._prerequisite_worktree_entries(repository)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_worktree_cleanliness_rejects_unignored_special_entries(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "ordinary.txt").write_text("ordinary\n", encoding="utf-8")
    special = repository / "foreign.fifo"
    os.mkfifo(special)
    runner = ScriptedPrerequisiteRunner(repository)
    runner.ignored_worktree_paths = frozenset()

    with pytest.raises(ValueError, match="non-regular entry"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (".superpowers/state", True),
        (".coverage", True),
        ("TASK.md", True),
        (".env.local", True),
        (".coverage.local", True),
        ("pkg/__pycache__/x.pyc", True),
        ("pkg.egg-info/METADATA", True),
        ("module.pyd", True),
        ("collector.log", True),
        ("runs/local/state.json", True),
        ("examples/validation_study/configs/short.toml", True),
        ("examples/validation_study/results.json", True),
        ("examples/validation_study/.study-work/state", True),
        ("examples/validation_study/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.study-1.tmp/state", True),
        ("foreign.py", False),
    ),
)
def test_auditor_ignored_worktree_path_policy_is_explicit(path: str, expected: bool) -> None:
    assert auditor._permitted_ignored_relocated_worktree_path(path) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (".superpowers/state", True),
        (".coverage", True),
        ("TASK.md", True),
        (".env.local", True),
        (".coverage.local", True),
        ("pkg/__pycache__/x.pyc", True),
        ("pkg.egg-info/METADATA", True),
        ("module.pyd", True),
        ("collector.log", True),
        ("runs/local/state.json", True),
        ("examples/validation_study/prerequisites.json", True),
        ("examples/validation_study/results.json", True),
        ("examples/validation_study/configs/short.toml", True),
        ("examples/validation_study/.study-work/state", True),
        ("examples/validation_study/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.study-1.tmp/state", True),
        ("foreign.py", False),
    ),
)
def test_prerequisite_ignored_worktree_path_policy_is_explicit(path: str, expected: bool) -> None:
    assert study._permitted_ignored_prerequisite_worktree_path(path) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("status", (b"", b"?? foreign\0"))
def test_auditor_worktree_status_parser_accepts_empty_and_canonical_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: bytes,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git_bytes(*_args: object, **_kwargs: object) -> bytes:
        return status

    monkeypatch.setattr(auditor, "_git_bytes", git_bytes)
    auditor._relocated_worktree_paths(repository)  # pyright: ignore[reportPrivateUsage]


def test_auditor_worktree_entry_scan_covers_regular_directory_special_and_skipped_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "source.py").write_text("pass\n", encoding="utf-8")
    nested = repository / "nested"
    nested.mkdir()
    (nested / "child.py").write_text("pass\n", encoding="utf-8")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    (repository / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (repository / ".venv" / "deep").mkdir(parents=True)
    (repository / ".venv" / "deep" / "ignored.py").write_text("pass\n", encoding="utf-8")
    special = repository / "special.fifo"
    os.mkfifo(special)

    entries, nonregular = auditor._relocated_worktree_entry_paths(  # pyright: ignore[reportPrivateUsage]
        repository,
        candidate_paths=("candidate.txt",),
    )

    assert "source.py" in entries
    assert "nested/child.py" in entries
    assert "candidate.txt" not in entries
    assert ".git" not in entries
    assert ".venv/deep/ignored.py" not in entries
    assert nonregular == ("special.fifo",)


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

    monkeypatch.setattr(study, "_prerequisite_worktree_entries", entries)
    monkeypatch.setattr(study, "_ignored_prerequisite_worktree_paths", ignored_paths)
    monkeypatch.setattr(study, "_permitted_ignored_prerequisite_worktree_path", permitted_path)

    with pytest.raises(ValueError, match="non-regular entry: second"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_cleanliness_uses_real_git_stdin_nul_records_for_ignored_foreign_names(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True, capture_output=True)
    names = ("foreign space", "foreign\nnewline")
    (repository / ".git" / "info" / "exclude").write_text("foreign*\n", encoding="utf-8")
    for name in names:
        (repository / name).write_text("ignored\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], bytes | None]] = []

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
        assert cwd == repository
        assert check is False
        assert capture_output is True
        assert shell is False
        assert timeout == study.SUBPROCESS_TIMEOUTS["git_or_version"]
        calls.append((tuple(argv), input))
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    with pytest.raises(ValueError, match="ignored prerequisite worktree entry is not permitted"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]

    check_ignore = [call for call in calls if call[0][:3] == ("git", "check-ignore", "-z")]
    assert check_ignore == [
        (
            ("git", "check-ignore", "-z", "--stdin"),
            b"".join(os.fsencode(name) + b"\0" for name in sorted(names)),
        )
    ]


def test_prerequisite_cleanliness_rejects_non_utf8_ignored_git_record_after_byte_exact_input(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True, capture_output=True)
    raw_name = b"foreign-\xff"
    name = os.fsdecode(raw_name)
    (repository / ".git" / "info" / "exclude").write_text("foreign*\n", encoding="utf-8")
    (repository / name).write_text("ignored\n", encoding="utf-8")
    check_ignore_inputs: list[bytes | None] = []

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
        assert cwd == repository
        if tuple(argv[:3]) == ("git", "check-ignore", "-z"):
            check_ignore_inputs.append(input)
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    with pytest.raises(ValueError, match="ignored prerequisite path is not UTF-8"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]

    assert check_ignore_inputs == [raw_name + b"\0"]


def test_prerequisite_ignored_path_codec_skips_real_git_for_an_empty_path_set(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[tuple[str, ...]] = []

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
        calls.append(tuple(argv))
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    assert study._ignored_prerequisite_worktree_paths(repository, (), runner=runner) == frozenset()  # pyright: ignore[reportPrivateUsage]
    assert calls == []
