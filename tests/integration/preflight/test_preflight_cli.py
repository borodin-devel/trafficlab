import builtins
import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

import trafficlab
import trafficlab.capture as capture_package
import trafficlab.capture.docker as docker_package
import trafficlab.cli as cli
import trafficlab.preflight.stage as preflight_module
from trafficlab.cli import main
from trafficlab.common.config_io import load_experiment
from trafficlab.common.errors import TrafficlabError
from trafficlab.preflight.stage import PreparedExperiment

pytestmark = pytest.mark.integration


def _write_config(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def _configured_run_directory(data: dict[str, object]) -> Path:
    return Path(cast(str, cast(dict[str, object], data["run"])["directory"]))


def _run_installed_preflight(experiment_path: Path) -> subprocess.CompletedProcess[str]:
    installed_script = Path(sys.executable).with_name("trafficlab")
    return subprocess.run(
        [str(installed_script), "preflight", str(experiment_path), "--config-only"],
        check=False,
        capture_output=True,
        text=True,
    )


def _is_docker_adapter_module(name: str) -> bool:
    if name == "docker" or name.startswith("docker."):
        return True
    return name.startswith("trafficlab.capture.docker.")


def _is_docker_adapter_import(name: str, fromlist: tuple[str, ...] | None) -> bool:
    return _is_docker_adapter_module(name) or (
        name == "trafficlab.capture.docker" and bool({"compose", "image", "process"}.intersection(fromlist or ()))
    )


def _is_run_or_capture_import(name: str, fromlist: tuple[str, ...] | None) -> bool:
    return (
        name in {"trafficlab.pipeline.stage", "trafficlab.capture.stage"}
        or (name == "trafficlab.pipeline" and "stage" in (fromlist or ()))
        or (name == "trafficlab.capture" and "stage" in (fromlist or ()))
    )


@pytest.mark.parametrize(
    ("name", "fromlist", "expected"),
    [
        ("trafficlab.pipeline.stage", None, True),
        ("trafficlab.capture.stage", None, True),
        ("trafficlab.pipeline", ("stage",), True),
        ("trafficlab.capture", ("stage",), True),
        ("trafficlab.runner", None, False),
        ("trafficlab.capture.validation", None, False),
        ("trafficlab", ("runtime",), False),
        ("trafficlab", None, False),
    ],
)
def test_run_or_capture_import_guard_classifies_direct_fromlist_and_close_names(
    name: str,
    fromlist: tuple[str, ...] | None,
    expected: bool,
) -> None:
    """A guard that misses real imports or blocks close names cannot prove lazy CLI isolation."""
    assert _is_run_or_capture_import(name, fromlist) is expected


def _clear_docker_adapter_import_state(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(sys.modules):
        if name.startswith("trafficlab.capture.docker."):
            monkeypatch.delitem(sys.modules, name)
    for child in ("compose", "image", "process", "types"):
        monkeypatch.delattr(docker_package, child, raising=False)


def test_prepare_experiment_publishes_the_validated_effective_configuration(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Skipping any composition stage could publish an unchecked or irreproducible experiment."""
    experiment_path = tmp_path / "configs" / "experiment.toml"
    _write_config(experiment_path, valid_config_data)

    prepared = preflight_module.prepare_experiment(experiment_path)
    from trafficlab.common.config_io import load_configuration_pair

    pair = load_configuration_pair(experiment_path)

    assert prepared.source == experiment_path
    assert prepared.portable_config == pair.portable
    assert prepared.config == pair.realized
    assert prepared.report.config == prepared.config
    assert all(finding.ok for finding in prepared.report.findings)
    assert prepared.run_directory == prepared.config.run.directory
    assert load_experiment(prepared.run_directory / "experiment.toml") == prepared.config
    assert [
        json.loads(line) for line in (prepared.run_directory / "run.log").read_text(encoding="utf-8").splitlines()
    ] == [
        {
            "event": "effective_config_published",
            "path": str(prepared.run_directory / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(prepared.run_directory), "stage": "preflight"},
    ]


def test_prepare_experiment_failure_before_publication_leaves_no_run_directory(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Publishing before local checks finish would leave a run that was never ready."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "runs" / "must-not-exist"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": str(tmp_path / "missing"), "target": "/work/data", "read_only": True}
    ]
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, data)

    with pytest.raises(TrafficlabError, match="mounts: mount source missing is unavailable"):
        preflight_module.prepare_experiment(experiment_path)

    assert not run_directory.exists()


@pytest.mark.parametrize(
    "method_weights",
    [
        {"frame_size_ks": 0.0, "iat_ks": 1.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.1, "iat_ks": 0.2, "autocorrelation": 0.3, "multiscale_rate": 0.4},
    ],
    ids=("zero-weight", "mixed-weights"),
)
def test_config_only_cli_uses_production_python_api_without_subprocess_or_docker_import(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    method_weights: dict[str, float],
) -> None:
    """Routing config-only through an external command would violate the one-process API contract."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = "runs/config-only-weights"
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": "fixture-data", "target": "/work/data", "read_only": True}
    ]
    cast(dict[str, object], data["similarity"])["method_weights"] = method_weights
    experiment_path = tmp_path / "config" / "experiment.toml"
    (experiment_path.parent / "fixture-data").mkdir(parents=True)
    _write_config(experiment_path, data)
    from trafficlab.common.config_io import load_configuration_pair

    prepared_results: list[PreparedExperiment] = []

    def prepare(path: Path) -> PreparedExperiment:
        prepared = preflight_module.prepare_experiment(path)
        prepared_results.append(prepared)
        return prepared

    real_import = builtins.__import__

    def reject_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("config-only invoked a subprocess")

    def reject_eager_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] | None = None,
        level: int = 0,
    ) -> Any:
        if _is_docker_adapter_import(name, fromlist) or _is_run_or_capture_import(name, fromlist):
            raise AssertionError(f"config-only imported forbidden module {name}")
        return real_import(name, globals, locals, fromlist or (), level)

    _clear_docker_adapter_import_state(monkeypatch)
    monkeypatch.delitem(sys.modules, "trafficlab.capture.stage", raising=False)
    monkeypatch.delattr(capture_package, "stage", raising=False)
    monkeypatch.delitem(sys.modules, "trafficlab.pipeline.stage", raising=False)
    monkeypatch.delattr(trafficlab, "run", raising=False)
    assert {name for name in sys.modules if _is_docker_adapter_module(name)} == set()
    assert not hasattr(docker_package, "compose")
    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(subprocess, "Popen", reject_subprocess)
    monkeypatch.setattr(builtins, "__import__", reject_eager_import)

    reloaded_cli = importlib.reload(cli)
    assert reloaded_cli.main(["preflight", str(experiment_path), "--config-only"], prepare=prepare) == 0

    captured = capsys.readouterr()
    pair = load_configuration_pair(experiment_path)
    assert str(pair.realized.run.directory) in captured.out
    assert captured.err == ""
    assert prepared_results[0].portable_config == pair.portable
    published = load_experiment(pair.realized.run.directory / "experiment.toml")
    assert published == prepared_results[0].config
    assert published.similarity == pair.realized.similarity
    assert published.similarity.method_weights.model_dump() == method_weights
    assert tuple(published.similarity.method_weights.model_dump()) == (
        "frame_size_ks",
        "iat_ks",
        "autocorrelation",
        "multiscale_rate",
    )
    assert {name for name in sys.modules if _is_docker_adapter_module(name)} == set()
    assert not hasattr(docker_package, "compose")
    assert "trafficlab.pipeline.stage" not in sys.modules
    assert "trafficlab.capture.stage" not in sys.modules


