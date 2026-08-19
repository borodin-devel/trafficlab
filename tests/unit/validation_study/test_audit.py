from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tomllib
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from scripts import audit_validation_study as auditor
from scripts import generate_validation_study_fixture as fixture_generator
from scripts import run_validation_study as study
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study import (
    CAPTURE_BYTES,
    FIT_FIXTURE,
    ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS,
    REFERENCE_BYTES,
    ROOT,
    candidate_index,
    copy_validation_study_candidate,
    finish_validation_study_worktree_cleanup,
    frozen,
    offline_capture,
    offline_validation_study_primary,
    remove_validation_study_worktree,
    response_headers,
    rewrite_candidate_manifest,
    tree_inventory,
    validation_study_request_test_name,
    write_candidate_index,
    write_canonical_json,
    write_study_inputs,
)
from trafficlab.comparison import compare_experiment, parse_comparison_result
from trafficlab.compatibility import identify_bytes
from trafficlab.config import ExperimentConfig, SimilarityConfig
from trafficlab.config_io import load_configuration_pair, render_effective_config
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.fitting import fit_experiment
from trafficlab.generation import generate_experiment
from trafficlab.models.registry import load_best_model, rebuild_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import open_or_prepare_experiment
from trafficlab.run import RunDependencies, run_experiment
from trafficlab.trace import TraceEvent, normalize_reference, parse_capture_metadata

VALIDATION_STUDY_LOCAL_EXCLUDE_LOCK = Path("/tmp") / (
    f"trafficlab-validation-study-{hashlib.sha256(str(ROOT).encode('utf-8')).hexdigest()}.exclude.lock"
)


def offline_published_study(repository_root: Path) -> tuple[Path, Path, Path]:
    prerequisite_path, _expected = write_study_inputs(repository_root)
    prerequisites = study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)
    configs = study.validate_base_configs(repository_root, prerequisites)
    workloads = {item.name: item for item in study.workload_specs(prerequisites.url)}
    records: list[study.StudyRunRecord] = []
    traces: dict[tuple[study.WorkloadName, int], tuple[TraceEvent, ...]] = {}
    settings: dict[study.WorkloadName, SimilarityConfig] = {}
    for order, run_id, workload_value, repeat in study.PRIMARY_ORDER:
        workload_name = cast(study.WorkloadName, workload_value)
        run_result, spec, workload, responses = offline_validation_study_primary(
            repository_root,
            execution_order=order,
            run_id=run_id,
            workload_name=workload_name,
            repeat=repeat,
            base_config=configs[workload_name],
        )
        records.append(
            study.extract_primary_record(
                repository_root,
                spec,
                workload,
                run_result,
                float(order),
                responses,
            )
        )
        traces[(workload_name, repeat)] = study._load_reference_trace(  # pyright: ignore[reportPrivateUsage]
            spec.run_directory
        )
        settings[workload_name] = configs[workload_name].similarity

    def reproduction_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        run_experiment(
            repository_root / command[-1],
            dependencies=RunDependencies(
                open_or_prepare_experiment,
                offline_capture,
                fit_experiment,
                generate_experiment,
                compare_experiment,
            ),
        )
        scratch = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "mount"
            / "study-1"
            / "streaming.headers"
        )
        scratch.write_bytes(response_headers(0, 4_194_303))
        return subprocess.CompletedProcess(command, 0, stdout=b"offline reproduction\n", stderr=b"")

    reproduction = study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisites.study_id,
        configs["streaming"],
        records[3],
        workloads["streaming"],
        object_size_bytes=cast(int, prerequisites.capability["object_size_bytes"]),
        runner=reproduction_runner,
        perf_counter=iter((20.0, 22.0)).__next__,
    )
    identity = cast(
        study.JsonObject,
        {
            "git_commit": prerequisites.git_commit,
            "python_version": prerequisites.tools["python_version"],
            "trafficlab_version": prerequisites.tools["trafficlab_version"],
            "docker_engine_version": prerequisites.tools["docker_engine_version"],
            "docker_compose_version": prerequisites.tools["docker_compose_version"],
            "platform": prerequisites.tools["platform"],
        },
    )
    result = study.StudyResults(
        schema_version=1,
        environment=study._environment_record(  # pyright: ignore[reportPrivateUsage]
            prerequisites, identity, "2026-08-13T13:00:00Z"
        ),
        protocol=study._protocol_record(  # pyright: ignore[reportPrivateUsage]
            prerequisites, prerequisite_path.read_bytes()
        ),
        runs=tuple(records),
        natural_variation=cast(
            tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject],
            tuple(frozen(value) for value in study.natural_variation(records, traces, settings)),
        ),
        workload_summaries=cast(
            tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject],
            tuple(frozen(value) for value in study.workload_summaries(records)),
        ),
        reproduction=reproduction,
    )
    result_path = repository_root / "examples" / "validation_study" / "results.json"
    study._publish_results(result_path, result, repository_root=repository_root)  # pyright: ignore[reportPrivateUsage]
    report_path = repository_root / "examples" / "validation_study" / "REPORT.md"
    identifiers = [
        prerequisites.study_id,
        prerequisites.git_commit,
        cast(str, prerequisites.images["target_image_id"]),
        cast(str, prerequisites.images["capture_image_id"]),
        *(record.run_id for record in records),
        "10-streaming-r2-reproduction",
    ]
    report_path.write_text("\n\n".join((*study.REPORT_HEADINGS, *identifiers)), encoding="utf-8")
    return prerequisite_path, result_path, report_path


def finish_validation_study_exclude_restore(*, body_error: BaseException | None, restore: Callable[[], None]) -> None:
    """Restore shared Git exclusion bytes without discarding a primary audit failure."""

    try:
        restore()
    except BaseException as cleanup_error:
        if body_error is not None:
            raise BaseExceptionGroup(
                "validation-study audit and shared Git exclusion cleanup both failed",
                (body_error, cleanup_error),
            ) from None
        raise


