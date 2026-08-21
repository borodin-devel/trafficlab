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
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT, VALIDATION_STUDY_CANDIDATE
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.stage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.genetic.types import METHOD_ORDER
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.stage import generate_experiment
from trafficlab.preflight.stage import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, run_experiment

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[3]
_FIT_FIXTURE = PIPELINE_FIXTURE_ROOT / "fit"
_CAPTURE_BYTES = (_FIT_FIXTURE / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_FIXTURE / "reference.pcapng").read_bytes()
_AUDIT_FIXTURE = VALIDATION_STUDY_CANDIDATE


def _copy_audit_fixture_to_clean_checkout(tmp_path: Path) -> tuple[Path, Path]:
    source_environment = cast(
        dict[str, object], json.loads((_AUDIT_FIXTURE / "environment.json").read_text(encoding="utf-8"))
    )
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
    candidate = repository / "fixture-study"
    shutil.copytree(_AUDIT_FIXTURE, candidate)
    return repository, candidate


def _copy_audit_fixture_to_committed_destination(tmp_path: Path) -> tuple[Path, Path]:
    """Place the candidate in its real accepted path and commit only that evidence."""

    source_environment = cast(
        dict[str, object], json.loads((_AUDIT_FIXTURE / "environment.json").read_text(encoding="utf-8"))
    )
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
    destination = repository / "examples" / "validation_study" / "evidence" / "fixture-study"
    shutil.copytree(_AUDIT_FIXTURE, destination)
    subprocess.run(
        ("git", "add", "-f", destination.relative_to(repository).as_posix()),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        (
            "git",
            "-c",
            "user.email=validation-study@example.test",
            "-c",
            "user.name=Validation Study",
            "commit",
            "--quiet",
            "-m",
            "evidence: retain fixture study",
        ),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    return repository, destination


def test_clean_checkout_checks_the_pristine_tracked_validation_fixture(tmp_path: Path) -> None:
    """The checked fixture itself, including retained logs, must survive a no-hardlink clone."""

    repository = tmp_path / "pristine-current-checkout"
    subprocess.run(
        ("git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(_ROOT), str(repository)),
        check=True,
        capture_output=True,
    )
    subprocess.run(("git", "checkout", "--detach", "HEAD"), cwd=repository, check=True, capture_output=True)
    fixture = repository / "tests" / "fixtures" / "data" / "validation_study" / "candidate"
    environment = cast(dict[str, object], json.loads((fixture / "environment.json").read_text(encoding="utf-8")))
    clone_environment = dict(os.environ)
    clone_environment["PYTHONPATH"] = str(repository / "src")
    imported = subprocess.run(
        (sys.executable, "-c", "import trafficlab; print(trafficlab.__file__)"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        env=clone_environment,
    )
    assert Path(imported.stdout.strip()).is_relative_to(repository)
    completed = subprocess.run(
        (
            sys.executable,
            "scripts/generate_validation_study_fixture.py",
            "--check",
            "--source-commit",
            cast(str, environment["source_commit"]),
            "--source-tree",
            cast(str, environment["source_tree"]),
        ),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        env=clone_environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (repository / ".venv").exists()


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
    config = config.model_copy(update={"genetic": config.genetic.model_copy(update={"generation_count": 1})})
    assert config.genetic.generation_count == 1
    assert config.genetic.trial_seeds == (17, 29)
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
    header_bytes = b"HTTP/1.1 206 Response\r\nContent-Range: bytes 0-1048575/4194304\r\nContent-Length: 1048576\r\n\r\n"
    header_path = evidence_directory / "short.headers"
    header_path.write_bytes(header_bytes)
    header_path.chmod(0o600)
    transfer_responses: tuple[study.JsonObject, ...] = (
        {
            "transfer_index": 0,
            "requested_start": 0,
            "requested_end": 1048575,
            "status": 206,
            "content_length": 1048576,
            "content_range": "bytes 0-1048575/4194304",
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
    candidate = repository / "fixture-study"
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
import shutil
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
sys.argv = ["scripts/audit_validation_study.py", "fixture-study", "--repository", str(checkout)]
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


def test_clean_checkout_reconstructs_unmodified_candidate_and_checks_owned_fixture(tmp_path: Path) -> None:
    """A no-hardlink clone owns both the successful audit and supplied-ID fixture check."""
    repository, candidate = _copy_audit_fixture_to_clean_checkout(tmp_path)
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_text(encoding="utf-8")))
    source_commit = cast(str, environment["source_commit"])
    source_tree = cast(str, environment["source_tree"])
    clone_fixture = repository / "tests" / "fixtures" / "data" / "validation_study" / "candidate"
    wrapper = """
import os
import runpy
import shutil
import socket
import subprocess
import sys
from pathlib import Path

checkout = Path.cwd().resolve()
original = Path(os.environ["TRAFFICLAB_ORIGINAL_ROOT"]).resolve()
source_commit = os.environ["TRAFFICLAB_SOURCE_COMMIT"]
source_tree = os.environ["TRAFFICLAB_SOURCE_TREE"]
import scripts.audit_validation_study as audit
import scripts.generate_validation_study_fixture as generator
import trafficlab
assert Path(audit.__file__).resolve().is_relative_to(checkout)
assert Path(generator.__file__).resolve().is_relative_to(checkout)
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
allowed_git = {
    ("git", "rev-parse", "HEAD"),
    ("git", "rev-parse", "HEAD^{tree}"),
    ("git", "rev-parse", f"{source_commit}^{{tree}}"),
    ("git", "merge-base", "--is-ancestor", source_commit, source_commit),
    ("git", "diff", "--name-only", "-z", "--no-renames", f"{source_commit}..{source_commit}"),
    ("git", "show", f"{source_commit}:uv.lock"),
    ("git", "show", f"{source_commit}:docker/capture/image-lock.json"),
    ("git", "show", f"{source_commit}:examples/data/fit/experiment.toml"),
    ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"),
}
def local_git_only(argv, *args, **kwargs):
    if tuple(argv) in allowed_git:
        return original_run(argv, *args, **kwargs)
    if tuple(argv) == ("git", "check-ignore", "-z", "--stdin"):
        payload = kwargs.get("input")
        assert isinstance(payload, bytes) and payload.endswith(b"\\0")
        return original_run(argv, *args, **kwargs)
    raise AssertionError("audit attempted Docker or a non-local-Git subprocess")
subprocess.run = local_git_only
sys.argv = ["scripts/audit_validation_study.py", "fixture-study", "--repository", str(checkout)]
try:
    runpy.run_path(str(checkout / "scripts" / "audit_validation_study.py"), run_name="__main__")
except SystemExit as error:
    assert error.code == 0
else:
    raise AssertionError("audit script did not exit")
shutil.rmtree(checkout / "tests" / "fixtures" / "data" / "validation_study" / "candidate")
shutil.copytree(checkout / "fixture-study", checkout / "tests" / "fixtures" / "data" / "validation_study" / "candidate")
sys.argv = [
    "scripts/generate_validation_study_fixture.py",
    "--check",
    "--source-commit",
    source_commit,
    "--source-tree",
    source_tree,
]
try:
    runpy.run_path(str(checkout / "scripts" / "generate_validation_study_fixture.py"), run_name="__main__")
except SystemExit as error:
    assert error.code == 0
else:
    raise AssertionError("fixture generator did not exit")
"""
    environment_variables = dict(os.environ)
    environment_variables["PYTHONPATH"] = ""
    environment_variables["TRAFFICLAB_ORIGINAL_ROOT"] = str(_ROOT)
    environment_variables["TRAFFICLAB_SOURCE_COMMIT"] = source_commit
    environment_variables["TRAFFICLAB_SOURCE_TREE"] = source_tree
    completed = subprocess.run(
        ("uv", "run", "--locked", "--offline", "python", "-c", wrapper),
        cwd=repository,
        env=environment_variables,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "validation-study-audit: accepted " in completed.stdout
    assert (
        "validation-study fixture: checked-in paths and bytes match deterministic production output" in completed.stdout
    )
    candidate_bytes = {
        path.relative_to(candidate).as_posix(): path.read_bytes()
        for path in sorted(candidate.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    fixture_bytes = {
        path.relative_to(clone_fixture).as_posix(): path.read_bytes()
        for path in sorted(clone_fixture.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    assert len(candidate_bytes) == 232
    assert candidate_bytes == fixture_bytes


def test_clean_checkout_audits_a_committed_destination_and_rejects_later_science_changes(tmp_path: Path) -> None:
    """A checked evidence-only descendant is auditable, but scientific-code drift is not."""

    repository, destination = _copy_audit_fixture_to_committed_destination(tmp_path)
    candidate_argument = destination.relative_to(repository).as_posix()
    wrapper = f"""
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
allowed_git = {
        (
            ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"),
            ("git", "check-ignore", "-z", "--stdin"),
        )
    }
def local_git_only(argv, *args, **kwargs):
    if tuple(argv[:2]) in {{("git", "rev-parse"), ("git", "merge-base"), ("git", "diff"), ("git", "show")}} or tuple(argv) in allowed_git:
        if tuple(argv) == ("git", "check-ignore", "-z", "--stdin"):
            payload = kwargs.get("input")
            assert isinstance(payload, bytes) and payload.endswith(b"\\0")
        return original_run(argv, *args, **kwargs)
    raise AssertionError("audit attempted Docker or a non-local-Git subprocess")
subprocess.run = local_git_only
sys.argv = ["scripts/audit_validation_study.py", "{candidate_argument}", "--repository", str(checkout)]
runpy.run_path(str(checkout / "scripts" / "audit_validation_study.py"), run_name="__main__")
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = ""
    environment["TRAFFICLAB_ORIGINAL_ROOT"] = str(_ROOT)
    accepted = subprocess.run(
        ("uv", "run", "--locked", "--offline", "python", "-c", wrapper),
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert "validation-study-audit: accepted " in accepted.stdout

    science_path = repository / "src" / "trafficlab" / "__init__.py"
    science_path.write_text(
        science_path.read_text(encoding="utf-8") + "\n# post-evidence science drift\n", encoding="utf-8"
    )
    subprocess.run(
        ("git", "add", science_path.relative_to(repository).as_posix()), cwd=repository, check=True, capture_output=True
    )
    subprocess.run(
        (
            "git",
            "-c",
            "user.email=validation-study@example.test",
            "-c",
            "user.name=Validation Study",
            "commit",
            "--quiet",
            "-m",
            "test: mutate science source",
        ),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    rejected = subprocess.run(
        ("uv", "run", "--locked", "--offline", "python", "-c", wrapper),
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "non-evidence" in rejected.stderr
