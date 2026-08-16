from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from scripts import audit_validation_study as auditor
from scripts import run_validation_study as study
from trafficlab.artifacts import append_run_log
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import compare_experiment
from trafficlab.fitting import fit_experiment
from trafficlab.generation import generate_experiment
from trafficlab.genetic.types import METHOD_ORDER
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, run_experiment

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_FIT_FIXTURE = _ROOT / "examples" / "data" / "fit"
_CAPTURE_BYTES = (_FIT_FIXTURE / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_FIXTURE / "reference.pcapng").read_bytes()
_AUDIT_FIXTURE = _ROOT / "tests" / "fixtures" / "validation_study_candidate"


def _copy_audit_fixture_to_clean_checkout(tmp_path: Path) -> tuple[Path, Path]:
    source_environment = cast(dict[str, object], json.loads((_AUDIT_FIXTURE / "environment.json").read_text(encoding="utf-8")))
    repository = tmp_path / "relocated-repository"
    subprocess.run(
        ("git", "clone", "--no-hardlinks", "--no-checkout", str(_ROOT), str(repository)),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "checkout", "--detach", cast(str, source_environment["source_commit"])),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    candidate = repository / "candidate"
    shutil.copytree(_AUDIT_FIXTURE, candidate)
    return repository, candidate


