import json
import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from trafficlab.capture.topology import ComposePaths, render_production_compose, write_production_compose
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError


def _config(valid_config_data: dict[str, object], tmp_path: Path) -> ExperimentConfig:
    target = cast(dict[str, object], valid_config_data["target"])
    target["argv"] = ["curl", "--fail", "https://example.invalid/data?a=1&b=2"]
    target["environment"] = {"Z_LAST": "z", "A_FIRST": "a value"}
    target["working_directory"] = "/workspace"
    target["mounts"] = [
        {"source": str(tmp_path / "input"), "target": "/workspace/input", "read_only": True},
        {"source": str(tmp_path / "cache"), "target": "/workspace/cache", "read_only": False},
    ]
    return ExperimentConfig.model_validate(valid_config_data)


def _document(rendered: bytes) -> dict[str, object]:
    return cast(dict[str, object], json.loads(rendered))


def test_render_production_compose_has_exact_two_service_topology(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    paths = ComposePaths(project_name="trafficlab-case-0123", output_directory=tmp_path / "capture output")

    document = _document(
        render_production_compose(
            config,
            paths,
            target_image=config.target.image,
            capture_image=config.capture.image,
        )
    )

    assert document["name"] == "trafficlab-case-0123"
    services = cast(dict[str, dict[str, object]], document["services"])
    assert set(services) == {"capture", "target"}
    assert services["capture"] == {
        "cap_add": ["NET_RAW", "NET_ADMIN"],
        "image": "trafficlab-capture:local",
        "volumes": [
            {
                "read_only": False,
                "source": str((tmp_path / "capture output").resolve()),
                "target": "/trafficlab",
                "type": "bind",
            }
        ],
    }
    assert services["target"] == {
        "command": ["curl", "--fail", "https://example.invalid/data?a=1&b=2"],
        "environment": {"A_FIRST": "a value", "Z_LAST": "z"},
        "image": "curlimages/curl:8.10.1",
        "init": True,
        "network_mode": "service:capture",
        "volumes": [
            {
                "read_only": True,
                "source": str((tmp_path / "input").resolve()),
                "target": "/workspace/input",
                "type": "bind",
            },
            {
                "read_only": False,
                "source": str((tmp_path / "cache").resolve()),
                "target": "/workspace/cache",
                "type": "bind",
            },
        ],
        "working_dir": "/workspace",
    }


def test_render_production_compose_is_sorted_compact_and_deterministic(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    paths = ComposePaths(project_name="trafficlab-deterministic", output_directory=tmp_path / "output")

    first = render_production_compose(
        config,
        paths,
        target_image=config.target.image,
        capture_image=config.capture.image,
    )
    second = render_production_compose(
        config,
        paths,
        target_image=config.target.image,
        capture_image=config.capture.image,
    )
    document = json.loads(first)

    assert first == second
    assert first == (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    assert b"\n" not in first[:-1]
    assert b'"capture"' in first
    assert first.index(b'"capture"') < first.index(b'"target"')


def test_render_resolves_relative_docker_bind_sources(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _config(valid_config_data, tmp_path)
    target = config.target.model_copy(
        update={"mounts": (config.target.mounts[0].model_copy(update={"source": Path("relative-input")}),)}
    )
    config = config.model_copy(update={"target": target})

    document = _document(
        render_production_compose(
            config,
            ComposePaths(project_name="trafficlab-relative", output_directory=Path("relative-output")),
            target_image=config.target.image,
            capture_image=config.capture.image,
        )
    )

    services = cast(dict[str, dict[str, object]], document["services"])
    capture_mount = cast(list[dict[str, object]], services["capture"]["volumes"])[0]
    target_mount = cast(list[dict[str, object]], services["target"]["volumes"])[0]
    assert capture_mount["source"] == str((tmp_path / "relative-output").resolve())
    assert target_mount["source"] == str((tmp_path / "relative-input").resolve())
    assert capture_mount["target"] == "/trafficlab"
    assert target_mount["target"] == "/workspace/input"


def test_compose_document_has_no_privileged_or_wrapper_features(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    document = _document(
        render_production_compose(
            config,
            ComposePaths(project_name="trafficlab-simple", output_directory=tmp_path / "output"),
            target_image=config.target.image,
            capture_image=config.capture.image,
        )
    )
    services = cast(dict[str, dict[str, object]], document["services"])

    assert set(document) == {"name", "services"}
    for service in services.values():
        assert "entrypoint" not in service
        assert "privileged" not in service
        assert service.get("network_mode") != "host"
        assert "ports" not in service
    assert isinstance(services["target"]["command"], list)
    assert services["target"]["command"] == list(config.target.argv)
    assert "command" not in services["capture"]
    assert "endpoint" not in services


def test_compose_paths_are_immutable(tmp_path: Path) -> None:
    paths = ComposePaths(project_name="trafficlab-frozen", output_directory=tmp_path)

    with pytest.raises(FrozenInstanceError):
        cast(Any, paths).project_name = "changed"


def test_writer_writes_exact_rendered_bytes(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    config = _config(valid_config_data, tmp_path)
    paths = ComposePaths(project_name="trafficlab-write", output_directory=tmp_path / "output")
    destination = tmp_path / "compose.json"

    write_production_compose(
        destination,
        config,
        paths,
        target_image=config.target.image,
        capture_image=config.capture.image,
    )

    assert destination.read_bytes() == render_production_compose(
        config,
        paths,
        target_image=config.target.image,
        capture_image=config.capture.image,
    )


@pytest.mark.parametrize(
    "resolution_error",
    [OSError("resolution denied"), RuntimeError("resolution loop")],
    ids=["os-error", "runtime-error"],
)
def test_renderer_translates_bind_resolution_failures(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolution_error: OSError | RuntimeError,
) -> None:
    config = _config(valid_config_data, tmp_path)
    paths = ComposePaths(project_name="trafficlab-resolution", output_directory=tmp_path / "output")

    def fail_resolve(_path: Path, strict: bool = False) -> Path:
        del strict
        raise resolution_error

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(TrafficlabError, match="could not resolve Docker bind source") as error:
        render_production_compose(
            config,
            paths,
            target_image=config.target.image,
            capture_image=config.capture.image,
        )

    assert error.value.corrective_action == "verify Docker bind source paths can be resolved and retry"


def test_writer_translates_destination_write_failures(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    config = _config(valid_config_data, tmp_path)
    paths = ComposePaths(project_name="trafficlab-write-failure", output_directory=tmp_path / "output")

    with pytest.raises(TrafficlabError, match="could not write Compose file") as error:
        write_production_compose(
            tmp_path,
            config,
            paths,
            target_image=config.target.image,
            capture_image=config.capture.image,
        )

    assert error.value.corrective_action == "verify the Compose destination is writable"


def test_capture_entrypoint_is_valid_posix_shell_with_exact_capture_contract() -> None:
    root = Path(__file__).parents[3]
    script = root / "docker" / "capture" / "capture.sh"

    checked = subprocess.run(["sh", "-n", str(script)], check=False, capture_output=True, text=True)
    content = script.read_text(encoding="utf-8")

    assert checked.returncode == 0, checked.stderr
    assert content.startswith("#!/bin/sh\nset -eu\n")
    assert "/sys/class/net/eth0/address" in content
    assert "capture.json.tmp" in content
    assert "capture.json" in content
    assert "reference.pcapng.tmp" in content
    assert 'dumpcap -i eth0 -p -q -w - > "$capture_path" &' in content
    assert 'reordercap "$capture_path" "$ordered_path"' in content
    assert 'mv -f "$ordered_path" "$capture_path"' in content
    assert "sudo" not in content
    assert "promisc" not in content.lower()


def _run_capture_script(
    tmp_path: Path,
    mac: str | None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    root = Path(__file__).parents[3]
    interface_path = tmp_path / "eth0-address"
    output_directory = tmp_path / "output"
    bin_directory = tmp_path / "bin"
    if mac is not None:
        interface_path.write_text(mac, encoding="utf-8")
    output_directory.mkdir()
    bin_directory.mkdir()
    (output_directory / "capture.json").write_text("stale metadata", encoding="utf-8")
    (output_directory / "capture.json.tmp").write_text("stale temporary metadata", encoding="utf-8")
    (output_directory / "reference.pcapng.tmp").write_text("stale capture", encoding="utf-8")
    dumpcap = bin_directory / "dumpcap"
    dumpcap.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$TRAFFICLAB_TEST_DUMPCAP_ARGS"\nprintf captured\n',
        encoding="utf-8",
    )
    dumpcap.chmod(0o755)
    reordercap = bin_directory / "reordercap"
    reordercap.write_text('#!/bin/sh\ncp "$1" "$2"\n', encoding="utf-8")
    reordercap.chmod(0o755)
    script = (root / "docker" / "capture" / "capture.sh").read_text(encoding="utf-8")
    script = script.replace("/sys/class/net/eth0/address", str(interface_path))
    script = script.replace("/trafficlab", str(output_directory))
    script_path = tmp_path / "capture.sh"
    script_path.write_text(script, encoding="utf-8")
    args_path = tmp_path / "dumpcap.args"
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
    environment["TRAFFICLAB_TEST_DUMPCAP_ARGS"] = str(args_path)
    completed = subprocess.run(
        ["sh", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return completed, output_directory, args_path


def test_capture_entrypoint_normalizes_mac_clears_stale_files_and_executes_dumpcap(
    tmp_path: Path,
) -> None:
    completed, output_directory, args_path = _run_capture_script(
        tmp_path,
        "02:42:AC:11:00:02\n",
    )

    assert completed.returncode == 0, completed.stderr
    assert (output_directory / "capture.json").read_text(encoding="utf-8") == (
        '{"interface":"eth0","target_mac":"02:42:ac:11:00:02"}\n'
    )
    assert not (output_directory / "capture.json.tmp").exists()
    capture_path = output_directory / "reference.pcapng.tmp"
    assert capture_path.read_bytes() == b"captured"
    assert capture_path.stat().st_mode & stat.S_IROTH
    assert not (output_directory / "reference.pcapng.ordered").exists()
    assert args_path.read_text(encoding="utf-8").splitlines() == [
        "-i",
        "eth0",
        "-p",
        "-q",
        "-w",
        "-",
    ]


def test_capture_entrypoint_preserves_dumpcap_failure_status_on_interrupt(tmp_path: Path) -> None:
    """Losing the failed wait status could falsely finalize a capture whose dumpcap flush failed."""
    root = Path(__file__).parents[3]
    interface_path = tmp_path / "eth0-address"
    output_directory = tmp_path / "output"
    bin_directory = tmp_path / "bin"
    interface_path.write_text("02:42:ac:11:00:02\n", encoding="utf-8")
    output_directory.mkdir()
    bin_directory.mkdir()
    ordered_path = output_directory / "reference.pcapng.ordered"
    dumpcap_pid_path = tmp_path / "dumpcap.pid"
    reordercap_called_path = tmp_path / "reordercap.called"

    dumpcap = bin_directory / "dumpcap"
    dumpcap.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import signal\n"
        "import sys\n"
        "\n"
        "signal.signal(signal.SIGINT, lambda _signal, _frame: sys.exit(23))\n"
        "with open(os.environ['TRAFFICLAB_TEST_DUMPCAP_PID'], 'w', encoding='utf-8') as stream:\n"
        "    stream.write(f'{os.getpid()}\\n')\n"
        "sys.stdout.buffer.write(b'captured')\n"
        "sys.stdout.buffer.flush()\n"
        "while True:\n"
        "    signal.pause()\n",
        encoding="utf-8",
    )
    dumpcap.chmod(0o755)
    reordercap = bin_directory / "reordercap"
    reordercap.write_text(
        '#!/bin/sh\n: > "$TRAFFICLAB_TEST_REORDERCAP_CALLED"\ncp "$1" "$2"\n',
        encoding="utf-8",
    )
    reordercap.chmod(0o755)

    script = (root / "docker" / "capture" / "capture.sh").read_text(encoding="utf-8")
    script = script.replace("/sys/class/net/eth0/address", str(interface_path))
    script = script.replace("/trafficlab", str(output_directory))
    script_path = tmp_path / "capture.sh"
    script_path.write_text(script, encoding="utf-8")
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
    environment["TRAFFICLAB_TEST_DUMPCAP_PID"] = str(dumpcap_pid_path)
    environment["TRAFFICLAB_TEST_REORDERCAP_CALLED"] = str(reordercap_called_path)

    process = subprocess.Popen(
        ["sh", str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not dumpcap_pid_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert dumpcap_pid_path.exists(), "dumpcap did not start before the test deadline"
        dumpcap_pid = int(dumpcap_pid_path.read_text(encoding="utf-8"))
        ordered_path.write_text("stale ordered capture", encoding="utf-8")
        process.send_signal(signal.SIGINT)
        _stdout, stderr = process.communicate(timeout=5.0)
        assert process.returncode == 23, stderr
        assert not reordercap_called_path.exists()
        assert not ordered_path.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(dumpcap_pid, 0)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


@pytest.mark.parametrize(
    ("mac", "message"),
    [
        ("not-a-mac\n", "malformed"),
        ("00:00:00:00:00:00\n", "zero"),
        ("01:00:5e:00:00:01\n", "multicast"),
    ],
)
def test_capture_entrypoint_rejects_invalid_mac_before_starting_dumpcap(
    tmp_path: Path,
    mac: str,
    message: str,
) -> None:
    completed, output_directory, args_path = _run_capture_script(tmp_path, mac)

    assert completed.returncode == 1
    assert message in completed.stderr
    assert not (output_directory / "capture.json").exists()
    assert not (output_directory / "capture.json.tmp").exists()
    assert not (output_directory / "reference.pcapng.tmp").exists()
    assert not args_path.exists()


def test_capture_entrypoint_rejects_missing_eth0_before_starting_dumpcap(tmp_path: Path) -> None:
    completed, output_directory, args_path = _run_capture_script(tmp_path, None)

    assert completed.returncode == 1
    assert "eth0 MAC is unavailable" in completed.stderr
    assert not (output_directory / "capture.json").exists()
    assert not (output_directory / "capture.json.tmp").exists()
    assert not (output_directory / "reference.pcapng.tmp").exists()
    assert not args_path.exists()


def test_capture_image_is_minimal_pinned_and_runs_entrypoint_directly() -> None:
    dockerfile = (Path(__file__).parents[3] / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith(
        "ARG SOURCE_DATE_EPOCH=1785789333\n"
        "FROM debian:bookworm-20260803-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241\n"
    )
    assert "snapshot.debian.org/archive/debian/20260803T203533Z/" in dockerfile
    assert "snapshot.debian.org/archive/debian-security/20260803T203533Z/" in dockerfile
    assert "--no-install-recommends" in dockerfile
    assert "ca-certificates=20230311+deb12u1" in dockerfile
    assert "curl=7.88.1-10+deb12u15" in dockerfile
    assert "wireshark-common=4.0.17-0+deb12u3" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/trafficlab-capture"]' in dockerfile
    assert "node" not in dockerfile.lower()
    assert "npm" not in dockerfile.lower()
    assert "sudo" not in dockerfile.lower()
    assert "CMD" not in dockerfile