@contextmanager
def exclusive_validation_study_file_lock(path: Path) -> Generator[None, None, None]:
    """Serialize a temporary mutation of shared Git administration bytes across workers."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def candidate_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def auditor_semantics_fixture_config() -> ExperimentConfig:
    """Build one valid config with the declared host mount that publication relocates."""

    document = tomllib.loads(
        (VALIDATION_STUDY_CANDIDATE / "configs" / "training-short-r1.realized.toml").read_text(encoding="utf-8")
    )
    target = cast(dict[str, Any], document["target"])
    target["mounts"] = [{"source": "/retained/mount", "target": "/trafficlab-study/short.headers", "read_only": True}]
    return ExperimentConfig.model_validate(document)


NONOPERATIONAL_CONFIG_MUTATIONS = (
    "master_seed",
    "target_image",
    "target_argv_order",
    "target_environment",
    "target_working_directory",
    "mount_target",
    "mount_read_only",
    "capture_timeout",
    "trial_limit",
    "final_limit",
    "population_size",
    "model_bound",
    "similarity_limit",
)

NONOPERATIONAL_REALIZED_CONFIG_MUTATIONS = tuple(
    case for case in NONOPERATIONAL_CONFIG_MUTATIONS if case not in {"mount_target", "mount_read_only"}
)


def nonoperational_config_mutation(config: ExperimentConfig, case: str) -> ExperimentConfig:
    document = config.model_dump(mode="json")
    target = cast(dict[str, Any], document["target"])
    if case == "master_seed":
        cast(dict[str, Any], document["run"])["master_seed"] = 74
    elif case == "target_image":
        target["image"] = "curlimages/curl@sha256:" + "1" * 64
    elif case == "target_argv_order":
        target["argv"] = list(reversed(cast(list[str], target["argv"])))
    elif case == "target_environment":
        target["environment"] = {"TRAFFICLAB_MUTATION": "1"}
    elif case == "target_working_directory":
        target["working_directory"] = "/changed"
    elif case == "mount_target":
        mount = cast(dict[str, Any], cast(list[object], target["mounts"])[0])
        mount["target"] = "/changed.headers"
    elif case == "mount_read_only":
        mount = cast(dict[str, Any], cast(list[object], target["mounts"])[0])
        mount["read_only"] = False
    elif case == "capture_timeout":
        cast(dict[str, Any], document["capture"])["readiness_timeout_seconds"] = 3.0
    elif case == "trial_limit":
        cast(dict[str, Any], cast(dict[str, Any], document["generation"])["trial"])["max_packets"] = 501
    elif case == "final_limit":
        cast(dict[str, Any], cast(dict[str, Any], document["generation"])["final"])["max_packets"] = 1001
    elif case == "population_size":
        cast(dict[str, Any], document["genetic"])["population_size"] = 7
    elif case == "model_bound":
        cast(dict[str, Any], cast(dict[str, Any], document["models"])["poisson_empirical"])["c_lambda"]["lower"] = 0.6
    else:
        cast(dict[str, Any], document["similarity"])["max_direction_bin_cells"] = 101

    return ExperimentConfig.model_validate(document)


def config_semantic_leaf_paths(value: Any, prefix: tuple[str | int, ...] = ()) -> tuple[tuple[str | int, ...], ...]:
    if prefix == ("run", "directory"):
        return ()
    if prefix == ("target", "mounts"):
        mounts = cast(list[dict[str, Any]], value)
        return tuple(
            path
            for index, mount in enumerate(mounts)
            for path in (("target", "mounts", index, "target"), ("target", "mounts", index, "read_only"))
            if path[-1] in mount
        )
    if prefix == ("similarity", "method_weights"):
        return (prefix,)
    if prefix == ("similarity",):
        coupled = {
            "acf_lags",
            "acf_lag_weights",
            "acf_iat_weight",
            "acf_size_weight",
            "multiscale_packet_weight",
            "multiscale_byte_weight",
        }
        settings = cast(dict[str, Any], value)
        return (
            ("similarity", "__acf_lags_and_weights__"),
            ("similarity", "__acf_component_weights__"),
            ("similarity", "__multiscale_component_weights__"),
            *(
                path
                for key, child in settings.items()
                if key not in coupled
                for path in config_semantic_leaf_paths(child, (*prefix, key))
            ),
        )
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if not mapping:
            return (prefix,)
        return tuple(
            path for key, child in mapping.items() for path in config_semantic_leaf_paths(child, (*prefix, key))
        )
    return (prefix,)


def config_semantic_path_value(document: dict[str, Any], path: tuple[str | int, ...]) -> Any:
    if isinstance(path[-1], str) and path[-1].startswith("__"):
        return None
    value: Any = document
    for part in path:
        value = value[part]
    return value


def set_config_semantic_path_value(
    document: dict[str, Any],
    path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    if path == ("similarity", "__acf_lags_and_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["acf_lags"] = [1, 2]
        similarity["acf_lag_weights"] = [0.5, 0.5]
        return
    if path == ("similarity", "__acf_component_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["acf_iat_weight"] = 0.6
        similarity["acf_size_weight"] = 0.4
        return
    if path == ("similarity", "__multiscale_component_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["multiscale_packet_weight"] = 0.6
        similarity["multiscale_byte_weight"] = 0.4
        return
    parent = config_semantic_path_value(document, path[:-1])
    parent[path[-1]] = replacement


def config_semantic_replacements(path: tuple[str | int, ...], value: Any) -> tuple[Any, ...]:
    if isinstance(path[-1], str) and path[-1].startswith("__"):
        return (None,)
    if path == ("target", "image"):
        return ("curlimages/curl@sha256:" + "1" * 64,)
    if path == ("capture", "image"):
        return ("trafficlab-capture@sha256:" + "2" * 64,)
    if path == ("capture", "network_probe_url"):
        return ("https://example.test/changed",)
    if path == ("similarity", "method_weights"):
        weights = cast(dict[str, float], value)
        return ({**weights, "frame_size_ks": 0.30, "iat_ks": 0.20},)
    if type(value) is bool:
        return (not value,)
    if type(value) is int:
        return (value + 1, value - 1)
    if type(value) is float:
        return (value + 0.01, value - 0.01)
    if type(value) is str:
        return (f"{value}-changed",)
    if isinstance(value, list):
        items = cast(list[Any], value)
        if path == ("genetic", "trial_seeds"):
            return ([cast(int, items[0]) + 1],)
        if all(type(item) is str for item in items):
            return (list(reversed(items)),)
        if all(type(item) is int for item in items):
            return ([cast(int, items[0]) + 1],)
        if len(items) == 1:
            return ([cast(float, items[0]) + 0.1], [cast(float, items[0]) - 0.1])
        changed = list(items)
        changed[0] = cast(float, changed[0]) + 0.1
        changed[-1] = cast(float, changed[-1]) - 0.1
        return (changed,)
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return ({**mapping, "TRAFFICLAB_MUTATION": "1"},)
    raise AssertionError(f"no mutation candidate for config path {path}")


def test_local_audit_revalidates_report_checkpoint_artifacts_and_lineage_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, result_path, report_path = offline_published_study(repository_root)
    prerequisite = study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)

    def reject_external(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("external")

    monkeypatch.setattr(study.subprocess, "run", reject_external)
    before = {path: path.read_bytes() for path in repository_root.rglob("*") if path.is_file()}
    study.audit_published_study(
        repository_root=repository_root,
        prerequisite_path=prerequisite_path,
        result_path=result_path,
        report_path=report_path,
    )
    after = {path: path.read_bytes() for path in repository_root.rglob("*") if path.is_file()}
    assert before == after
    for missing in (
        study.REPORT_HEADINGS[0],
        prerequisite.study_id,
        prerequisite.git_commit,
        cast(str, prerequisite.images["target_image_id"]),
        cast(str, prerequisite.images["capture_image_id"]),
        study.PRIMARY_ORDER[0][1],
        "10-streaming-r2-reproduction",
    ):
        original = report_path.read_text(encoding="utf-8")
        report_path.write_text(original.replace(missing, "removed", 1), encoding="utf-8")
        with pytest.raises(TrafficlabError, match="report"):
            study.audit_published_study(
                repository_root=repository_root,
                prerequisite_path=prerequisite_path,
                result_path=result_path,
                report_path=report_path,
            )
        report_path.write_text(original, encoding="utf-8")

    results = study.parse_study_results(result_path.read_bytes(), repository_root=repository_root)
    checkpoint_path = repository_root / results.runs[0].run_directory / "checkpoint.json"
    checkpoint_content = checkpoint_path.read_bytes()
    checkpoint_path.write_bytes(checkpoint_content + b" ")
    with pytest.raises(TrafficlabError):
        study.audit_published_study(
            repository_root=repository_root,
            prerequisite_path=prerequisite_path,
            result_path=result_path,
            report_path=report_path,
        )
    checkpoint_path.write_bytes(checkpoint_content)


def test_generated_validation_study_template_restores_an_independent_candidate(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Generated mutation candidates restore immutable template bytes between tests."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    original_environment = (candidate / "environment.json").read_bytes()
    assert original_environment == (generated_validation_study_candidate_template / "environment.json").read_bytes()
    (candidate / "environment.json").write_bytes(b"mutated test candidate\n")

    next_repository, next_candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )

    assert next_repository == repository
    assert next_candidate == candidate
    assert (next_candidate / "environment.json").read_bytes() == original_environment


def test_relocated_audit_candidate_uses_a_detached_git_worktree(tmp_path: Path) -> None:
    """Repeated unit audits use a real checkout without duplicating repository objects."""
    repository, _candidate = copy_validation_study_candidate(tmp_path)
    source_environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text(encoding="utf-8")),
    )
    assert (repository / ".git").is_file()
    assert (
        subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == source_environment["source_commit"]
    )


def test_shared_validation_study_checkout_refreshes_each_candidate(
    tmp_path: Path, shared_validation_study_repository: Path
) -> None:
    """Candidate-only audits reuse one worker checkout without retaining prior candidate bytes."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    assert repository == shared_validation_study_repository
    (candidate / "foreign.txt").write_text("test-only residue\n", encoding="utf-8")

    next_repository, next_candidate = copy_validation_study_candidate(tmp_path)

    assert next_repository == repository
    assert next_candidate == candidate
    assert not (next_candidate / "foreign.txt").exists()


def test_validation_study_context_uses_node_name_when_original_name_is_absent() -> None:
    """Non-parametrized pytest nodes retain an isolation key for source-mutating audits."""

    request = cast(
        pytest.FixtureRequest,
        SimpleNamespace(
            node=SimpleNamespace(
                name="test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination",
                originalname=None,
            )
        ),
    )

    assert validation_study_request_test_name(request) in ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS


def test_validation_study_local_exclude_lock_is_process_exclusive(tmp_path: Path) -> None:
    """The common Git exclusion mutation serializes independently scheduled workers."""

    lock_path = tmp_path / "exclude.lock"
    probe = (
        "import fcntl, pathlib, sys\n"
        "with pathlib.Path(sys.argv[1]).open('a+b') as stream:\n"
        "    try:\n"
        "        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    except BlockingIOError:\n"
        "        raise SystemExit(0)\n"
        "    raise SystemExit(1)\n"
    )

    with exclusive_validation_study_file_lock(lock_path):
        result = subprocess.run(
            (sys.executable, "-c", probe, str(lock_path)),
            check=False,
            capture_output=True,
        )

    assert result.returncode == 0


def test_validation_study_worktree_removal_propagates_cleanup_failure(tmp_path: Path) -> None:
    """Detached-checkout cleanup must not silently retain Git administration state."""

    with pytest.raises(subprocess.CalledProcessError):
        remove_validation_study_worktree(tmp_path / "not-a-worktree")