def test_validation_study_extraction_uses_real_three_family_artifacts_fresh_seed_and_lineage(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    study_id = "study-1"
    run_id = "01-short-r1"
    url = "https://downloads.example.test/object.bin"
    workload = study.workload_specs(url)[0]
    (repository_root / "examples" / "validation_study" / ".study-work" / "mount" / study_id).mkdir(parents=True)
    config = study.build_base_config(
        workload,
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        capture_image_id=f"sha256:{'d' * 64}",
    )
    experiment_path = repository_root / "runs" / "validation_study" / study_id / "realized-configs" / f"{run_id}.toml"
    study._render_realized_config(config, experiment_path)  # pyright: ignore[reportPrivateUsage]

    def capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
        metadata_path = prepared.run_directory / "capture.json"
        reference_path = prepared.run_directory / "reference.pcapng"
        metadata_path.write_bytes(_CAPTURE_BYTES)
        reference_path.write_bytes(_REFERENCE_BYTES)
        inspection = validate_capture_pair(metadata_path, reference_path, deadline=None)
        append_run_log(
            prepared.run_directory,
            {
                "event": "capture_published",
                "packet_count": inspection.packet_count,
                "path": str(reference_path),
                "project_name": "trafficlab-validation-study-integration",
                "reused": False,
                "stage": "capture",
            },
        )
        return CaptureResult(prepared.run_directory, reference_path, inspection.packet_count, 0, reused=False)

    result = run_experiment(
        experiment_path,
        dependencies=RunDependencies(
            open_or_prepare_experiment,
            capture,
            fit_experiment,
            generate_experiment,
            compare_experiment,
        ),
    )
    evidence_directory = (
        repository_root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / run_id
    )
    evidence_directory.mkdir(parents=True)
    header_bytes = b"HTTP/1.1 206 Response\r\nContent-Range: bytes 0-262143/4194304\r\nContent-Length: 262144\r\n\r\n"
    header_path = evidence_directory / "short.headers"
    header_path.write_bytes(header_bytes)
    header_path.chmod(0o600)
    transfer_responses: tuple[study.JsonObject, ...] = (
        {
            "transfer_index": 0,
            "requested_start": 0,
            "requested_end": 262143,
            "status": 206,
            "content_length": 262144,
            "content_range": "bytes 0-262143/4194304",
            "header_archive_path": header_path.relative_to(repository_root).as_posix(),
            "header_sha256": hashlib.sha256(header_bytes).hexdigest(),
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        },
    )
    run_spec = study.StudyRunSpec(
        1,
        run_id,
        "short",
        1,
        experiment_path,
        config.run.directory,
        evidence_directory,
    )

    record = study.extract_primary_record(
        repository_root,
        run_spec,
        workload,
        result,
        1.25,
        transfer_responses,
    )

    assert tuple(item["family"] for item in record.family_champions) == (
        "markov_renewal",
        "mmpp",
        "poisson_empirical",
    )
    assert all(item["selection_seeds"] == (17, 29) for item in record.family_champions)
    fresh_simulation = cast(study.JsonObject, study._thaw_json(record.fresh_simulation))  # pyright: ignore[reportPrivateUsage]
    fresh_simulation_score = cast(study.JsonObject, fresh_simulation["score"])
    methods = cast(study.JsonObject, fresh_simulation_score["methods"])
    artifact_sha256 = cast(
        study.JsonObject,
        study._thaw_json(record.artifact_sha256),  # pyright: ignore[reportPrivateUsage]
    )
    input_sha256 = result.comparison.input_sha256
    assert input_sha256 is not None
    assert fresh_simulation["seed"] == 97
    assert tuple(methods) == METHOD_ORDER
    assert artifact_sha256["capture.json"] == input_sha256["capture_json"]
    assert artifact_sha256["reference.pcapng"] == input_sha256["reference_pcapng"]
    assert artifact_sha256["generated.pcapng"] == input_sha256["generated_pcapng"]
    assert sorted(path.name for path in config.run.directory.iterdir()) == sorted(study.ARTIFACT_NAMES)


def test_audit_cli_accepts_only_a_reconstructed_offline_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository, candidate = _copy_audit_fixture_to_clean_checkout(tmp_path)

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

    assert auditor.main([str(candidate), "--repository", str(repository)]) == 0
    assert capsys.readouterr().out.startswith("validation-study-audit: accepted ")


def test_audit_script_main_reconstructs_a_relocated_fixture_without_a_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository, candidate = _copy_audit_fixture_to_clean_checkout(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(_ROOT / "scripts" / "audit_validation_study.py"),
            str(candidate),
            "--repository",
            str(repository),
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        runpy.run_path(str(_ROOT / "scripts" / "audit_validation_study.py"), run_name="__main__")

    assert exit_code.value.code == 0
    assert capsys.readouterr().out.startswith("validation-study-audit: accepted ")


def test_clean_checkout_auditor_rejects_a_candidate_bound_to_a_different_source_revision(tmp_path: Path) -> None:
    """The offline script must execute from a clean local checkout, not this worktree."""
    source_environment = cast(
        dict[str, object], json.loads((_AUDIT_FIXTURE / "environment.json").read_text(encoding="utf-8"))
    )
    source_commit = cast(str, source_environment["source_commit"])
    repository = tmp_path / "clean-checkout"
    subprocess.run(
        ("git", "clone", "--no-hardlinks", "--no-checkout", str(_ROOT), str(repository)),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "checkout", "--detach", source_commit),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    candidate = repository / "candidate"
    shutil.copytree(_AUDIT_FIXTURE, candidate)
    parent = subprocess.run(
        ("git", "rev-parse", "HEAD^"), cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment["source_commit"] = parent
    environment_path.write_text(
        json.dumps(environment, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    index = cast(dict[str, object], json.loads((candidate / "index.json").read_text(encoding="utf-8")))
    auditor.write_manifest(
        candidate,
        ownership=cast(dict[str, str], index["ownership"]),
        lineage=cast(dict[str, object], index["lineage"]),
    )

    wrapper = """
import os
import runpy
import socket
import subprocess
import sys
from pathlib import Path

checkout = Path.cwd().resolve()
original = Path(os.environ["TRAFFICLAB_ORIGINAL_ROOT"]).resolve()
import scripts.audit_validation_study as audit
import trafficlab
assert Path(audit.__file__).resolve().is_relative_to(checkout)
assert Path(trafficlab.__file__).resolve().is_relative_to(checkout)
original_read_bytes = Path.read_bytes
original_read_text = Path.read_text
def checked_read_bytes(path, *args, **kwargs):
    if path.resolve().is_relative_to(original):
        raise AssertionError("audit read the original worktree")
    return original_read_bytes(path, *args, **kwargs)
def checked_read_text(path, *args, **kwargs):
    if path.resolve().is_relative_to(original):
        raise AssertionError("audit read the original worktree")
    return original_read_text(path, *args, **kwargs)
Path.read_bytes = checked_read_bytes
Path.read_text = checked_read_text
def blocked_network(*args, **kwargs):
    raise AssertionError("audit attempted network access")
socket.socket = blocked_network
socket.create_connection = blocked_network
original_run = subprocess.run
def local_git_only(argv, *args, **kwargs):
    if tuple(argv[:1]) == ("git",):
        return original_run(argv, *args, **kwargs)
    raise AssertionError("audit attempted Docker or a subprocess")
subprocess.run = local_git_only
sys.argv = ["scripts/audit_validation_study.py", "candidate", "--repository", str(checkout)]
runpy.run_path(str(checkout / "scripts" / "audit_validation_study.py"), run_name="__main__")
"""
    environment_variables = dict(os.environ)
    environment_variables["PYTHONPATH"] = ""
    environment_variables["TRAFFICLAB_ORIGINAL_ROOT"] = str(_ROOT)
    completed = subprocess.run(
        ("uv", "run", "--locked", "--offline", "python", "-c", wrapper),
        cwd=repository,
        env=environment_variables,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "source commit" in completed.stderr
