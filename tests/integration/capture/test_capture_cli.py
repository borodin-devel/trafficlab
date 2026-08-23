import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest
import tomli_w

from trafficlab.capture.stage import CaptureResult
from trafficlab.cli import main
from trafficlab.common.errors import TrafficlabError

pytestmark = pytest.mark.integration


def _write_config(path: Path, data: dict[str, object]) -> None:
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def _run_installed_capture(
    experiment_path: Path,
    *,
    working_directory: Path,
    fake_docker_directory: Path,
    fixture_pcapng: Path,
    state_path: Path,
    argv_path: Path,
    parent_path: Path,
) -> subprocess.CompletedProcess[str]:
    process = _start_installed_capture(
        experiment_path,
        working_directory=working_directory,
        fake_docker_directory=fake_docker_directory,
        fixture_pcapng=fixture_pcapng,
        state_path=state_path,
        argv_path=argv_path,
        parent_path=parent_path,
    )
    stdout, stderr = process.communicate(timeout=30)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _start_installed_capture(
    experiment_path: Path,
    *,
    working_directory: Path,
    fake_docker_directory: Path,
    fixture_pcapng: Path,
    state_path: Path,
    argv_path: Path,
    parent_path: Path,
    mode: str = "normal",
) -> subprocess.Popen[str]:
    installed_script = Path(sys.executable).with_name("trafficlab")
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    environment["PATH"] = f"{fake_docker_directory}:{environment['PATH']}"
    environment["TRAFFICLAB_FAKE_PCAPNG"] = str(fixture_pcapng)
    environment["TRAFFICLAB_FAKE_DOCKER_STATE"] = str(state_path)
    environment["TRAFFICLAB_FAKE_DOCKER_ARGV"] = str(argv_path)
    environment["TRAFFICLAB_FAKE_DOCKER_PARENT"] = str(parent_path)
    environment["TRAFFICLAB_FAKE_DOCKER_MODE"] = mode
    return subprocess.Popen(
        [str(installed_script), "capture", str(experiment_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=working_directory,
        env=environment,
    )


def _isolated_installed_working_directory(root: Path) -> Path:
    working_directory = root / "installed-entry-cwd"
    source_shadow = working_directory / "src" / "trafficlab"
    source_shadow.mkdir(parents=True)
    (source_shadow / "__init__.py").write_text(
        'raise RuntimeError("installed entry point imported a working-directory source shadow")\n',
        encoding="utf-8",
    )
    return working_directory


def _write_fake_docker(bin_directory: Path, argv_path: Path) -> None:
    fake_docker = bin_directory / "docker"
    fake_docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$TRAFFICLAB_FAKE_DOCKER_ARGV"
tr '\\000' ' ' < "/proc/$PPID/cmdline" > "$TRAFFICLAB_FAKE_DOCKER_PARENT"
if [ "$1" = info ]; then
    printf '{"Architecture":"x86_64","OSType":"linux"}\\n'
    exit 0
fi
if [ "$1" = image ] && [ "$2" = inspect ]; then
    case "$3" in
      trafficlab-capture:*) content_id=sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c ;;
      *) content_id=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc ;;
    esac
    printf '[{"Id":"%s","Architecture":"amd64","Os":"linux","RepoDigests":[],"RepoTags":["%s"]}]\\n' "$content_id" "$3"
    exit 0
fi
if [ "$1" = image ]; then
    exit 0
fi
if [ "$1" = compose ] && [ "$2" = version ]; then
    printf '{"version":"v5.4.0"}\\n'
    exit 0
fi
compose_file=
previous=
for argument in "$@"; do
    if [ "$previous" = --file ]; then
        compose_file=$argument
    fi
    previous=$argument
done
case " $* " in
  *" config --format json "*) printf '{}\\n' ;;
  *" create capture "*)
    output=$(sed -n 's/.*"source": *"\\([^"]*\\)".*/\\1/p' "$compose_file" | head -n 1)
    printf '{\\n  "interface": "eth0",\\n  "target_mac": "02:42:ac:11:00:02"\\n}\\n' > "$output/capture.json"
    cp "$TRAFFICLAB_FAKE_PCAPNG" "$output/reference.pcapng.tmp"
    printf running > "$TRAFFICLAB_FAKE_DOCKER_STATE"
    ;;
  *" ps --all --format json target "*)
    case " $* " in
      *" --project-name trafficlab-capture-"*) production_capture=true ;;
      *) production_capture=false ;;
    esac
    if [ "$TRAFFICLAB_FAKE_DOCKER_MODE" = interrupt ] && [ "$production_capture" = true ]; then
        if [ "$(cat "$TRAFFICLAB_FAKE_DOCKER_STATE")" = target-killed ]; then
            printf '[{"ID":"target-id","Name":"fake-target","Service":"target","State":"exited","ExitCode":137}]\\n'
        else
            printf '[{"ID":"target-id","Name":"fake-target","Service":"target","State":"running","ExitCode":0}]\\n'
        fi
    else
        printf '[{"ID":"target-id","Name":"fake-target","Service":"target","State":"exited","ExitCode":0}]\\n'
    fi
    ;;
  *" ps --all --format json capture "*)
    if [ -f "$TRAFFICLAB_FAKE_DOCKER_STATE" ] && [ "$(cat "$TRAFFICLAB_FAKE_DOCKER_STATE")" = flushed ]; then
        printf '[{"ID":"capture-id","Name":"fake-capture","Service":"capture","State":"exited","ExitCode":0}]\\n'
    else
        printf '[{"ID":"capture-id","Name":"fake-capture","Service":"capture","State":"running","ExitCode":0}]\\n'
    fi
    ;;
  *" kill --signal SIGINT capture "*) printf flushed > "$TRAFFICLAB_FAKE_DOCKER_STATE" ;;
  *" kill target "*) printf target-killed > "$TRAFFICLAB_FAKE_DOCKER_STATE" ;;
  *" ps --all --format json "*) printf '[]\\n' ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    argv_path.write_text("", encoding="utf-8")


