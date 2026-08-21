"""CLI routing tests for the offline fit stage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import trafficlab.cli as cli_module
from trafficlab.capture.lineage import CaptureResult
from trafficlab.cli import main
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.stage import FitStageResult
from trafficlab.generation.stage import GenerationStageResult
from trafficlab.pipeline.types import RunResult
from trafficlab.preflight.types import PreparedExperiment


def _fit_result(*, reused: bool = False) -> FitStageResult:
    return cast(
        FitStageResult,
        SimpleNamespace(
            best_model=SimpleNamespace(family="mmpp"),
            best_model_path=Path("run/best_model.json"),
            outcome=SimpleNamespace(winner=SimpleNamespace(fitness=0.875)),
            reused_best_model=reused,
        ),
    )


def test_cli_fit_uses_injected_boundary_and_reports_result(capsys: pytest.CaptureFixture[str]) -> None:
    """The fit command must route directly to the Python stage boundary without Docker orchestration."""
    paths: list[Path] = []

    def fit(path: Path) -> FitStageResult:
        paths.append(path)
        return _fit_result()

    assert main(["fit", "experiment.toml"], fit=fit) == 0
    assert paths == [Path("experiment.toml")]
    assert capsys.readouterr().out == ("fit: family=mmpp fitness=0.875000 output=run/best_model.json reused=false\n")


def test_cli_fit_formats_trafficlab_errors_with_stage_and_action(capsys: pytest.CaptureFixture[str]) -> None:
    """A package error must keep the fit stage prefix, corrective action, and exact exit code."""

    def fail(_path: Path) -> FitStageResult:
        raise TrafficlabError("checkpoint incompatible", corrective_action="start a new run", exit_code=7)

    assert main(["fit", "experiment.toml"], fit=fail) == 7
    assert capsys.readouterr().err == "fit: checkpoint incompatible; start a new run\n"


def test_cli_fit_lazily_loads_the_production_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Leaving the boundary uninjected must import only the offline fitting stage selected by the command."""
    import trafficlab.fitting.stage as fitting

    paths: list[Path] = []

    def fit(path: Path) -> FitStageResult:
        paths.append(path)
        return _fit_result(reused=True)

    monkeypatch.setattr(fitting, "fit_experiment", fit)

    assert main(["fit", "experiment.toml"]) == 0
    assert paths == [Path("experiment.toml")]
    assert "reused=true" in capsys.readouterr().out


def _run_result() -> RunResult:
    return cast(
        RunResult,
        SimpleNamespace(
            run_directory=Path("/abs/run"),
            capture=SimpleNamespace(packet_count=10),
            fit=SimpleNamespace(outcome=SimpleNamespace(winner=SimpleNamespace(family="mmpp", fitness=0.8))),
            generation=SimpleNamespace(trace=tuple(range(12))),
            comparison=SimpleNamespace(aggregate_score=0.75),
        ),
    )


def test_cli_run_uses_one_injected_boundary_and_reports_locked_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A split dispatch or drifted summary would break the public whole-experiment boundary."""
    paths: list[Path] = []

    def run_once(path: Path) -> RunResult:
        paths.append(path)
        return _run_result()

    assert main(["run", "experiment.toml"], run=run_once) == 0
    assert paths == [Path("experiment.toml")]
    captured = capsys.readouterr()
    assert captured.out == (
        "run: family=mmpp fitness=0.800000 reference_packets=10 generated_packets=12 "
        "aggregate_score=0.750000 output=/abs/run\n"
    )
    assert captured.err == ""


def test_cli_run_formats_package_error_with_exact_status(capsys: pytest.CaptureFixture[str]) -> None:
    """Replacing a package status or action would hide the owning stage's actionable failure."""

    def fail(_path: Path) -> RunResult:
        raise TrafficlabError("capture unavailable", corrective_action="repair capture", exit_code=23)

    assert main(["run", "experiment.toml"], run=fail) == 23
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "run: capture unavailable; repair capture\n"


def test_cli_run_returns_130_after_interruption(capsys: pytest.CaptureFixture[str]) -> None:
    """An interactive interruption must remain distinguishable after capture owns lifecycle cleanup."""

    def interrupt(_path: Path) -> RunResult:
        raise KeyboardInterrupt

    assert main(["run", "experiment.toml"], run=interrupt) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "run: interrupted by user; inspect run.log and retry run\n"