def test_config_only_cli_starts_from_a_fresh_interpreter_without_loading_run_capture_or_docker(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    """Cached preflight state must not let a cold CLI import hide an eager Docker-adapter dependency."""
    experiment_path = tmp_path / "cold-config-only.toml"
    _write_config(experiment_path, valid_config_data)
    evidence_path = tmp_path / "cold-config-only.json"
    script = """
import json
import subprocess
import sys
from pathlib import Path

def reject_subprocess(*_args, **_kwargs):
    raise AssertionError("cold config-only invoked a subprocess")

subprocess.run = reject_subprocess
subprocess.Popen = reject_subprocess
from trafficlab.cli import main

status = main(["preflight", sys.argv[1], "--config-only"])
from trafficlab.preflight.stage import run_preflight
direct = run_preflight(Path(sys.argv[1]), config_only=True)
forbidden = sorted(
    name for name in sys.modules
    if name in {"trafficlab.pipeline.stage", "trafficlab.capture.stage"}
    or name.startswith("trafficlab.capture.docker.")
)
Path(sys.argv[2]).write_text(
    json.dumps({"direct_run_directory": str(direct.run_directory), "forbidden": forbidden, "status": status}),
    encoding="utf-8",
)
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(experiment_path), str(evidence_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"preflight: prepared {_configured_run_directory(valid_config_data)}\n"
    assert result.stderr == ""
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == {
        "direct_run_directory": str(_configured_run_directory(valid_config_data)),
        "forbidden": [],
        "status": 0,
    }


def test_plain_preflight_uses_the_injected_full_boundary(
    valid_config_data: dict[str, object], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Routing plain preflight through the config-only callback would silently omit Docker readiness checks."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    plain_calls: list[Path] = []

    def prepare(path: Path) -> PreparedExperiment:
        raise AssertionError(f"plain preflight used the config-only callback for {path}")

    def full(path: Path) -> PreparedExperiment:
        plain_calls.append(path)
        return preflight_module.prepare_experiment(path)

    assert main(["preflight", str(experiment_path)], prepare=prepare, full_preflight=full) == 0

    captured = capsys.readouterr()
    assert captured.out == f"preflight: prepared {_configured_run_directory(valid_config_data)}\n"
    assert captured.err == ""
    assert plain_calls == [experiment_path]


@pytest.mark.parametrize(
    "arguments, expected_fragment",
    [
        (["run", "experiment.toml", "--config-only"], "unrecognized arguments"),
        (["preflight", "--config-only"], "the following arguments are required"),
        (["preflight", "experiment.toml", "--unknown"], "unrecognized arguments"),
        (["compare", "experiment.toml", "--config-only"], "unrecognized arguments"),
    ],
    ids=[
        "preflight-option-is-rejected-by-run",
        "missing-experiment",
        "unknown-preflight-option",
        "preflight-option-is-rejected-by-compare",
    ],
)
def test_cli_rejects_commands_and_arguments_outside_the_public_surface(
    arguments: list[str], expected_fragment: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Accepting an unimplemented command or malformed invocation would expose a false public API."""
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    assert expected_fragment in captured.err


def test_malformed_configuration_error_matches_python_api(tmp_path: Path) -> None:
    """Leaking loader exceptions would make an expected operator error look like a program crash."""
    experiment_path = tmp_path / "malformed.toml"
    experiment_path.write_text("[run\n", encoding="utf-8")

    with pytest.raises(TrafficlabError) as api_error:
        preflight_module.prepare_experiment(experiment_path)

    result = _run_installed_preflight(experiment_path)

    assert result.returncode == api_error.value.exit_code
    assert result.stdout == ""
    assert result.stderr == f"preflight: {api_error.value}; {api_error.value.corrective_action}\n"
    assert "Traceback" not in result.stderr


def test_local_preflight_error_matches_python_api(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """Formatting a different local failure at the CLI would make the two public entry points disagree."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "runs" / "must-not-exist"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": str(tmp_path / "missing"), "target": "/work/data", "read_only": True}
    ]
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, data)

    with pytest.raises(TrafficlabError) as api_error:
        preflight_module.prepare_experiment(experiment_path)

    result = _run_installed_preflight(experiment_path)

    assert result.returncode == api_error.value.exit_code
    assert result.stdout == ""
    assert result.stderr == f"preflight: {api_error.value}; {api_error.value.corrective_action}\n"
    assert not run_directory.exists()


def test_installed_trafficlab_script_runs_config_only_preflight(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A declared but uninstalled entry point would make the documented command unusable from a clean environment."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    api_config = load_experiment(experiment_path)

    result = _run_installed_preflight(experiment_path)

    assert result.returncode == 0, result.stderr
    assert str(_configured_run_directory(valid_config_data)) in result.stdout
    assert result.stderr == ""
    assert load_experiment(_configured_run_directory(valid_config_data) / "experiment.toml") == api_config


def test_checked_in_example_is_complete_and_uses_repository_relative_paths() -> None:
    """A stale or partial example would fail the first documented config-only experiment."""
    repository = Path(__file__).parents[3]
    example_path = repository / "examples" / "configs" / "minimal.toml"

    config = load_experiment(example_path)

    assert config.run.directory == repository / "runs" / "minimal"
    assert config.target.mounts[0].source == repository / "examples" / "data"
    assert config.target.mounts[0].target == "/work/data"
    assert config.target.mounts[0].read_only is True
    assert config.models.poisson_empirical is not None
    assert config.models.markov_renewal is not None
    assert config.models.mmpp is not None
    assert config.models.poisson_empirical.operator_values == (0.9, 1.0, 0.1)
    assert config.models.markov_renewal.operator_values == (0.9, 0.2, 0.1)
    assert config.models.mmpp.operator_values == (0.9, 0.25, 0.1)
    assert (repository / "examples" / "data" / "request.txt").read_text(encoding="utf-8") == ("trafficlab example\n")