def test_validation_study_worktree_cleanup_preserves_a_primary_failure() -> None:
    """A failing finalizer adds its diagnostic without erasing the body failure."""

    primary = RuntimeError("primary test failure")
    cleanup = OSError("synthetic worktree cleanup failure")

    def cleanup_failure(_repository: Path) -> None:
        raise cleanup

    with pytest.raises(BaseExceptionGroup) as captured:
        finish_validation_study_worktree_cleanup(
            (Path("owned-worktree"),),
            body_error=primary,
            remove=cleanup_failure,
        )

    assert captured.value.exceptions == (primary, cleanup)


def test_validation_study_exclude_restore_preserves_a_primary_failure() -> None:
    """A shared-Git restore failure retains the audit assertion that triggered cleanup."""

    primary = RuntimeError("primary audit failure")
    cleanup = OSError("synthetic exclude restore failure")

    def restore_failure() -> None:
        raise cleanup

    with pytest.raises(BaseExceptionGroup) as captured:
        finish_validation_study_exclude_restore(body_error=primary, restore=restore_failure)

    assert captured.value.exceptions == (primary, cleanup)


def test_offline_audit_reconstructs_held_out_without_calling_the_producer_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auditor derives the independent held-out horizon from retained public bytes."""

    repository, candidate = copy_validation_study_candidate(tmp_path)

    def producer_boundary_must_not_run(**_kwargs: object) -> study.HeldOutEvaluation:
        raise AssertionError("auditor delegated held-out reconstruction to the producer boundary")

    monkeypatch.setattr(auditor, "evaluate_study_held_out", producer_boundary_must_not_run, raising=False)
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate


@pytest.mark.parametrize("case", ("tracked_auditor", "tracked_source", "untracked_source"))
def test_offline_auditor_rejects_non_evidence_worktree_changes(
    tmp_path: Path,
    case: str,
) -> None:
    """The accepted audit cannot trust a checkout with mutable auditor or source inputs."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    if case == "tracked_auditor":
        changed = repository / "scripts" / "audit_validation_study.py"
        changed.write_bytes(changed.read_bytes() + b"\n# dirty auditor\n")
    elif case == "tracked_source":
        changed = repository / "src" / "trafficlab" / "comparison.py"
        changed.write_bytes(changed.read_bytes() + b"\n# dirty source\n")
    else:
        (repository / "untracked_source.py").write_text("sentinel = True\n", encoding="utf-8")

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "environment", "not_published", "primary")
    assert "working-tree" in outcome.detail


def test_offline_auditor_allows_document_test_and_evidence_worktree_changes(tmp_path: Path) -> None:
    """The source guard permits non-production test changes and retained evidence."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    for relative in (
        "examples/validation_study/README.md",
        "examples/validation_study/REPORT.md",
        "tests/fixtures/data/manifest.json",
        "tests/fixtures/data/validation_study/candidate/environment.json",
    ):
        path = repository / relative
        path.write_bytes(path.read_bytes() + b"\nlocal audit note\n")
    evidence_note = repository / "examples" / "validation_study" / "evidence" / "local-audit-note.txt"
    evidence_note.parent.mkdir(parents=True, exist_ok=True)
    evidence_note.write_text("retained evidence note\n", encoding="utf-8")
    for relative in (
        "examples/validation_study/.study-work/attempts/fixture-study/state.json",
        "examples/validation_study/evidence/.candidates/fixture-study/state.json",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    if hasattr(os, "mkfifo"):
        ignored_fifo = (
            repository / "examples" / "validation_study" / ".study-work" / "attempts" / "fixture-study" / "state.fifo"
        )
        os.mkfifo(ignored_fifo)

    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert b"examples/validation_study/README.md" in status
    assert b"examples/validation_study/evidence/local-audit-note.txt" in status
    assert b".study-work" not in status
    assert b".candidates" not in status
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate


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
    assert auditor.audit_bundle(accepted, repository=repository).bundle == accepted


def test_offline_auditor_does_not_exempt_an_external_staged_source_candidate(tmp_path: Path) -> None:
    """Only source candidates beneath the relocated repository can suppress worktree evidence."""

    repository, candidate = copy_validation_study_candidate(tmp_path)

    assert (
        auditor._audit_staged_bundle(  # pyright: ignore[reportPrivateUsage]
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

    def no_nonregular_paths(_repository: Path, *, candidate_paths: Sequence[str]) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(auditor, "_relocated_worktree_paths", source_paths)
    monkeypatch.setattr(auditor, "_nonregular_relocated_worktree_paths", no_nonregular_paths)

    with pytest.raises(auditor._Issue, match="non-evidence working-tree change") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._require_permitted_relocated_worktree(  # pyright: ignore[reportPrivateUsage]
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
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_foreign",
        "environment",
        "not_published",
    )


@pytest.mark.parametrize("entry_kind", ("regular", "symlink", "fifo"))
def test_offline_auditor_rejects_local_exclude_ignored_non_evidence_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    """Local Git exclusion cannot exempt a source entry from the relocated audit boundary."""

    if entry_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository, candidate = copy_validation_study_candidate(tmp_path)
    relative = f"locally-excluded-{entry_kind}"
    source = repository / relative
    exclude_value = subprocess.run(
        ("git", "rev-parse", "--git-path", "info/exclude"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    exclude = Path(exclude_value)
    if not exclude.is_absolute():
        exclude = repository / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_validation_study_file_lock(VALIDATION_STUDY_LOCAL_EXCLUDE_LOCK):
        original_exclude = exclude.read_bytes() if exclude.exists() else None
        body_error: BaseException | None = None
        try:
            with exclude.open("a", encoding="utf-8") as stream:
                stream.write(f"{relative}\n")
            if entry_kind == "regular":
                source.write_text("ignored foreign source\n", encoding="utf-8")
            elif entry_kind == "symlink":
                source.symlink_to("scripts/audit_validation_study.py")
            else:
                os.mkfifo(source)

            status = subprocess.run(
                ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
                cwd=repository,
                check=True,
                capture_output=True,
            ).stdout
            assert relative.encode("utf-8") not in status
            with pytest.raises(TrafficlabError) as captured:
                auditor.audit_bundle(candidate, repository=repository)

            outcome = captured.value.failure_outcome
            assert outcome is not None
            assert (
                outcome.kind,
                outcome.stage,
                outcome.detail,
                outcome.affected_evidence,
                outcome.evidence_state,
                outcome.authority,
            ) == (
                "artifact_foreign",
                "publication",
                f"relocated checkout contains non-evidence working-tree change: {relative}",
                "environment",
                "not_published",
                "primary",
            )
        except BaseException as error:
            body_error = error
            raise
        finally:

            def restore() -> None:
                if original_exclude is None:
                    exclude.unlink(missing_ok=True)
                else:
                    exclude.write_bytes(original_exclude)

            finish_validation_study_exclude_restore(body_error=body_error, restore=restore)


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (b"broken\0", "working-tree status"),
        (b"?? untracked_source.py", "working-tree status"),
        (b"?? " + bytes((255, 0)), "working-tree path is not UTF-8"),
        (bytes((255, 63, 32)) + b"source.py\0", "working-tree status is not ASCII"),
        (b"!! source.py\0", "working-tree status is malformed"),
        (b"?? /source.py\0", "working-tree path is not repository-relative"),
        (b"?? ../source.py\0", "working-tree path is not repository-relative"),
        (b"?? \0", "working-tree status is malformed"),
    ),
)
def test_offline_auditor_rejects_malformed_worktree_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: bytes,
    expected: str,
) -> None:
    """Git-status decoding is itself canonical audit evidence, not a best-effort hint."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    original_git_bytes = auditor._git_bytes  # pyright: ignore[reportPrivateUsage]

    def malformed_status(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
        if name == "relocated Git working tree":
            return status
        return original_git_bytes(repository, argv, name=name)

    monkeypatch.setattr(auditor, "_git_bytes", malformed_status)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "environment",
        "not_published",
    )
    assert expected in outcome.detail