@pytest.mark.parametrize(
    "arguments", [["run", "experiment.toml", "--config-only"], ["run", "experiment.toml", "--unknown"]]
)
def test_cli_run_rejects_options_outside_its_public_surface(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Accepting a run-level resume or arbitrary option would expose an unsupported coordinator protocol."""
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err


def test_cli_without_arguments_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    """Returning success for an empty invocation would make a missing command look completed."""
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: trafficlab")


def test_cli_uses_process_arguments_when_argv_is_not_injected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ignoring process argv would break the installed entrypoint despite working injected calls."""
    monkeypatch.setattr(cli_module.sys, "argv", ["trafficlab", "run", "experiment.toml"])

    assert main(run=lambda _path: _run_result()) == 0
    assert capsys.readouterr().out.startswith("run: family=mmpp")


def test_cli_plain_preflight_lazily_uses_production_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plain preflight must reach the full production boundary when no callback is injected."""
    import trafficlab.preflight.stage as preflight_module

    prepared = cast(PreparedExperiment, SimpleNamespace(run_directory=Path("/abs/run")))
    calls: list[tuple[Path, bool]] = []

    def full(path: Path, *, config_only: bool) -> PreparedExperiment:
        calls.append((path, config_only))
        return prepared

    monkeypatch.setattr(preflight_module, "run_preflight", full)

    assert main(["preflight", "experiment.toml"]) == 0
    assert calls == [(Path("experiment.toml"), False)]
    assert capsys.readouterr().out == "preflight: prepared /abs/run\n"


def _capture_result() -> CaptureResult:
    return cast(
        CaptureResult,
        SimpleNamespace(packet_count=10, reference_path=Path("/abs/run/reference.pcapng")),
    )


def test_cli_capture_uses_injected_boundary_and_reports_result(capsys: pytest.CaptureFixture[str]) -> None:
    """Capture must dispatch one Python boundary and report its published reference artifact."""
    paths: list[Path] = []

    def capture(path: Path) -> CaptureResult:
        paths.append(path)
        return _capture_result()

    assert main(["capture", "experiment.toml"], capture=capture) == 0
    assert paths == [Path("experiment.toml")]
    assert capsys.readouterr().out == "capture: packets=10 output=/abs/run/reference.pcapng\n"


def test_cli_capture_lazily_loads_production_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An uninjected capture command must import its production stage only after selection."""
    import trafficlab.capture.stage as capture_module

    paths: list[Path] = []

    def capture(path: Path) -> CaptureResult:
        paths.append(path)
        return _capture_result()

    monkeypatch.setattr(capture_module, "capture_experiment", capture)

    assert main(["capture", "experiment.toml"]) == 0
    assert paths == [Path("experiment.toml")]
    assert "packets=10" in capsys.readouterr().out


def test_cli_capture_returns_130_after_interruption(capsys: pytest.CaptureFixture[str]) -> None:
    """Capture interruption must retain its conventional status after lifecycle cleanup."""

    def interrupt(_path: Path) -> CaptureResult:
        raise KeyboardInterrupt

    assert main(["capture", "experiment.toml"], capture=interrupt) == 130
    assert capsys.readouterr().err == "capture: interrupted by user; inspect run.log and retry capture\n"


def test_cli_generate_uses_injected_boundary_and_reports_result(capsys: pytest.CaptureFixture[str]) -> None:
    """Generate must route one callback and count the returned final events."""
    result = cast(
        GenerationStageResult,
        SimpleNamespace(trace=(object(), object()), generated_path=Path("/abs/run/generated.pcapng")),
    )
    paths: list[Path] = []

    def generate(path: Path) -> GenerationStageResult:
        paths.append(path)
        return result

    assert main(["generate", "experiment.toml"], generate=generate) == 0
    assert paths == [Path("experiment.toml")]
    assert capsys.readouterr().out == "generate: packets=2 output=/abs/run/generated.pcapng\n"


def test_cli_run_lazily_loads_production_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An uninjected run command must import the coordinator only after run is selected."""
    import trafficlab.pipeline.stage as run_module

    paths: list[Path] = []

    def run(path: Path) -> RunResult:
        paths.append(path)
        return _run_result()

    monkeypatch.setattr(run_module, "run_experiment", run)

    assert main(["run", "experiment.toml"]) == 0
    assert paths == [Path("experiment.toml")]
    assert capsys.readouterr().out.startswith("run: family=mmpp")


def test_entrypoint_exits_with_main_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed console wrapper must propagate the exact main status."""
    monkeypatch.setattr(cli_module, "main", lambda: 17)

    with pytest.raises(SystemExit) as raised:
        cli_module.entrypoint()

    assert raised.value.code == 17
