from __future__ import annotations

import builtins
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import trafficlab.cli as cli_module
from tests.support.generation import (
    CAPTURE_BYTES,
    prepare_stage_run,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    Direction,
    TraceEvent,
    TrafficTrace,
    parse_capture_metadata,
)
from trafficlab.generation.stage import GenerationStageResult
from trafficlab.preflight.types import PreparedExperiment

pytestmark = pytest.mark.integration


def test_cli_generate_injected_dispatch_prints_exact_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generate must dispatch one in-process call and report its validated packet count and artifact path."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    calls: list[Path] = []
    result = GenerationStageResult(
        run_directory=run_directory,
        generated_path=run_directory / "generated.pcapng",
        trace=TrafficTrace.from_events(
            (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(1.0, Direction.INBOUND, 80))
        ),
        seed=54321,
        observation_window_seconds=1.0,
        reused=False,
    )

    def generate(path: Path) -> GenerationStageResult:
        calls.append(path)
        return result

    assert cli_module.main(["generate", str(experiment_path)], generate=generate) == 0

    captured = capsys.readouterr()
    assert calls == [experiment_path]
    assert captured.out == f"generate: packets=2 output={run_directory / 'generated.pcapng'}\n"
    assert captured.err == ""


def test_cli_generate_rejects_config_only_without_dispatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generation is never a configuration-only operation."""
    calls: list[Path] = []

    def generate(path: Path) -> GenerationStageResult:
        calls.append(path)
        raise AssertionError("invalid generate invocation dispatched")

    assert cli_module.main(["generate", str(tmp_path / "experiment.toml"), "--config-only"], generate=generate) == 2

    captured = capsys.readouterr()
    assert calls == []
    assert captured.out == ""
    assert "unrecognized arguments: --config-only" in captured.err


def test_cli_generate_formats_structured_errors_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = TrafficlabError("generation failed", corrective_action="repair the run", exit_code=9)

    def generate(_path: Path) -> GenerationStageResult:
        raise failure

    assert cli_module.main(["generate", str(tmp_path / "experiment.toml")], generate=generate) == 9

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "generate: generation failed; repair the run\n"
    assert "Traceback" not in captured.err


def test_cli_existing_command_isolated_from_generation_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Registering generate must not eagerly import its model and PCAPNG stage for existing commands."""
    real_import = builtins.__import__
    imported_generation: list[str] = []

    def observe_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "trafficlab.generation.stage" or name.startswith("trafficlab.generation.stage."):
            imported_generation.append(name)
        return real_import(name, globals, locals, fromlist, level)

    prepared = cast(
        PreparedExperiment,
        SimpleNamespace(run_directory=tmp_path / "run"),
    )
    monkeypatch.setattr(builtins, "__import__", observe_import)
    reloaded = importlib.reload(cli_module)

    def prepare(_path: Path) -> PreparedExperiment:
        return prepared

    assert (
        reloaded.main(
            ["preflight", str(tmp_path / "experiment.toml"), "--config-only"],
            prepare=prepare,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == f"preflight: prepared {tmp_path / 'run'}\n"
    assert captured.err == ""
    assert imported_generation == []


def test_cli_default_generate_lazily_imports_and_runs_stage(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_import = builtins.__import__
    imported_generation: list[str] = []

    def observe_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "trafficlab.generation.stage":
            imported_generation.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", observe_import)

    assert cli_module.main(["generate", str(experiment_path)]) == 0

    captured = capsys.readouterr()
    parsed = read_pcapng_bytes(
        (run_directory / "generated.pcapng").read_bytes(),
        parse_capture_metadata(CAPTURE_BYTES, source=run_directory / "capture.json"),
        source=run_directory / "generated.pcapng",
    )
    assert imported_generation == ["trafficlab.generation.stage"]
    assert captured.out == f"generate: packets={len(parsed)} output={run_directory / 'generated.pcapng'}\n"
    assert captured.err == ""
