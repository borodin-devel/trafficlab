import importlib
import importlib.metadata
import os
import runpy
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import trafficlab
import trafficlab.cli as cli
from trafficlab import __version__
from trafficlab.capture import CaptureResult
from trafficlab.errors import TrafficlabError


def test_version_is_project_version() -> None:
    assert __version__ == "0.1.0"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == "trafficlab 0.1.0\n"


def test_no_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err


def test_invalid_argument_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--not-a-real-option"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_main_uses_process_arguments(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["trafficlab", "--version"])
    assert cli.main() == 0
    assert capsys.readouterr().out == "trafficlab 0.1.0\n"


def test_main_handles_argument_parser_without_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class ParserWithoutExitCode:
        def parse_args(self, arguments: list[str]) -> None:
            raise SystemExit(None)

        def print_usage(self, file: object) -> None:
            del file

    monkeypatch.setattr(cli, "build_parser", lambda: ParserWithoutExitCode())
    assert cli.main(["anything"]) == 0


def test_capture_keyboard_interrupt_returns_documented_status(capsys: pytest.CaptureFixture[str]) -> None:
    """Letting an interrupted capture escape would turn a normal terminal action into a traceback."""

    def interrupted(_: Path) -> CaptureResult:
        raise KeyboardInterrupt

    assert cli.main(["capture", "experiment.toml"], capture=interrupted) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "capture: interrupted by user; inspect run.log and retry capture\n"


def test_entrypoint_raises_main_exit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: 4)
    with pytest.raises(SystemExit) as raised:
        cli.entrypoint()
    assert raised.value.code == 4


def test_trafficlab_error_preserves_correction_and_exit_code() -> None:
    error = TrafficlabError("bad configuration", corrective_action="fix the TOML", exit_code=7)
    assert str(error) == "bad configuration"
    assert error.corrective_action == "fix the TOML"
    assert error.exit_code == 7


def test_source_tree_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_distribution(_: str) -> str:
        raise PackageNotFoundError

    try:
        with monkeypatch.context() as patched:
            patched.setattr(importlib.metadata, "version", missing_distribution)
            reloaded = importlib.reload(trafficlab)
            assert reloaded.__version__ == "0.1.0"
            assert reloaded.USER_AGENT == (
                f"trafficlab/{reloaded.__version__} (+https://github.com/borodin-devel/trafficlab)"
            )
    finally:
        importlib.reload(trafficlab)
        importlib.reload(cli)


def test_module_entrypoint_supports_version_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "trafficlab", "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0
    assert result.stdout == "trafficlab 0.1.0\n"
    assert result.stderr == ""


def test_module_entrypoint_runs_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["trafficlab", "--version"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("trafficlab", run_name="__main__")
    assert raised.value.code == 0
