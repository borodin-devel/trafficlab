import copy
import json
from pathlib import Path
from typing import cast

import pytest
import tomli_w

import trafficlab.preflight as preflight_module
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.preflight import (
    CaptureEnvironmentIdentity,
    DockerPreflight,
    PreflightFinding,
    PreflightReport,
    open_or_prepare_experiment,
    run_preflight,
)

pytestmark = pytest.mark.integration


def _write_config(path: Path, data: dict[str, object]) -> None:
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def test_open_or_prepare_reuses_only_the_authoritative_unchanged_run(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Creating a second run would break sequential preflight and capture commands for one experiment."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)

    first = open_or_prepare_experiment(experiment_path)
    snapshot_path = first.run_directory / "experiment.toml"
    log_path = first.run_directory / "run.log"
    snapshot_before = snapshot_path.read_bytes()
    log_before = log_path.read_bytes()
    reopened = open_or_prepare_experiment(experiment_path)

    assert reopened.config == first.config
    assert reopened.run_directory == first.run_directory
    assert reopened.source == experiment_path
    assert snapshot_before == render_effective_config(first.config)
    assert snapshot_path.read_bytes() == snapshot_before
    assert log_path.read_bytes() == log_before
    assert all(finding.ok for finding in reopened.report.findings)


def test_existing_run_must_be_writable_without_mutation_or_docker_access(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Accepting a read-only prepared run would make later run.log and artifact publication fail unexpectedly."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    prepared = open_or_prepare_experiment(experiment_path)
    before = {path.name: path.read_bytes() for path in prepared.run_directory.iterdir() if path.is_file()}
    checked: list[Path] = []

    def unwritable(path: Path) -> bool:
        checked.append(path)
        return False

    class _NoDocker:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"unwritable reuse touched Docker attribute {name}")

    with pytest.raises(TrafficlabError, match="run_directory:.*not writable") as caught:
        run_preflight(
            experiment_path,
            config_only=False,
            docker=cast(DockerPreflight, _NoDocker()),
            writable=unwritable,
        )

    assert "make the existing run directory writable" in caught.value.corrective_action
    assert checked == [prepared.run_directory]
    after = {path.name: path.read_bytes() for path in prepared.run_directory.iterdir() if path.is_file()}
    assert after == before


def test_existing_run_path_must_still_be_a_directory(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """Treating a file as a reusable run would leak a raw artifact or directory error later."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = Path(cast(str, cast(dict[str, object], valid_config_data["run"])["directory"]))
    run_directory.write_bytes(b"not a directory")
    _write_config(experiment_path, valid_config_data)

    with pytest.raises(TrafficlabError, match="run path is not a directory"):
        open_or_prepare_experiment(experiment_path)

    assert run_directory.read_bytes() == b"not a directory"


@pytest.mark.parametrize("corruption", ["missing_snapshot", "malformed_snapshot", "mismatch", "malformed_log"])
def test_open_or_prepare_rejects_a_non_authoritative_run_without_mutation(
    valid_config_data: dict[str, object], tmp_path: Path, corruption: str
) -> None:
    """Repairing an ambiguous existing run in place could silently mix two experiments."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    prepared = open_or_prepare_experiment(experiment_path)
    snapshot = prepared.run_directory / "experiment.toml"
    log = prepared.run_directory / "run.log"

    if corruption == "missing_snapshot":
        snapshot.unlink()
    elif corruption == "malformed_snapshot":
        snapshot.write_bytes(b"[run\n")
    elif corruption == "mismatch":
        changed = copy.deepcopy(valid_config_data)
        cast(dict[str, object], changed["run"])["master_seed"] = 999
        _write_config(experiment_path, changed)
    else:
        log.write_bytes(b"not-json\n")

    before = {path.name: path.read_bytes() for path in prepared.run_directory.iterdir() if path.is_file()}
    with pytest.raises(TrafficlabError, match="existing run"):
        open_or_prepare_experiment(experiment_path)

    after = {path.name: path.read_bytes() for path in prepared.run_directory.iterdir() if path.is_file()}
    assert after == before


def test_run_preflight_config_only_never_touches_the_injected_docker_boundary(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A Docker access hidden inside config-only would violate its permanent offline contract."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)

    class _NoDocker:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"config-only touched Docker attribute {name}")

    prepared = run_preflight(experiment_path, config_only=True, docker=cast(DockerPreflight, _NoDocker()))

    assert prepared.run_directory.exists()
    assert len(prepared.report.findings) == 3


def test_run_preflight_preserves_local_findings_and_logs_the_direct_docker_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Dropping local evidence or the direct Docker reason would make a full-preflight failure ambiguous."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)

    class _UnavailableDocker:
        def info(self, *, deadline: float) -> object:
            assert deadline == 160.0
            raise TrafficlabError("daemon permission denied", corrective_action="grant Docker daemon access")

    with pytest.raises(TrafficlabError, match="docker_daemon: daemon permission denied") as caught:
        run_preflight(
            experiment_path,
            config_only=False,
            docker=cast(DockerPreflight, _UnavailableDocker()),
            clock=lambda: 100.0,
        )

    assert caught.value.corrective_action == "grant Docker daemon access"
    run_directory = Path(cast(str, cast(dict[str, object], valid_config_data["run"])["directory"]))
    records = [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]
    assert records[-1] == {
        "detail": "daemon permission denied",
        "event": "preflight_check",
        "name": "docker_daemon",
        "ok": False,
        "stage": "preflight",
    }


def test_run_preflight_propagates_resolved_environment_identity(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    identity = CaptureEnvironmentIdentity(
        host_architecture="x86_64",
        target_reference="curlimages/curl:8.10.1",
        target_content_id="sha256:" + ("c" * 64),
        capture_reference="trafficlab-capture:phase3-test",
        capture_content_id="sha256:854b21990ba8c1a566c0b5f5abaef8d72840cbf4a0ebb22230da7127462ed602",
        capture_tool_version="4.0.17",
    )

    def successful_check(
        config: ExperimentConfig,
        docker: object,
        *,
        deadline: float,
        clock: object,
    ) -> PreflightReport:
        del docker, clock
        assert deadline == 160.0
        return PreflightReport(
            config=config,
            findings=(PreflightFinding("docker_identity", True, "images resolved"),),
            environment_identity=identity,
        )

    monkeypatch.setattr(preflight_module, "check_docker", successful_check)

    prepared = run_preflight(
        experiment_path,
        config_only=False,
        docker=cast(DockerPreflight, object()),
        clock=lambda: 100.0,
    )

    assert prepared.report.environment_identity == identity
    records = [
        json.loads(line) for line in (prepared.run_directory / "run.log").read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1] == {
        "capture_content_id": identity.capture_content_id,
        "capture_reference": identity.capture_reference,
        "capture_tool_version": identity.capture_tool_version,
        "event": "capture_environment_identity",
        "host_architecture": identity.host_architecture,
        "stage": "preflight",
        "target_content_id": identity.target_content_id,
        "target_reference": identity.target_reference,
    }