@pytest.mark.parametrize(("case", "expected_kind"), (("oserror", "artifact_corrupt"), ("nonzero", "artifact_foreign")))
def test_offline_auditor_classifies_worktree_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    """The worktree inspection retains the existing Git failure taxonomy."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    original_run = auditor.subprocess.run

    def worktree_failure(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        if command == ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"):
            if case == "oserror":
                raise OSError("synthetic status failure")
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"synthetic status failure\n")
        return cast(Any, original_run)(*args, **kwargs)

    monkeypatch.setattr(auditor.subprocess, "run", worktree_failure)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "environment",
        "not_published",
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    (
        (1, b"", frozenset[str]()),
        (0, b"foreign.fifo\0", frozenset({"foreign.fifo"})),
    ),
)
def test_offline_auditor_exactly_parses_terminal_nul_ignored_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: bytes,
    expected: frozenset[str],
) -> None:
    """The Git NUL protocol has explicit empty and exactly-delimited records."""

    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[tuple[tuple[str, ...], bytes]] = []

    def check_ignore(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        calls.append((command, cast(bytes, kwargs["input"])))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")

    monkeypatch.setattr(auditor.subprocess, "run", check_ignore)

    assert (
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )
        == expected
    )
    assert calls == [(("git", "check-ignore", "-z", "--stdin"), b"foreign.fifo\0")]


def test_offline_auditor_rejects_empty_match_ignored_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's match status must include an exact ignored-path record."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def inconsistent_match(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        assert kwargs["input"] == b"foreign.fifo\0"
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(auditor.subprocess, "run", inconsistent_match)

    with pytest.raises(auditor._Issue, match="must be nonempty for match status") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_rejects_nonempty_no_match_ignored_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's no-match status cannot carry a record that exempts a special entry."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def inconsistent_no_match(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        assert kwargs["input"] == b"foreign.fifo\0"
        return subprocess.CompletedProcess(command, 1, stdout=b"foreign.fifo\0", stderr=b"")

    monkeypatch.setattr(auditor.subprocess, "run", inconsistent_no_match)

    with pytest.raises(auditor._Issue, match="must be empty for no-match status") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


@pytest.mark.parametrize(
    ("case", "expected_kind", "expected"),
    (
        ("oserror", "artifact_corrupt", "could not inspect relocated Git ignored paths"),
        ("nonzero", "artifact_foreign", "could not resolve ignored paths"),
        ("non_utf8", "artifact_corrupt", "relocated Git ignored path is not UTF-8"),
        ("foreign_path", "artifact_corrupt", "ignored paths do not match"),
        ("truncated", "artifact_corrupt", "ignored paths must be terminal NUL-delimited"),
        ("duplicate", "artifact_corrupt", "ignored paths must be unique"),
        ("nonempty_no_match", "artifact_corrupt", "ignored paths must be empty for no-match status"),
        ("empty_match", "artifact_corrupt", "ignored paths must be nonempty for match status"),
    ),
)
def test_offline_auditor_classifies_ignored_special_entry_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
    expected: str,
) -> None:
    """The special-entry ignore query remains a strict Git audit boundary."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository, candidate = copy_validation_study_candidate(tmp_path)
    source = repository / "foreign.fifo"
    os.mkfifo(source)
    original_run = auditor.subprocess.run

    def ignored_path_failure(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        if command == ("git", "check-ignore", "-z", "--stdin"):
            if case == "oserror":
                raise OSError("synthetic ignored-path failure")
            if case == "nonzero":
                return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"synthetic failure\n")
            if case == "non_utf8":
                return subprocess.CompletedProcess(command, 0, stdout=bytes((255, 0)), stderr=b"")
            if case == "truncated":
                return subprocess.CompletedProcess(command, 0, stdout=b"foreign.fifo", stderr=b"")
            if case == "duplicate":
                return subprocess.CompletedProcess(command, 0, stdout=b"foreign.fifo\0foreign.fifo\0", stderr=b"")
            if case == "nonempty_no_match":
                return subprocess.CompletedProcess(command, 1, stdout=b"foreign.fifo\0", stderr=b"")
            if case == "empty_match":
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"elsewhere\0", stderr=b"")
        return cast(Any, original_run)(*args, **kwargs)

    monkeypatch.setattr(auditor.subprocess, "run", ignored_path_failure)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "environment",
        "not_published",
    )
    assert expected in outcome.detail


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

    with pytest.raises(auditor._Issue, match="could not inspect relocated working-tree") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._nonregular_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            candidate_paths=(),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_rejects_a_non_utf8_special_entry_path(tmp_path: Path) -> None:
    """Filesystem paths that cannot be rendered into Git's UTF-8 protocol remain corrupt."""

    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(auditor._Issue, match="working-tree path is not UTF-8") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("bad\udcff",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_checks_the_worktree_before_committed_descendant_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutable source is primary before the auditor trusts the committed descendant diff."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    changed = repository / "scripts" / "audit_validation_study.py"
    changed.write_bytes(changed.read_bytes() + b"\n# dirty auditor\n")
    original_git_bytes = auditor._git_bytes  # pyright: ignore[reportPrivateUsage]

    def require_worktree_first(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
        if name == "post-source changed paths":
            pytest.fail("committed descendant paths were trusted before the dirty worktree")
        return original_git_bytes(repository, argv, name=name)

    monkeypatch.setattr(auditor, "_git_bytes", require_worktree_first)

    with pytest.raises(TrafficlabError, match="working-tree"):
        auditor.audit_bundle(candidate, repository=repository)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("recorded_tree", "does not resolve"),
        ("non_ancestor", "is not an ancestor"),
        ("ancestry_oserror", "could not inspect source ancestry"),
        ("non_utf8_path", "post-source path is not UTF-8"),
        ("non_evidence_path", "non-evidence changes"),
        ("changed_image_lock", "capture image-lock bytes"),
    ),
)
def test_offline_auditor_covers_environment_source_binding_failure_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected: str,
) -> None:
    """Supplemental coverage exercises every local Git/source binding rejection."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    content = (candidate / "environment.json").read_bytes()
    if case == "recorded_tree":
        original_identity = auditor._git_identity  # pyright: ignore[reportPrivateUsage]

        def mismatched_recorded_tree(repository: Path, argv: tuple[str, ...], *, name: str) -> str:
            if name == "recorded source tree":
                return "0" * 39 + "1"
            return original_identity(repository, argv, name=name)

        monkeypatch.setattr(auditor, "_git_identity", mismatched_recorded_tree)
    elif case in {"non_ancestor", "ancestry_oserror"}:
        original_run = auditor.subprocess.run

        def source_binding_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            command = tuple(cast(Sequence[str], args[0]))
            if command[:3] == ("git", "merge-base", "--is-ancestor"):
                if case == "ancestry_oserror":
                    raise OSError("synthetic Git failure")
                return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")
            return cast(Any, original_run)(*args, **kwargs)

        monkeypatch.setattr(auditor.subprocess, "run", source_binding_run)
    else:
        original_git_bytes = auditor._git_bytes  # pyright: ignore[reportPrivateUsage]

        def source_binding_bytes(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
            if case == "non_utf8_path" and name == "post-source changed paths":
                return b"\xff\0"
            if case == "non_evidence_path" and name == "post-source changed paths":
                return b"src/trafficlab/__init__.py\0"
            if case == "changed_image_lock" and name == "recorded capture image lock":
                return b"different checked image lock\n"
            return original_git_bytes(repository, argv, name=name)

        monkeypatch.setattr(auditor, "_git_bytes", source_binding_bytes)

    with pytest.raises(auditor._Issue, match=expected):  # pyright: ignore[reportPrivateUsage]
        auditor._environment(content, repository=repository)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("mismatch", ("protocol", "prerequisites"))
def test_offline_auditor_covers_root_study_identity_rejections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """The public bundle checker rejects conflicting candidate, protocol, and prerequisite IDs first."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    entries = auditor._verify_inventory(  # pyright: ignore[reportPrivateUsage]
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

    monkeypatch.setattr(auditor, "_environment", empty_environment)
    if mismatch == "protocol":
        monkeypatch.setattr(auditor, "_protocol", wrong_protocol)
        monkeypatch.setattr(auditor, "_prerequisites", mismatched_prerequisites)
        expected = "protocol destination ID"
    else:
        monkeypatch.setattr(auditor, "_protocol", matching_protocol)
        monkeypatch.setattr(auditor, "_prerequisites", wrong_prerequisites)
        expected = "retained prerequisites must bind"

    with pytest.raises(auditor._Issue, match=expected):  # pyright: ignore[reportPrivateUsage]
        auditor._audit(candidate, repository, entries)  # pyright: ignore[reportPrivateUsage]