def test_capture_cli_dispatches_in_process_and_prints_published_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Replacing the capture route with a subprocess or changing its input loses the one-process stage contract."""
    experiment_path = tmp_path / "experiment.toml"
    reference_path = tmp_path / "run" / "reference.pcapng"
    calls: list[Path] = []

    def capture(path: Path) -> CaptureResult:
        calls.append(path)
        return CaptureResult(reference_path.parent, reference_path, packet_count=7, target_status=0)

    assert main(["capture", str(experiment_path)], capture=capture) == 0

    captured = capsys.readouterr()
    assert calls == [experiment_path]
    assert captured.out == f"capture: packets=7 output={reference_path}\n"
    assert captured.err == ""


def test_capture_cli_formats_expected_stage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dropping the corrective action makes a known capture failure needlessly hard to recover from."""
    experiment_path = tmp_path / "experiment.toml"

    def fail(_: Path) -> CaptureResult:
        raise TrafficlabError("capture image stopped", corrective_action="rebuild the capture image", exit_code=9)

    assert main(["capture", str(experiment_path)], capture=fail) == 9

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "capture: capture image stopped; rebuild the capture image\n"


def test_capture_unknown_option_does_not_start_capture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Parsing options after dispatch could start an unintended Docker lifecycle."""
    calls: list[Path] = []

    def capture(path: Path) -> CaptureResult:
        calls.append(path)
        raise AssertionError("capture must not start for invalid arguments")

    assert main(["capture", str(tmp_path / "experiment.toml"), "--config-only"], capture=capture) == 2

    captured = capsys.readouterr()
    assert calls == []
    assert captured.out == ""
    assert "unrecognized arguments: --config-only" in captured.err


def test_installed_capture_uses_direct_fake_docker_without_sudo_or_python(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Routing an installed capture through Python or sudo would violate the public Docker boundary."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    argv_path = tmp_path / "docker.argv"
    parent_path = tmp_path / "docker.parent"
    state_path = tmp_path / "docker.state"
    _write_fake_docker(bin_directory, argv_path)

    result = _run_installed_capture(
        experiment_path,
        working_directory=_isolated_installed_working_directory(tmp_path),
        fake_docker_directory=bin_directory,
        fixture_pcapng=Path(__file__).parents[3] / "examples" / "data" / "reference.pcapng",
        state_path=state_path,
        argv_path=argv_path,
        parent_path=parent_path,
    )

    run = cast(dict[str, object], valid_config_data["run"])
    run_directory = Path(cast(str, run["directory"]))
    recorded_argv = argv_path.read_text(encoding="utf-8").splitlines()
    docker_parent = parent_path.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"capture: packets=5 output={run_directory / 'reference.pcapng'}\n"
    assert result.stderr == ""
    assert recorded_argv
    assert all(line.startswith(("info", "image", "compose", "network", "volume")) for line in recorded_argv)
    assert all("sudo" not in line for line in recorded_argv)
    parent_argv = docker_parent.split()
    assert Path(parent_argv[0]).resolve() == Path(sys.executable).resolve()
    assert parent_argv[1:] == [str(Path(sys.executable).with_name("trafficlab")), "capture", str(experiment_path)]


def test_installed_capture_sigint_runs_bounded_interruption_lifecycle(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A real SIGINT must produce exit 130 only after kill, flush, diagnostics, and cleanup."""
    experiment_path = tmp_path / "experiment.toml"
    _write_config(experiment_path, valid_config_data)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    argv_path = tmp_path / "docker.argv"
    parent_path = tmp_path / "docker.parent"
    state_path = tmp_path / "docker.state"
    _write_fake_docker(bin_directory, argv_path)
    process = _start_installed_capture(
        experiment_path,
        working_directory=_isolated_installed_working_directory(tmp_path),
        fake_docker_directory=bin_directory,
        fixture_pcapng=Path(__file__).parents[3] / "examples" / "data" / "reference.pcapng",
        state_path=state_path,
        argv_path=argv_path,
        parent_path=parent_path,
        mode="interrupt",
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            observed_commands = argv_path.read_text(encoding="utf-8").splitlines()
            target_start = next(
                (
                    index
                    for index, command in enumerate(observed_commands)
                    if "--project-name trafficlab-capture-" in command and "up --detach --no-deps target" in command
                ),
                None,
            )
            if target_start is not None and any(
                "ps --all --format json target" in command for command in observed_commands[target_start + 1 :]
            ):
                break
            time.sleep(0.01)
        else:
            pytest.fail("installed capture did not start target before SIGINT deadline")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5.0)

    run = cast(dict[str, object], valid_config_data["run"])
    run_directory = Path(cast(str, run["directory"]))
    commands = argv_path.read_text(encoding="utf-8").splitlines()
    kill_target = next(index for index, command in enumerate(commands) if " kill target" in f" {command}")
    flush_capture = next(
        index
        for index, command in enumerate(commands[kill_target + 1 :], start=kill_target + 1)
        if "kill --signal SIGINT capture" in command
    )
    cleanup = next(
        index
        for index, command in enumerate(commands[flush_capture + 1 :], start=flush_capture + 1)
        if "down --volumes --remove-orphans" in command
    )
    assert process.returncode == 130
    assert stdout == ""
    assert "capture: capture interrupted by user" in stderr
    assert kill_target < flush_capture < cleanup
    assert (run_directory / "diagnostic-capture.json").exists()
    assert (run_directory / "diagnostic-reference.pcapng").exists()
