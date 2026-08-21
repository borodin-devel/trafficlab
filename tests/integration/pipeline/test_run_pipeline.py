"""Non-Docker whole-run retry, corruption, and failure integration evidence."""

from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

import trafficlab.pipeline.stage as run_module
from trafficlab.artifacts.capture import load_or_recover_capture_pair
from trafficlab.artifacts.run_directory import create_run_directory
from trafficlab.capture.stage import CaptureResult
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.comparison.codec import sha256_bytes, similarity_settings_sha256
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.stage import FitStageResult, fit_experiment
from trafficlab.generation.stage import GenerationStageResult, generate_experiment
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.stage import PreparedExperiment, open_or_prepare_experiment

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[3]
_FIT_FIXTURE = _ROOT / "examples" / "data" / "fit"
_CAPTURE_BYTES = (_FIT_FIXTURE / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_FIXTURE / "reference.pcapng").read_bytes()


def _prepare_run(tmp_path: Path, name: str = "run", *, capture: bool = True) -> tuple[Path, Path, ExperimentConfig]:
    run_directory = tmp_path / name
    base = load_experiment(_FIT_FIXTURE / "experiment.toml")
    config = base.model_copy(update={"run": base.run.model_copy(update={"directory": run_directory})})
    experiment_path = tmp_path / f"{name}.toml"
    experiment_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    if capture:
        (run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
        (run_directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
    return experiment_path, run_directory, config


def _capture_boundary(
    calls: list[str], *, recaptured: list[bool] | None = None
) -> Callable[[Path, PreparedExperiment], CaptureResult]:
    def capture(path: Path, prepared: PreparedExperiment) -> CaptureResult:
        del path
        calls.append("capture")
        existing = load_or_recover_capture_pair(prepared.run_directory, deadline=None)
        repaired = existing is None
        if repaired:
            (prepared.run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
            (prepared.run_directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
            existing = load_or_recover_capture_pair(prepared.run_directory, deadline=None)
        assert existing is not None
        if recaptured is not None:
            recaptured.append(repaired)
        return CaptureResult(
            prepared.run_directory,
            prepared.run_directory / "reference.pcapng",
            existing.packet_count,
            0,
            reused=not repaired,
        )

    return capture


def _dependencies(calls: list[str], *, recaptured: list[bool] | None = None) -> RunDependencies:
    def preflight(path: Path) -> PreparedExperiment:
        calls.append("preflight")
        return open_or_prepare_experiment(path)

    def fit(path: Path) -> FitStageResult:
        calls.append("fit")
        return fit_experiment(path)

    def generate(path: Path) -> GenerationStageResult:
        calls.append("generate")
        return generate_experiment(path)

    def compare(path: Path) -> ComparisonResult:
        calls.append("compare")
        return compare_experiment(path)

    return RunDependencies(preflight, _capture_boundary(calls, recaptured=recaptured), fit, generate, compare)


def _run_offline(experiment_path: Path, *, recaptured: list[bool] | None = None) -> tuple[RunResult, list[str]]:
    calls: list[str] = []
    return run_experiment(experiment_path, dependencies=_dependencies(calls, recaptured=recaptured)), calls


def _records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]


def _artifact_bytes(run_directory: Path, names: tuple[str, ...]) -> dict[str, bytes]:
    return {name: (run_directory / name).read_bytes() for name in names}


def _canonical_json(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def test_real_offline_fit_generate_compare_has_one_window_lineage_seed_and_final_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing an ordinary offline boundary's W, lineage, final seed, or final limits must break this pipeline."""
    experiment_path, run_directory, config = _prepare_run(tmp_path)

    def forbid_run(*_args: object, **_kwargs: object) -> RunResult:
        raise AssertionError("offline analytical pipeline invoked run_experiment")

    monkeypatch.setattr(run_module, "run_experiment", forbid_run)
    fitted = fit_experiment(experiment_path)
    generated = generate_experiment(experiment_path)
    compared = compare_experiment(experiment_path)

    expected_hashes = {
        "capture_json": sha256_bytes(_CAPTURE_BYTES),
        "generated_pcapng": sha256_bytes((run_directory / "generated.pcapng").read_bytes()),
        "reference_pcapng": sha256_bytes(_REFERENCE_BYTES),
        "similarity_settings": similarity_settings_sha256(config.similarity),
    }
    assert (
        fitted.observation_window_seconds
        == generated.observation_window_seconds
        == compared.observation_window_seconds
        == 10.0
    )
    assert all(method.diagnostics["observation_window_seconds"] == 10.0 for method in compared.methods.values())
    assert compared.input_sha256 == expected_hashes
    assert generated.seed == config.run.final_seed == 97
    assert config.generation.final.max_packets == 1000
    assert config.generation.final.max_output_bytes == 2_000_000
    assert config.generation.final.max_wall_seconds == 10.0


def test_injected_capture_and_real_offline_stages_complete_one_process_run(tmp_path: Path) -> None:
    """Replacing any real offline later stage with fabricated results would evade end-to-end artifact validation."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)

    result, calls = _run_offline(experiment_path)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert result.run_directory == run_directory
    assert result.capture.reused is True
    assert result.fit.observation_window_seconds == result.generation.observation_window_seconds == 10.0
    assert result.comparison.input_sha256 is not None
    assert sorted(path.name for path in run_directory.iterdir()) == [
        "best_model.json",
        "capture.json",
        "checkpoint.json",
        "experiment.toml",
        "ga_history.csv",
        "generated.pcapng",
        "reference.pcapng",
        "run.log",
        "similarity.json",
    ]


def _run_installed(experiment_path: Path, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    installed = Path(sys.executable).with_name("trafficlab")
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    return subprocess.run(
        [str(installed), "run", str(experiment_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
    )


def test_installed_run_preflight_failure_matches_direct_coordinator_without_docker(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source-only route or altered error boundary would make the installed command disagree with Python."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "must-not-exist"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": str(tmp_path / "missing"), "target": "/work/data", "read_only": True}
    ]
    experiment_path = tmp_path / "invalid.toml"
    experiment_path.write_text(tomli_w.dumps(data), encoding="utf-8")
    working_directory = tmp_path / "installed-cwd"
    working_directory.mkdir()
    docker_bin = tmp_path / "docker-sentinel-bin"
    docker_bin.mkdir()
    docker_marker = tmp_path / "docker-was-invoked"
    docker_shim = docker_bin / "docker"
    docker_shim.write_text(
        "#!/bin/sh\n"
        f"> {shlex.quote(str(docker_marker))}\n"
        "echo 'installed run unexpectedly invoked Docker' >&2\n"
        "exit 99\n",
        encoding="utf-8",
    )
    docker_shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{docker_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    with pytest.raises(TrafficlabError) as raised:
        run_experiment(experiment_path)

    result = _run_installed(experiment_path, cwd=working_directory)

    assert result.returncode == raised.value.exit_code
    assert result.stdout == ""
    assert result.stderr == f"run: {raised.value}; {raised.value.corrective_action}\n"
    assert "mounts: mount source missing is unavailable" in result.stderr
    assert "make the named host source available to Docker" in result.stderr
    assert "Traceback" not in result.stderr
    assert not run_directory.exists()
    assert not docker_marker.exists()


@pytest.mark.parametrize("artifact", ["experiment.toml", "run.log"])
def test_corrupt_prepared_run_identity_is_rejected_without_mutation(tmp_path: Path, artifact: str) -> None:
    """Repreparing or appending after an authoritative prepared-run corruption would conceal mixed evidence."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    (run_directory / artifact).write_bytes(b"corrupt")
    before = _artifact_bytes(run_directory, tuple(path.name for path in run_directory.iterdir()))
    calls: list[str] = []

    with pytest.raises(TrafficlabError, match="existing run is not reusable"):
        run_experiment(experiment_path, dependencies=_dependencies(calls))

    assert calls == ["preflight"]
    assert _artifact_bytes(run_directory, tuple(before)) == before


@pytest.mark.parametrize(
    "state", ["absent", "metadata-only", "reference-only", "invalid-metadata", "invalid-reference"]
)
def test_absent_invalid_or_incomplete_capture_takes_recapture_boundary_then_real_offline_stages(
    tmp_path: Path, state: str
) -> None:
    """Trusting filename presence would let an incomplete or invalid pair bypass the recapture path."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path, capture=False)
    if state == "metadata-only":
        (run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
    elif state == "reference-only":
        (run_directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
    elif state == "invalid-metadata":
        (run_directory / "capture.json").write_bytes(b"{}")
        (run_directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
    elif state == "invalid-reference":
        (run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
        (run_directory / "reference.pcapng").write_bytes(b"not pcapng")
    recaptured: list[bool] = []

    result, calls = _run_offline(experiment_path, recaptured=recaptured)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert recaptured == [True]
    assert result.capture.reused is False
    assert (run_directory / "capture.json").read_bytes() == _CAPTURE_BYTES
    assert (run_directory / "reference.pcapng").read_bytes() == _REFERENCE_BYTES


def test_absent_checkpoint_with_resume_true_starts_fresh(tmp_path: Path) -> None:
    """Treating resume=true as requiring a checkpoint would prevent a new prepared run from fitting."""
    experiment_path, run_directory, config = _prepare_run(tmp_path)
    assert config.genetic.resume is True
    assert not (run_directory / "checkpoint.json").exists()

    result, _calls = _run_offline(experiment_path)

    assert result.fit.reused_best_model is False
    assert (run_directory / "checkpoint.json").is_file()


@pytest.mark.parametrize("kind", ["malformed", "incompatible"])
def test_bad_checkpoint_is_preserved_and_rejected(tmp_path: Path, kind: str) -> None:
    """Repairing or replacing a bad authoritative checkpoint would destroy resume diagnostics."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    _run_offline(experiment_path)
    checkpoint = run_directory / "checkpoint.json"
    if kind == "malformed":
        bad = b"{}"
    else:
        original = checkpoint.read_bytes()
        document = cast(dict[str, object], json.loads(original))
        cast(dict[str, object], document["experiment_identity"])["sha256"] = "0" * 64
        bad = _canonical_json(document)
    checkpoint.write_bytes(bad)
    best_before = (run_directory / "best_model.json").read_bytes()

    with pytest.raises(TrafficlabError):
        _run_offline(experiment_path)

    assert checkpoint.read_bytes() == bad
    assert (run_directory / "best_model.json").read_bytes() == best_before
    assert _records(run_directory)[-1]["event"] == "run_failed"
    assert _records(run_directory)[-1]["failed_stage"] == "fit"


@pytest.mark.parametrize("state", ["absent", "corrupt"])
def test_history_is_atomically_derived_from_authoritative_checkpoint(tmp_path: Path, state: str) -> None:
    """Trusting or independently reconstructing history would let it diverge from the checkpoint."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    _run_offline(experiment_path)
    checkpoint_before = (run_directory / "checkpoint.json").read_bytes()
    expected_history = (run_directory / "ga_history.csv").read_bytes()
    history = run_directory / "ga_history.csv"
    if state == "absent":
        history.unlink()
    else:
        history.write_bytes(b"corrupt history\n")

    result, _calls = _run_offline(experiment_path)

    assert result.fit.reused_best_model is True
    assert (run_directory / "checkpoint.json").read_bytes() == checkpoint_before
    assert history.read_bytes() == expected_history


def test_absent_best_model_is_rebuilt_from_terminal_checkpoint(tmp_path: Path) -> None:
    """Restarting selection instead of rebuilding the checkpoint winner would change deterministic evidence."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    _run_offline(experiment_path)
    expected = (run_directory / "best_model.json").read_bytes()
    checkpoint_before = (run_directory / "checkpoint.json").read_bytes()
    (run_directory / "best_model.json").unlink()

    result, _calls = _run_offline(experiment_path)

    assert result.fit.reused_best_model is False
    assert (run_directory / "best_model.json").read_bytes() == expected
    assert (run_directory / "checkpoint.json").read_bytes() == checkpoint_before


@pytest.mark.parametrize("kind", ["malformed", "different"])
def test_bad_or_different_best_model_is_preserved_and_rejected(tmp_path: Path, kind: str) -> None:
    """Replacing an existing nonidentical winner would erase evidence from a conflicting fit."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    _run_offline(experiment_path)
    path = run_directory / "best_model.json"
    if kind == "malformed":
        bad = b"{}"
    else:
        document = cast(dict[str, Any], json.loads(path.read_bytes()))
        cast(dict[str, Any], document["reference_identity"])["sha256"] = "0" * 64
        bad = _canonical_json(document)
    path.write_bytes(bad)

    with pytest.raises(TrafficlabError):
        _run_offline(experiment_path)

    assert path.read_bytes() == bad
    assert _records(run_directory)[-1]["failed_stage"] == "fit"


@pytest.mark.parametrize("state", ["absent", "malformed", "different", "identical"])
def test_generated_artifact_missing_corrupt_different_and_identical_policy(tmp_path: Path, state: str) -> None:
    """Generation must create only when absent, reject bad/different bytes, and reuse only exact validated bytes."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    _run_offline(experiment_path)
    path = run_directory / "generated.pcapng"
    expected = path.read_bytes()
    if state == "absent":
        path.unlink()
    elif state == "malformed":
        path.write_bytes(b"not pcapng")
    elif state == "different":
        path.write_bytes(_REFERENCE_BYTES)
    before = path.read_bytes() if path.exists() else None

    if state in {"malformed", "different"}:
        with pytest.raises(TrafficlabError):
            _run_offline(experiment_path)
        assert path.read_bytes() == before
        assert _records(run_directory)[-1]["failed_stage"] == "generate"
    else:
        result, _calls = _run_offline(experiment_path)
        assert path.read_bytes() == expected
        assert result.generation.reused is (state == "identical")


@pytest.mark.parametrize("state", ["absent", "malformed", "different", "identical"])
def test_similarity_artifact_missing_corrupt_different_and_identical_policy(tmp_path: Path, state: str) -> None:
    """Comparison must create only when absent, reject bad/different bytes, and reuse only exact validated bytes."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path)
    _run_offline(experiment_path)
    path = run_directory / "similarity.json"
    expected = path.read_bytes()
    if state == "absent":
        path.unlink()
    elif state == "malformed":
        path.write_bytes(b"{}")
    elif state == "different":
        path.write_bytes((_ROOT / "examples" / "data" / "similarity.json").read_bytes())
    before = path.read_bytes() if path.exists() else None

    if state in {"malformed", "different"}:
        with pytest.raises(TrafficlabError):
            _run_offline(experiment_path)
        assert path.read_bytes() == before
        assert _records(run_directory)[-1]["failed_stage"] == "compare"
    else:
        result, _calls = _run_offline(experiment_path)
        assert path.read_bytes() == expected
        assert result.comparison.input_sha256 is not None
        comparison_records = [
            record for record in _records(run_directory) if record.get("event") == "comparison_succeeded"
        ]
        assert comparison_records[-1]["reused"] is (state == "identical")


@pytest.mark.parametrize("failed_stage", ["preflight", "capture", "fit", "generate", "compare"])
def test_each_stage_failure_stops_exactly_and_preserves_prior_artifacts(tmp_path: Path, failed_stage: str) -> None:
    """Calling downstream work or rolling back prior artifacts would discard useful failure evidence."""
    experiment_path, run_directory, _config = _prepare_run(tmp_path, capture=False)
    authoritative_before = _artifact_bytes(run_directory, ("experiment.toml",))
    calls: list[str] = []
    ordinary = _dependencies(calls)
    expected_error = TrafficlabError(
        f"injected {failed_stage} failure",
        corrective_action=f"repair {failed_stage}",
        exit_code=31,
    )
    prior_at_failure: dict[str, bytes] = {}

    def fail_preflight(path: Path) -> PreparedExperiment:
        del path
        calls.append("preflight")
        raise expected_error

    def fail_capture(path: Path, prepared: PreparedExperiment) -> CaptureResult:
        del path, prepared
        calls.append("capture")
        raise expected_error

    def fail_fit(path: Path) -> FitStageResult:
        del path
        calls.append("fit")
        prior_at_failure.update(_artifact_bytes(run_directory, ("capture.json", "reference.pcapng")))
        raise expected_error

    def fail_generate(path: Path) -> GenerationStageResult:
        del path
        calls.append("generate")
        prior_at_failure.update(
            _artifact_bytes(
                run_directory,
                ("capture.json", "reference.pcapng", "checkpoint.json", "ga_history.csv", "best_model.json"),
            )
        )
        raise expected_error

    def fail_compare(path: Path) -> ComparisonResult:
        del path
        calls.append("compare")
        prior_at_failure.update(
            _artifact_bytes(
                run_directory,
                (
                    "capture.json",
                    "reference.pcapng",
                    "checkpoint.json",
                    "ga_history.csv",
                    "best_model.json",
                    "generated.pcapng",
                ),
            )
        )
        raise expected_error

    dependencies = RunDependencies(
        fail_preflight if failed_stage == "preflight" else ordinary.preflight,
        fail_capture if failed_stage == "capture" else ordinary.capture,
        fail_fit if failed_stage == "fit" else ordinary.fit,
        fail_generate if failed_stage == "generate" else ordinary.generate,
        fail_compare if failed_stage == "compare" else ordinary.compare,
    )
    stage_order = ["preflight", "capture", "fit", "generate", "compare"]
    expected_calls = stage_order[: stage_order.index(failed_stage) + 1]

    with pytest.raises(TrafficlabError) as raised:
        run_experiment(experiment_path, dependencies=dependencies)

    assert raised.value is expected_error
    assert raised.value.exit_code == 31
    assert calls == expected_calls
    assert _artifact_bytes(run_directory, ("experiment.toml",)) == authoritative_before
    assert _artifact_bytes(run_directory, tuple(prior_at_failure)) == prior_at_failure
    downstream = {
        "preflight": (
            "capture.json",
            "reference.pcapng",
            "checkpoint.json",
            "ga_history.csv",
            "best_model.json",
            "generated.pcapng",
            "similarity.json",
        ),
        "capture": (
            "capture.json",
            "reference.pcapng",
            "checkpoint.json",
            "ga_history.csv",
            "best_model.json",
            "generated.pcapng",
            "similarity.json",
        ),
        "fit": ("checkpoint.json", "ga_history.csv", "best_model.json", "generated.pcapng", "similarity.json"),
        "generate": ("generated.pcapng", "similarity.json"),
        "compare": ("similarity.json",),
    }
    assert all(not (run_directory / name).exists() for name in downstream[failed_stage])
    if failed_stage in {"fit", "generate", "compare"}:
        assert (run_directory / "capture.json").read_bytes() == _CAPTURE_BYTES
        assert (run_directory / "reference.pcapng").read_bytes() == _REFERENCE_BYTES
    if failed_stage in {"generate", "compare"}:
        assert (run_directory / "checkpoint.json").is_file()
        assert (run_directory / "best_model.json").is_file()
    if failed_stage == "compare":
        assert (run_directory / "generated.pcapng").is_file()
    failures = [record for record in _records(run_directory) if record.get("event") == "run_failed"]
    if failed_stage == "preflight":
        assert failures == []
    else:
        assert len(failures) == 1
        assert failures[0]["failed_stage"] == failed_stage
        assert failures[0]["detail"] == str(expected_error)