def test_offline_bundle_audit_reconstructs_relocated_complete_fixture_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    before = candidate_bytes(candidate)

    def reject_external(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline audit attempted an external operation")

    monkeypatch.setattr(socket, "socket", reject_external)
    monkeypatch.setattr(socket, "create_connection", reject_external)
    original_run = subprocess.run

    def local_git_only(argv: Sequence[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if tuple(argv[:1]) == ("git",):
            return original_run(argv, *args, **kwargs)  # type: ignore[call-overload]
        raise AssertionError("offline audit attempted a non-Git subprocess")

    monkeypatch.setattr(subprocess, "run", local_git_only)
    monkeypatch.setattr(study, "run_experiment", reject_external)

    result = auditor.audit_bundle(candidate, repository=repository)

    assert result.bundle == candidate
    assert result.run_directory == candidate / "training" / "short" / "r1"
    assert result.file_count == len(before) - 1
    assert result.manifest_sha256 == hashlib.sha256(before["manifest.json"]).hexdigest()
    assert candidate_bytes(candidate) == before


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

    assert auditor._config_semantics(relocated) == auditor._config_semantics(baseline)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("case", NONOPERATIONAL_CONFIG_MUTATIONS)
def test_offline_auditor_config_semantics_rejects_each_nonoperational_mutation(case: str) -> None:
    """Every scientific/workload field remains part of the retained config identity."""

    baseline = auditor_semantics_fixture_config()
    mutated = nonoperational_config_mutation(baseline, case)

    assert auditor._config_semantics(mutated) != auditor._config_semantics(baseline)  # pyright: ignore[reportPrivateUsage]


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
            assert auditor._config_semantics(mutated) != auditor._config_semantics(baseline)  # pyright: ignore[reportPrivateUsage]
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
        auditor.audit_bundle(candidate, repository=repository)

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


@pytest.mark.parametrize(
    ("mutation", "expected_kind"),
    (
        ("missing", "artifact_missing"),
        ("corrupt", "artifact_corrupt"),
        ("foreign", "artifact_foreign"),
        ("extra", "artifact_foreign"),
        ("symlink", "artifact_foreign"),
        ("temporary", "artifact_foreign"),
        ("owner", "artifact_foreign"),
        ("lineage", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_rejects_first_manifest_or_artifact_mismatch(
    tmp_path: Path,
    mutation: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)

    if mutation == "missing":
        (candidate / "training" / "short" / "r1" / "best_model.json").unlink()
    elif mutation == "corrupt":
        path = candidate / "training" / "short" / "r1" / "checkpoint.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "foreign":
        target = candidate / "training" / "short" / "r1" / "generated.pcapng"
        target.write_bytes((candidate / "training" / "short" / "r1" / "reference.pcapng").read_bytes())
        rewrite_candidate_manifest(candidate)
    elif mutation == "extra":
        (candidate / "unexpected.bin").write_bytes(b"unexpected")
    elif mutation == "symlink":
        (candidate / "training" / "short" / "r1" / "unexpected-link").symlink_to("generated.pcapng")
    elif mutation == "temporary":
        (candidate / "training" / "short" / "r1" / ".generated.tmp").write_bytes(b"temporary")
    else:
        index_path = candidate / "index.json"
        index = cast(dict[str, object], json.loads(index_path.read_text(encoding="utf-8")))
        relative = "training/short/r1/generated.pcapng"
        mapping_name = "ownership" if mutation == "owner" else "lineage"
        mapping = cast(dict[str, object], index[mapping_name])
        mapping[relative] = f"changed-{mutation}"
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == expected_kind
    assert outcome.stage == "publication"
    assert outcome.evidence_state == "not_published"
    assert outcome.authority == "primary"
    assert error.value.failure_outcomes == (outcome,)
    if mutation == "missing":
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


def test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    evidence_root = repository / "examples" / "validation_study" / "evidence"

    destination = study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)
    before = candidate_bytes(destination)
    root_before = candidate_bytes(repository)

    with pytest.raises(TrafficlabError) as error:
        study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "publication_collision"
    assert outcome.stage == "publication"
    assert destination == evidence_root / "fixture-study"
    assert candidate_bytes(destination) == before
    assert candidate_bytes(repository) == root_before
    assert not tuple(repository.rglob("*.tmp"))


def test_audited_bundle_rejects_the_first_primary_without_publication_residue(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    before = candidate_bytes(candidate)
    evidence_root = repository / "examples" / "validation_study" / "evidence"
    before_evidence = candidate_bytes(evidence_root)
    missing = "protocol.json"
    (candidate / missing).unlink()
    expected_candidate = dict(before)
    del expected_candidate[missing]

    with pytest.raises(TrafficlabError) as error:
        study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

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
        "protocol.json is missing from the retained bundle",
        "protocol.json",
        "not_published",
        "restore the exact retained artifact",
        "primary",
    )
    assert error.value.failure_outcomes == (outcome,)
    assert candidate_bytes(candidate) == expected_candidate
    assert candidate_bytes(evidence_root) == before_evidence
    assert not (evidence_root / "fixture-study").exists()
    assert not tuple(repository.rglob("*.tmp"))


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
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.stage == "publication"
    assert outcome.authority == "primary"


@pytest.mark.parametrize("mutation", ("environment", "final-controls"))
def test_offline_bundle_audit_reconstructs_environment_and_final_controls(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index_path = candidate / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if mutation == "environment":
        (repository / "uv.lock").write_bytes(b"different lock\n")
    else:
        cast(list[dict[str, object]], index["fresh_simulation"])[0]["seed"] = 98
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_foreign"
    assert outcome.affected_evidence in {"environment", "fresh_simulation/short/r1.json"}


def test_offline_bundle_audit_derives_w_from_the_normalized_reference(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    model_path = candidate / "training" / "short" / "r1" / "best_model.json"
    model = load_best_model(model_path.read_bytes(), source=model_path)
    model_path.write_bytes(
        render_best_model(rebuild_best_model(model, observation_window_seconds=model.observation_window_seconds + 1.0))
    )
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

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
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.stage == "publication"
    assert outcome.evidence_state == "not_published"


def test_offline_bundle_audit_reports_the_canonical_jsonl_owner_diagnostic(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    log_path = candidate / "training" / "short" / "r1" / "run.log"
    log_path.write_bytes(b'{"event": "fixture"}\n')
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.detail,
        outcome.affected_evidence,
        outcome.corrective_action,
    ) == (
        "artifact_corrupt",
        "run log record is not canonical JSONL",
        "training/short/r1/run.log",
        "restore canonical run log",
    )


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
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.detail, outcome.affected_evidence) == (
        "artifact_corrupt",
        detail,
        "training/short/r1/run.log",
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_kind"),
    (
        ("scientific_artifact_schema", 1, "scientific_semantics_incompatible"),
        ("python_implementation", "PyPy", "scientific_semantics_incompatible"),
        ("source_commit", "z" * 40, "artifact_corrupt"),
        ("target_image_reference", "trafficlab-target:latest", "artifact_corrupt"),
        ("target_image_id", "sha256:bad", "artifact_corrupt"),
        ("capture_image_reference", "trafficlab-capture:latest", "artifact_corrupt"),
        (
            "compatibility_decision",
            {"reason": "fixture", "status": "incompatible"},
            "scientific_semantics_incompatible",
        ),
    ),
)
def test_offline_bundle_audit_validates_every_environment_lock_boundary(
    tmp_path: Path,
    field: str,
    value: object,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment[field] = value
    write_canonical_json(environment_path, environment)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "environment",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("directory", "artifact_foreign"),
        ("configuration_path", "artifact_foreign"),
        ("seeds", "scientific_semantics_incompatible"),
        ("run_configuration", "artifact_corrupt"),
        ("run_configuration_semantics", "artifact_foreign"),
        ("reconstruction", "artifact_corrupt"),
        ("history", "artifact_foreign"),
        ("winner", "artifact_foreign"),
        ("comparison_parse", "artifact_corrupt"),
        ("comparison", "artifact_foreign"),
        ("index_identity", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_training_record_and_reconstruction_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    training = cast(list[dict[str, object]], index["training"])
    record = training[0]
    run = candidate / "training" / "short" / "r1"

    if case == "directory":
        record["directory"] = "training/short/r2"
        write_candidate_index(candidate, index)
    elif case == "configuration_path":
        record["portable_config"] = "configs/training-short-r1.realized.toml"
        write_candidate_index(candidate, index)
    elif case == "seeds":
        protocol_path = candidate / "protocol.json"
        protocol = cast(dict[str, object], json.loads(protocol_path.read_text(encoding="utf-8")))
        protocol["selection_seeds"] = [18, 30]
        write_canonical_json(protocol_path, protocol)
    elif case == "run_configuration":
        (run / "experiment.toml").write_bytes((candidate / "configs" / "training-short-r1.realized.toml").read_bytes())
    elif case == "run_configuration_semantics":
        document = tomllib.loads((run / "experiment.toml").read_text(encoding="utf-8"))
        cast(dict[str, Any], document["run"])["master_seed"] = 74
        (run / "experiment.toml").write_bytes(render_effective_config(ExperimentConfig.model_validate(document)))
    elif case == "reconstruction":
        (run / "capture.json").write_bytes(b"{}\n")
    elif case == "history":
        (run / "ga_history.csv").write_bytes(b"unexpected-history\n")
    elif case == "winner":
        (run / "best_model.json").write_bytes(
            (candidate / "training" / "short" / "r2" / "best_model.json").read_bytes()
        )
    elif case == "comparison_parse":
        (run / "similarity.json").write_bytes(b"{}\n")
    elif case == "comparison":
        (run / "similarity.json").write_bytes(
            (candidate / "training" / "short" / "r2" / "similarity.json").read_bytes()
        )
    else:
        identity = cast(dict[str, object], record["reference_identity"])
        identity["sha256"] = "0" * 64
        write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    "field",
    (
        "runtime",
        "winner",
        "weights",
        "invalid_chromosome",
        "natural_variation",
        "natural_reverse_null",
        "natural_reverse_missing",
        "natural_excluded",
    ),
)
def test_offline_bundle_audit_recomputes_each_report_input_family(tmp_path: Path, field: str) -> None:
    """Report inputs are independently reconstructed rather than trusted as producer output."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    path = candidate / "report_inputs.json"
    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    if field == "runtime":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        runtime = cast(dict[str, object], records[0]["runtime_seconds"])
        runtime["mean"] = cast(float, runtime["mean"]) + 1.0
    elif field == "winner":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        winners = cast(dict[str, object], records[0]["winner_family_counts"])
        winners["mmpp"] = cast(int, winners["mmpp"]) + 1
    elif field == "weights":
        records = cast(list[dict[str, object]], document["controlled_weight_analysis"])
        records[0]["alternative_aggregate"] = cast(float, records[0]["alternative_aggregate"]) + 1.0
    elif field == "invalid_chromosome":
        records = cast(list[dict[str, object]], document["invalid_chromosome_diagnostics"])
        limits = cast(dict[str, object], records[0]["trial_limits"])
        limits["max_packets"] = cast(int, limits["max_packets"]) + 1
    elif field == "natural_variation":
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        forward = cast(dict[str, object], pairs[0]["forward"])
        forward["aggregate"] = cast(float, forward["aggregate"]) + 1.0
    elif field == "natural_reverse_null":
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        pairs[0]["reverse"] = None
    elif field == "natural_reverse_missing":
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        del pairs[0]["reverse"]
    else:
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        pairs[0]["excluded"] = True
    write_canonical_json(path, document)
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
    ) == ("artifact_foreign", "publication", "report_inputs.json", "not_published", "primary")


@pytest.mark.parametrize(
    "binding",
    auditor._TRANSFER_BINDINGS,  # pyright: ignore[reportPrivateUsage]
    ids=lambda binding: f"{binding.scope}-{binding.run_id}-{binding.transfer_index}",
)
@pytest.mark.parametrize("kind", ("header", "observation"))
def test_offline_bundle_audit_rejects_each_scoped_transfer_file(
    tmp_path: Path,
    binding: auditor._Transfer,  # pyright: ignore[reportPrivateUsage]
    kind: str,
) -> None:
    """Every prerequisite, training, and held-out transfer is an independently retained audit input."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    if kind == "header":
        relative = f"headers/{binding.scope}/{binding.run_id}/{binding.filename}"
        path = candidate / relative
        path.write_bytes(path.read_bytes().replace(b"206", b"205", 1))
    else:
        relative = f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json"
        path = candidate / relative
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["status"] = 205
        write_canonical_json(path, document)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.affected_evidence == relative


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
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == ("artifact_foreign", cast(str, record["path"]))


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("binding", "artifact_foreign"),
        ("configuration", "artifact_foreign"),
        ("training_reference", "artifact_foreign"),
        ("reconstruction", "artifact_corrupt"),
        ("outputs", "artifact_foreign"),
        ("record", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_independent_held_out_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    held = cast(list[dict[str, object]], index["held_out"])[0]
    directory = candidate / cast(str, held["directory"])
    if case == "binding":
        held["training_directory"] = "training/short/r2"
        write_candidate_index(candidate, index)
    elif case == "configuration":
        for name in ("portable.toml", "realized.toml"):
            path = directory / name
            path.write_bytes(path.read_bytes().replace(b"final_seed = 97", b"final_seed = 98"))
    elif case == "training_reference":
        (directory / "reference.pcapng").write_bytes(
            (candidate / "training" / "short" / "r1" / "reference.pcapng").read_bytes()
        )
    elif case == "reconstruction":
        (directory / "capture.json").write_bytes(b"{}\n")
    elif case == "outputs":
        (directory / "generated.pcapng").write_bytes(
            (candidate / "held_out" / "streaming" / "generated.pcapng").read_bytes()
        )
    else:
        record_path = directory / "record.json"
        record = cast(dict[str, object], json.loads(record_path.read_text(encoding="utf-8")))
        record["seed"] = 98
        write_canonical_json(record_path, record)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("schema", "scientific_semantics_incompatible"),
        ("root_path", "artifact_foreign"),
        ("training_type", "artifact_corrupt"),
        ("training_count", "artifact_corrupt"),
        ("training_duplicate", "artifact_foreign"),
        ("fresh_type", "artifact_corrupt"),
        ("fresh_count", "artifact_corrupt"),
        ("held_type", "artifact_corrupt"),
        ("held_count", "artifact_corrupt"),
        ("held_duplicate", "artifact_foreign"),
        ("report_inputs", "artifact_foreign"),
        ("report", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_complete_index_schema_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    if case == "schema":
        index["schema_version"] = 1
        write_candidate_index(candidate, index)
    elif case == "root_path":
        index["report"] = "other-report.json"
        write_candidate_index(candidate, index)
    elif case == "training_type":
        index["training"] = {}
        write_candidate_index(candidate, index)
    elif case == "training_count":
        index["training"] = cast(list[object], index["training"])[:-1]
        write_candidate_index(candidate, index)
    elif case == "training_duplicate":
        training = cast(list[dict[str, object]], index["training"])
        training[-1] = copy.deepcopy(training[0])
        write_candidate_index(candidate, index)
    elif case == "fresh_type":
        index["fresh_simulation"] = {}
        write_candidate_index(candidate, index)
    elif case == "fresh_count":
        index["fresh_simulation"] = cast(list[object], index["fresh_simulation"])[:-1]
        write_candidate_index(candidate, index)
    elif case == "held_type":
        index["held_out"] = {}
        write_candidate_index(candidate, index)
    elif case == "held_count":
        index["held_out"] = cast(list[object], index["held_out"])[:-1]
        write_candidate_index(candidate, index)
    elif case == "held_duplicate":
        held = cast(list[dict[str, object]], index["held_out"])
        held[1] = copy.deepcopy(held[0])
        write_candidate_index(candidate, index)
    elif case == "report_inputs":
        path = candidate / "report_inputs.json"
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["formula"] = "not-arithmetic"
        write_canonical_json(path, document)
    else:
        path = candidate / "report.json"
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["formula"] = "not-arithmetic"
        write_canonical_json(path, document)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (("wrong_type", "artifact_corrupt"), ("manifest_disagreement", "artifact_foreign")),
)
def test_offline_bundle_audit_validates_index_metadata_before_scientific_reconstruction(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    ownership = copy.deepcopy(cast(dict[str, str], index["ownership"]))
    lineage = copy.deepcopy(cast(dict[str, object], index["lineage"]))
    if case == "wrong_type":
        index["ownership"] = []
    else:
        cast(dict[str, object], index["ownership"])["training/short/r1/generated.pcapng"] = "wrong-owner"
    write_candidate_index(candidate, index)
    auditor.write_manifest(candidate, ownership=ownership, lineage=lineage)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == (expected_kind, "index.json")


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
    assert auditor.owner_for_path(relative) == owner
    assert auditor.lineage_for_path(relative) == relation


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
        auditor.owner_for_path(relative)


def test_schema_lineage_mapping_rejects_unknown_path_family() -> None:
    with pytest.raises(Exception, match="documented lineage"):
        auditor.lineage_for_path("not-documented.bin")


def test_schema_file_inventory_reports_enumeration_lstat_and_nonregular_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    def unavailable_rglob(_path: Path, _pattern: str) -> Any:
        raise OSError("enumeration unavailable")

    monkeypatch.setattr(Path, "rglob", unavailable_rglob)
    with pytest.raises(Exception, match="could not enumerate retained bundle"):
        auditor.files_for_candidate(candidate, include_manifest=False)
    monkeypatch.undo()

    regular = candidate / "regular.bin"
    regular.write_bytes(b"regular")
    original_lstat = Path.lstat

    def unavailable_lstat(path: Path) -> os.stat_result:
        if path == regular:
            raise OSError("inspection unavailable")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", unavailable_lstat)
    with pytest.raises(Exception, match="could not inspect regular.bin"):
        auditor.files_for_candidate(candidate, include_manifest=False)
    monkeypatch.undo()

    fifo = candidate / "foreign.fifo"
    os.mkfifo(fifo)
    with pytest.raises(Exception, match="must be a regular file"):
        auditor.files_for_candidate(candidate, include_manifest=False)


def test_schema_manifest_writer_rejects_incomplete_keys_and_empty_owner(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "retained.bin").write_bytes(b"retained")

    with pytest.raises(ValueError, match="keys must equal"):
        auditor.write_manifest(candidate, ownership={}, lineage={})
    with pytest.raises(ValueError, match="nonempty string"):
        auditor.write_manifest(candidate, ownership={"retained.bin": ""}, lineage={"retained.bin": {}})


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

        monkeypatch.setattr(auditor, "_fresh", duplicate_fresh)
    else:
        original = auditor._expected_paths  # pyright: ignore[reportPrivateUsage]

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

        monkeypatch.setattr(auditor, "_expected_paths", altered_expected)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


def test_audit_bundle_rejects_a_candidate_outside_the_relocated_repository(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    outside = tmp_path / "outside-candidate"
    shutil.copytree(candidate, outside)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(outside, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.detail,
        outcome.affected_evidence,
        outcome.corrective_action,
    ) == (
        "artifact_foreign",
        "bundle must remain beneath the relocated repository",
        "bundle",
        "use a retained candidate beneath the repository",
    )


def test_audit_bundle_wraps_an_unclassified_owner_error_and_preserves_a_classified_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    unclassified = TrafficlabError("unclassified owner error", corrective_action="repair source evidence")

    def raise_unclassified(*_args: object, **_kwargs: object) -> object:
        raise unclassified

    monkeypatch.setattr(auditor, "_audit", raise_unclassified)
    with pytest.raises(TrafficlabError) as first:
        auditor.audit_bundle(candidate, repository=repository)
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

    monkeypatch.setattr(auditor, "_audit", raise_classified)
    with pytest.raises(TrafficlabError) as second:
        auditor.audit_bundle(candidate, repository=repository)
    assert second.value is classified
    assert second.value.failure_outcomes == (classified_outcome,)


def test_offline_bundle_fixture_carries_complete_real_program_validation_evidence_and_reconstructs_it(
    tmp_path: Path,
) -> None:
    """A retained candidate distinguishes training, fresh simulation, and independent held-out evidence."""
    repository, candidate = copy_validation_study_candidate(tmp_path)
    before = candidate_bytes(candidate)
    index = json.loads((candidate / "index.json").read_text(encoding="utf-8"))

    assert index["schema_version"] == 3
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

    result = auditor.audit_bundle(candidate, repository=repository)

    assert result.file_count == len(before) - 1
    assert candidate_bytes(candidate) == before


def test_validation_fixture_generator_rejects_nonhex_source_identities() -> None:
    with pytest.raises(ValueError, match="source identities"):
        fixture_generator.generate_fixture_tree(source_commit="z" * 40, source_tree="f" * 40)


@pytest.mark.parametrize(
    ("source_commit", "source_tree", "accepted"),
    (
        ("a" * 40, "b" * 40, True),
        ("z" * 40, "b" * 40, False),
        ("a" * 40, "z" * 40, False),
        ("0" * 40, "b" * 40, False),
        ("a" * 40, "0" * 40, False),
    ),
)
def test_validation_fixture_source_identity_guard_has_exact_acceptance_boundaries(
    source_commit: str,
    source_tree: str,
    accepted: bool,
) -> None:
    if accepted:
        fixture_generator.validate_source_identities(source_commit, source_tree)
    else:
        with pytest.raises(ValueError, match="source identities"):
            fixture_generator.validate_source_identities(source_commit, source_tree)


def test_validation_fixture_generator_check_rebuilds_the_retained_bytes() -> None:
    assert fixture_generator.main(["--check"]) == 0


def test_validation_fixture_generator_check_honors_explicit_source_identities() -> None:
    environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text()),
    )
    source_commit = cast(str, environment["source_commit"])
    alternate_commit = "a" * 40 if source_commit != "a" * 40 else "b" * 40

    assert (
        fixture_generator.main(
            [
                "--check",
                "--source-commit",
                alternate_commit,
                "--source-tree",
                cast(str, environment["source_tree"]),
            ]
        )
        == 1
    )


def test_validation_fixture_generator_main_requires_complete_ids_and_writes_to_its_owned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text()),
    )
    source_commit = cast(str, environment["source_commit"])
    source_tree = cast(str, environment["source_tree"])
    with pytest.raises(TrafficlabError, match="requires explicit source"):
        fixture_generator.main([])
    with pytest.raises(TrafficlabError, match="requires explicit source"):
        fixture_generator.main(["--check", "--source-commit", source_commit])

    output = tmp_path / "owned-fixture"
    monkeypatch.setattr(fixture_generator, "FIXTURE", output)
    assert fixture_generator.main(["--source-commit", source_commit, "--source-tree", source_tree]) == 0
    assert len(candidate_bytes(output)) == 232


def test_validation_fixture_retains_the_complete_232_file_evidence_inventory() -> None:
    assert len(candidate_bytes(VALIDATION_STUDY_CANDIDATE)) == 232


def test_historic_schema_one_workload_oracle_retains_the_measured_short_transfer() -> None:
    """The checked r3 result remains bound to its 256 KiB measured protocol."""

    short, streaming, bursty = study._historic_schema_one_workload_argvs()  # pyright: ignore[reportPrivateUsage]

    assert "--user-agent" not in short
    assert "0-262143" in short
    assert "262144" in short
    assert "0-1048575" not in short
    assert "1048576" not in short
    assert "0-4194303" in streaming
    assert "--parallel" in bursty
    assert study._expected_transfers("short") == ((0, 1_048_575, "short.headers"),)  # pyright: ignore[reportPrivateUsage]
    assert study._expected_transfers("short", historic_schema_one_result=True) == (  # pyright: ignore[reportPrivateUsage]
        (0, 262_143, "short.headers"),
    )
    assert study._workload_widths("short") == (0.001, 0.01)  # pyright: ignore[reportPrivateUsage]
    assert study._workload_widths("short", historic_schema_one_result=True) == (  # pyright: ignore[reportPrivateUsage]
        0.001,
        0.01,
    )


@pytest.mark.parametrize("workload", ("short", "streaming"))
def test_historic_schema_one_result_does_not_follow_current_workload_metadata(
    monkeypatch: pytest.MonkeyPatch,
    workload: study.WorkloadName,
) -> None:
    """The sole preserved result is bound to its complete primary and reproduction profiles."""

    current_workload_specs = study.workload_specs

    def changed_current_workloads(url: str) -> tuple[study.WorkloadSpec, ...]:
        return tuple(
            replace(
                specification,
                workload_timeout_seconds=36.0,
                total_timeout_seconds=91.0,
                multiscale_widths_seconds=(0.002, 0.02),
            )
            if specification.name == workload
            else specification
            for specification in current_workload_specs(url)
        )

    monkeypatch.setattr(study, "workload_specs", changed_current_workloads)
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()

    assert study.parse_study_results(content, repository_root=ROOT).protocol["study_id"] == (
        "validation-study-20260814-ovh-r3"
    )


def test_historic_descriptive_accepts_legacy_shape_without_weakening_current() -> None:
    """Only the exact retained historic result may omit recomputed bootstrap evidence."""
    observations = [1, 2, 4]
    current = study.descriptive_statistics(observations)
    historic = copy.deepcopy(current)
    historic.pop("bootstrap")

    assert (
        study._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            historic,
            name="historic descriptive",
            observations=observations,
            historic_schema_one_result=True,
        )
        == historic
    )
    assert (
        study._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            historic,
            name="historic descriptive without sources",
            historic_schema_one_result=True,
        )
        == historic
    )
    assert (
        study._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            current,
            name="current descriptive without sources",
        )
        == current
    )
    with pytest.raises(ValueError, match="bootstrap"):
        study._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            historic,
            name="current descriptive",
            observations=observations,
        )


def test_checked_study_result_uses_canonical_fresh_simulation_records() -> None:
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    document = cast(dict[str, object], json.loads(content))
    capability = cast(dict[str, object], cast(dict[str, object], document["protocol"])["capability"])
    argv = cast(list[str], capability["argv"])
    assert "--user-agent" not in argv
    result = study.parse_study_results(content, repository_root=ROOT)

    assert b'"fresh_simulation"' in content
    assert b'"held_out"' not in content
    assert study.render_study_results(result) == content

    near_miss = copy.deepcopy(document)
    near_miss_capability = cast(dict[str, object], cast(dict[str, object], near_miss["protocol"])["capability"])
    near_miss_argv = cast(list[str], near_miss_capability["argv"])
    near_miss_argv[near_miss_argv.index("--max-time") + 1] = "31"
    with pytest.raises(ValueError, match="capability argv"):
        study.parse_study_results(
            json.dumps(near_miss, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=ROOT,
        )

    workload_near_miss = copy.deepcopy(document)
    first_workload = cast(
        dict[str, object], cast(list[object], cast(dict[str, object], workload_near_miss["protocol"])["workloads"])[0]
    )
    workload_argv = cast(list[str], first_workload["argv"])
    workload_argv[workload_argv.index("--max-time") + 1] = "31"
    with pytest.raises(ValueError, match="short workload definition"):
        study.parse_study_results(
            json.dumps(workload_near_miss, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=ROOT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("current-capability", "capability argv"),
        ("current-workload", "short workload definition"),
        ("all-current", "capability argv"),
        ("wrong-study-id", "historic schema-1 protocol identity"),
        ("wrong-url", "historic schema-1 protocol identity"),
    ),
)
def test_historic_schema_one_protocol_is_one_atomic_identity(mutation: str, message: str) -> None:
    """The sole legacy result cannot combine independent current and historic command projections."""
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    document = copy.deepcopy(cast(dict[str, object], json.loads(content)))
    protocol = cast(dict[str, object], document["protocol"])
    study_id = cast(str, protocol["study_id"])
    url = cast(str, protocol["url"])
    capability = cast(dict[str, object], protocol["capability"])
    workloads = cast(list[dict[str, object]], protocol["workloads"])
    current_capability = study._expected_capability_argv(  # pyright: ignore[reportPrivateUsage]
        study_id,
        url,
    )
    current_workloads = study.workload_specs(url)

    if mutation in {"current-capability", "all-current"}:
        capability["argv"] = list(current_capability)
    if mutation in {"current-workload", "all-current"}:
        workloads[0]["argv"] = list(current_workloads[0].argv)
    if mutation == "all-current":
        for workload, current in zip(workloads, current_workloads, strict=True):
            workload["argv"] = list(current.argv)
    if mutation == "wrong-study-id":
        protocol["study_id"] = "legacy-study"
    if mutation == "wrong-url":
        protocol["url"] = "https://downloads.example.test/other.bin"

    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    with pytest.raises(ValueError, match=message):
        study.parse_study_results(invalid, repository_root=ROOT)


def test_current_protocol_rejects_a_capability_projection_without_the_package_user_agent() -> None:
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    current = cast(dict[str, object], json.loads(content))
    environment = cast(dict[str, object], current["environment"])
    environment["git_commit"] = "c" * 40

    capability = cast(dict[str, object], cast(dict[str, object], current["protocol"])["capability"])
    argv = cast(list[str], capability["argv"])
    assert "--user-agent" not in argv

    with pytest.raises(ValueError, match="capability argv"):
        study.parse_study_results(
            json.dumps(current, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=ROOT,
        )


def test_study_held_out_evaluator_uses_the_independent_window_with_the_fixed_training_model() -> None:
    """The study-only boundary evaluates a frozen training model without weakening ordinary stage lineage checks."""
    fixture = FIT_FIXTURE
    config = load_configuration_pair(fixture / "experiment.toml").realized
    metadata = parse_capture_metadata(CAPTURE_BYTES, source=fixture / "capture.json")
    original = parse_pcapng_bytes(REFERENCE_BYTES, metadata, source=fixture / "reference.pcapng")
    independent = tuple(
        TraceEvent(event.timestamp, event.direction, event.frame_length + (1 if index == 1 else 0))
        for index, event in enumerate(original)
    )
    independent_bytes = encode_pcapng(independent, metadata)

    result = study.evaluate_study_held_out(
        model_content=(fixture / "best_model.json").read_bytes(),
        model_source=fixture / "best_model.json",
        config=config,
        capture_content=CAPTURE_BYTES,
        capture_source=fixture / "capture.json",
        reference_content=independent_bytes,
        reference_source=Path("held_out/reference.pcapng"),
    )

    comparison = parse_comparison_result(result.comparison_json)
    assert result.seed == 97
    assert result.reference_identity.sha256 != result.training_model.reference_identity.sha256
    assert comparison.input_identities is not None
    assert result.generated_identity == comparison.input_identities.as_content_identities()["generated_pcapng"]
    assert comparison.methods.keys() == study.PUBLISHED_METHOD_ORDER

    with pytest.raises(TrafficlabError, match="independent held-out reference"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=REFERENCE_BYTES,
            reference_source=fixture / "reference.pcapng",
        )

    with pytest.raises(TypeError, match="ExperimentConfig"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=cast(Any, object()),
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=independent_bytes,
            reference_source=Path("held_out/reference.pcapng"),
        )

    with pytest.raises(TrafficlabError, match="final seed"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config.model_copy(update={"run": config.run.model_copy(update={"final_seed": 98})}),
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=independent_bytes,
            reference_source=Path("held_out/reference.pcapng"),
        )

    longer_window = tuple(
        TraceEvent(
            event.timestamp + (1.0 if index == len(independent) - 1 else 0.0), event.direction, event.frame_length
        )
        for index, event in enumerate(independent)
    )
    shorter_window = tuple(
        TraceEvent(event.timestamp * 0.8, event.direction, event.frame_length) for event in independent
    )
    for name, events in (("short", shorter_window), ("long", longer_window)):
        _normalized, held_out_window = normalize_reference(events)
        evaluation = study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=encode_pcapng(events, metadata),
            reference_source=Path(f"held_out/{name}-window.pcapng"),
        )
        assert held_out_window != result.training_model.observation_window_seconds
        assert evaluation.observation_window_seconds == held_out_window
        assert evaluation.training_model == result.training_model
        assert evaluation.training_model_identity == result.training_model_identity
        assert evaluation.seed == result.training_model.final_seed
        assert evaluation.training_model.final_limits == result.training_model.final_limits


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
        argv = list(study.prerequisite_command_argv(kind, study_id=study_id, url=url))
        tests = study.prerequisite_junit_counts(values["junit"])
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
            "target_image_id": f"sha256:{study.TARGET_REFERENCE.rsplit(':', 1)[-1]}",
            "target_image_reference": study.TARGET_REFERENCE,
            "uv_lock_identity": identify_bytes(b"locked\n").as_dict(),
        },
        "schema_version": 3,
        "study_id": study_id,
        "url": url,
    }

    rendered = study.render_retained_prerequisites(document)
    parsed = study.parse_retained_prerequisites(rendered)

    assert study.render_retained_prerequisites(parsed) == rendered
    commands = cast(list[dict[str, object]], parsed["commands"])
    assert commands[0]["tests"] == {"errors": 0, "failed": 0, "passed": 3, "skipped": 0, "total": 3}


def test_offline_auditor_binds_the_environment_to_the_relocated_git_and_image_locks(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment["source_commit"] = "b" * 40
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


def test_complete_fixture_freezes_training_model_selection_and_bidirectional_variation(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_text(encoding="utf-8")))
    report_inputs = cast(dict[str, object], json.loads((candidate / "report_inputs.json").read_text(encoding="utf-8")))

    selection = cast(dict[str, object], protocol["model_selection"])
    assert protocol["schema_version"] == 3
    assert "natural_variation_windows" not in protocol
    assert selection["rule"] == "highest_best_fitness_then_lowest_repeat"
    assert {cast(dict[str, object], value)["workload"] for value in cast(list[object], selection["selected"])} == {
        "short",
        "streaming",
        "bursty",
    }
    for row in cast(list[object], report_inputs["natural_variation"]):
        document = cast(dict[str, object], row)
        assert set(document) == {"pairs", "symmetric_mean", "workload"}
        for pair in cast(list[object], document["pairs"]):
            assert set(cast(dict[str, object], pair)) == {
                "forward",
                "left_repeat",
                "reverse",
                "right_repeat",
                "symmetric_mean",
            }
            for field in ("forward", "reverse", "symmetric_mean"):
                score = cast(dict[str, object], cast(dict[str, object], pair)[field])
                assert set(score) == {"aggregate", "methods"}
                assert type(score["aggregate"]) is float
                methods = cast(dict[str, object], score["methods"])
                assert tuple(methods) == study.PUBLISHED_METHOD_ORDER
                assert all(type(methods[method]) is float for method in study.PUBLISHED_METHOD_ORDER)

    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate


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
        study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

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
        study.prerequisite_junit_counts(b"<unexpected/>")
    with pytest.raises(ValueError, match="prerequisite kind"):
        study.prerequisite_command_argv(
            "unsupported", study_id="fixture-study", url="https://downloads.example.test/object.bin"
        )
    with pytest.raises(ValueError, match="prerequisite kind"):
        study.validate_frozen_prerequisite_command(
            "unsupported",
            (),
            0,
            {},
            study_id="fixture-study",
            url="https://downloads.example.test/object.bin",
        )
    with pytest.raises(ValueError, match="canonical sorted compact"):
        study.parse_retained_prerequisites(noncanonical)


def test_validation_study_gitignore_tracks_only_accepted_run_logs() -> None:
    """Accepted evidence logs remain trackable while candidates and ordinary logs stay ignored."""

    def ignored(path: str) -> bool:
        result = subprocess.run(
            ("git", "check-ignore", "-q", "--", path),
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        assert result.returncode in (0, 1)
        return result.returncode == 0

    assert not ignored("examples/validation_study/evidence/study-1/training/short/r1/run.log")
    assert ignored("examples/validation_study/evidence/.candidates/study-1/training/short/r1/run.log")
    assert ignored("runs/study-1/run.log")
