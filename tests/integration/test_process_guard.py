from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tests.support.fixture_paths import PROCESS_GUARD_FIXTURE_ROOT

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).parents[2]
GUARD = REPOSITORY_ROOT / "scripts" / "run_bounded.sh"
TREE_FIXTURE = PROCESS_GUARD_FIXTURE_ROOT / "process_guard_tree.py"
PID_NAMES = ("parent.pid", "child.pid", "grandchild.pid")


def _unit_name(label: str) -> str:
    return f"trafficlab-test-guard-test-{label}-{os.getpid()}-{time.monotonic_ns()}"


def _guard_argv(*arguments: str) -> tuple[str, ...]:
    assert GUARD.is_file(), f"bounded test guard is missing: {GUARD}"
    return (str(GUARD), *arguments)


def _run_guard(
    *arguments: str,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _guard_argv(*arguments),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10.0,
    )


def _assert_scope_inactive(unit: str) -> None:
    result = subprocess.run(
        ("systemctl", "--user", "is-active", f"{unit}.scope"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    state = result.stdout.strip()
    assert result.returncode in {3, 4} and state in {"failed", "inactive", "unknown"}, (
        f"scope {unit}.scope remained active or could not be verified: "
        f"status={result.returncode}, stdout={state!r}, stderr={result.stderr.strip()!r}"
    )


def _controlled_path(tmp_path: Path, *, omit: str | None = None, manager_available: bool = True) -> str:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir(parents=True)
    for name in (
        "bash",
        "chmod",
        "mktemp",
        "mv",
        "rm",
        "setsid",
        "sleep",
        "systemd-run",
        "systemctl",
        "timeout",
    ):
        if name == omit or (name == "systemctl" and not manager_available):
            continue
        source = shutil.which(name)
        assert source is not None
        (bin_directory / name).symlink_to(source)
    if not manager_available:
        manager = bin_directory / "systemctl"
        manager.write_text("#!/bin/sh\necho 'user manager unavailable' >&2\nexit 1\n", encoding="utf-8")
        manager.chmod(0o755)
    return str(bin_directory)


def _replace_command(bin_directory: Path, name: str, content: str) -> None:
    command_path = bin_directory / name
    command_path.unlink()
    command_path.write_text(content, encoding="utf-8")
    command_path.chmod(0o755)


def _valid_prefix(unit: str) -> tuple[str, ...]:
    return (
        "--memory-high",
        "48M",
        "--memory-max",
        "64M",
        "--swap-max",
        "0",
        "--wall-time",
        "2s",
        "--kill-after",
        "200ms",
        "--unit",
        unit,
        "--",
    )


def test_invalid_setup_returns_125_with_corrective_diagnostics(tmp_path: Path) -> None:
    """Missing, malformed, unsafe, or unavailable setup must never launch an unbounded command."""
    unit = _unit_name("invalid")
    cases: tuple[tuple[Sequence[str], str], ...] = (
        (("--memory-high", "48M"), "required option"),
        ((*_valid_prefix(unit),), "missing command"),
        (("--memory-high", "12MB", *_valid_prefix(unit)[2:], "/bin/true"), "memory"),
        (("--memory-high", "64M", *_valid_prefix(unit)[2:], "/bin/true"), "less than"),
        (("--memory-high", "999999999999999999999999999999999G", *_valid_prefix(unit)[2:], "/bin/true"), "range"),
        ((*_valid_prefix(unit)[:6], "--wall-time", "0ms", *_valid_prefix(unit)[8:], "/bin/true"), "duration"),
        ((*_valid_prefix(unit)[:10], "--unit", "bad/unit", "--", "/bin/true"), "unit"),
        (("--unexpected", "value", "--", "/bin/true"), "unknown option"),
    )
    for arguments, diagnostic in cases:
        result = _run_guard(*arguments)
        assert result.returncode == 125, (arguments, result.stdout, result.stderr)
        assert diagnostic in result.stderr.lower(), (arguments, result.stderr)

    valid = (*_valid_prefix(unit), "/bin/true")
    for unavailable in ("setsid", "systemd-run", "systemctl", "timeout"):
        environment = {**os.environ, "PATH": _controlled_path(tmp_path / unavailable, omit=unavailable)}
        result = _run_guard(*valid, environment=environment)
        assert result.returncode == 125
        assert unavailable in result.stderr

    environment = {**os.environ, "PATH": _controlled_path(tmp_path / "manager", manager_available=False)}
    result = _run_guard(*valid, environment=environment)
    assert result.returncode == 125
    assert unit in result.stderr
    assert "user manager" in result.stderr.lower()

    launch_path = Path(_controlled_path(tmp_path / "launch"))
    fake_systemd_run = launch_path / "systemd-run"
    fake_systemd_run.unlink()
    fake_systemd_run.write_text("#!/bin/sh\necho 'controlled launch failure' >&2\nexit 23\n", encoding="utf-8")
    fake_systemd_run.chmod(0o755)
    launch_unit = _unit_name("launch-failure")
    result = _run_guard(*_valid_prefix(launch_unit), "/bin/true", environment={**os.environ, "PATH": str(launch_path)})
    assert result.returncode == 125
    assert launch_unit in result.stderr
    assert "failed to create" in result.stderr.lower()
    _assert_scope_inactive(launch_unit)


def test_child_status_is_preserved_and_named_scope_becomes_inactive(tmp_path: Path) -> None:
    """Replacing the child status or leaving its named scope active violates the guard boundary."""
    unit = _unit_name("status")

    result = _run_guard(*_valid_prefix(unit), "/bin/sh", "-c", "exit 37")

    assert result.returncode == 37, result.stderr
    _assert_scope_inactive(unit)

    leak_unit = _unit_name("success-leak")
    pid_file = tmp_path / "detached.pid"
    child_code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    parent_code = (
        "import pathlib,subprocess,sys;"
        "child=subprocess.Popen((sys.executable,'-c',sys.argv[2]),start_new_session=True);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='ascii')"
    )

    result = _run_guard(*_valid_prefix(leak_unit), sys.executable, "-c", parent_code, str(pid_file), child_code)

    assert result.returncode == 125, result.stderr
    detached_pid = int(pid_file.read_text(encoding="ascii"))
    _assert_pids_disappear((detached_pid, detached_pid, detached_pid))
    _assert_scope_inactive(leak_unit)

    collision_unit = _unit_name("collision")
    collision_pid_file = tmp_path / "collision.pid"
    collision_code = (
        "import os,pathlib,signal,sys,time;"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='ascii');"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    )
    existing = subprocess.Popen(
        (
            "systemd-run",
            "--user",
            "--quiet",
            "--scope",
            "--collect",
            f"--unit={collision_unit}",
            sys.executable,
            "-c",
            collision_code,
            str(collision_pid_file),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    collision_pid: int | None = None
    try:
        collision_pid = _wait_for_path(collision_pid_file, existing)
        result = _run_guard(*_valid_prefix(collision_unit), "/bin/true")

        assert result.returncode == 125, result.stderr
        assert collision_unit in result.stderr
        assert "already exists" in result.stderr.lower()
        assert Path(f"/proc/{collision_pid}").exists(), "guard killed a process in a scope it did not create"
        active = subprocess.run(
            ("systemctl", "--user", "is-active", f"{collision_unit}.scope"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        assert active.returncode == 0 and active.stdout.strip() == "active"
    finally:
        _stop_exact_scope(collision_unit)
        existing.communicate(timeout=10.0)
        if collision_pid is not None:
            _assert_pids_disappear((collision_pid, collision_pid, collision_pid))
        _assert_scope_inactive(collision_unit)


def test_concurrent_unit_creator_is_never_claimed_or_killed(tmp_path: Path) -> None:
    """A same-name unit created after precheck is not proof that this guard owns it."""
    unit = _unit_name("concurrent-collision")
    pid_file = tmp_path / "unrelated.pid"
    marker = tmp_path / "injected"
    bin_directory = Path(_controlled_path(tmp_path / "concurrent-bin"))
    real_systemctl = shutil.which("systemctl")
    real_systemd_run = shutil.which("systemd-run")
    assert real_systemctl is not None and real_systemd_run is not None
    child_code = (
        "import os,pathlib,signal,sys,time;"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='ascii');"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    )
    wrapper = f"""#!/bin/bash
if [[ $1 == --user && $2 == show && $3 == {unit}.scope && $5 == LoadState && ! -e {marker} ]]; then
    : > {marker}
    {real_systemd_run} --user --quiet --scope --collect --unit={unit} \\
        {sys.executable} -c {shlex.quote(child_code)} {pid_file} >/dev/null 2>&1 &
    for attempt in {{1..100}}; do [[ -s {pid_file} ]] && break; sleep 0.01; done
    printf 'not-found\\n'
    exit 0
fi
exec {real_systemctl} "$@"
"""
    _replace_command(bin_directory, "systemctl", wrapper)
    unrelated_pid: int | None = None
    try:
        result = _run_guard(
            *_valid_prefix(unit),
            "/bin/true",
            environment={**os.environ, "PATH": str(bin_directory)},
        )
        unrelated_pid = int(pid_file.read_text(encoding="ascii"))

        assert result.returncode == 125, result.stderr
        assert Path(f"/proc/{unrelated_pid}").exists(), "guard killed the concurrently created unrelated process"
        active = subprocess.run(
            ("systemctl", "--user", "is-active", f"{unit}.scope"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        assert active.returncode == 0 and active.stdout.strip() == "active"
    finally:
        _stop_exact_scope(unit)
        if unrelated_pid is not None:
            _assert_pids_disappear((unrelated_pid, unrelated_pid, unrelated_pid))
        _assert_scope_inactive(unit)


def test_collected_owned_scope_replaced_before_cleanup_is_never_killed(tmp_path: Path) -> None:
    """A stale acknowledgement must not authorize killing a later unit incarnation with the same name."""
    unit = _unit_name("collected-replacement")
    replacement_pid_file = tmp_path / "replacement.pid"
    injected = tmp_path / "replacement-injected"
    launcher_done = tmp_path / "launcher-done"
    bin_directory = Path(_controlled_path(tmp_path / "replacement-bin"))
    real_systemctl = shutil.which("systemctl")
    real_systemd_run = shutil.which("systemd-run")
    assert real_systemctl is not None and real_systemd_run is not None
    replacement_code = (
        "import os,pathlib,signal,sys,time;"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='ascii');"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    )
    systemctl_wrapper = f"""#!/bin/bash
if [[ $1 == --user && $3 == {unit}.scope && -e {launcher_done} && ! -e {injected} ]] && \
    {{ [[ $2 == is-active ]] || [[ $2 == show && $5 == Description ]]; }}; then
    for attempt in {{1..100}}; do
        {real_systemctl} --user is-active {unit}.scope >/dev/null 2>&1 || break
        sleep 0.01
    done
    : > {injected}
    {real_systemd_run} --user --quiet --scope --collect --unit={unit} \
        {sys.executable} -c {shlex.quote(replacement_code)} {replacement_pid_file} >/dev/null 2>&1 &
    for attempt in {{1..100}}; do [[ -s {replacement_pid_file} ]] && break; sleep 0.01; done
    printf 'active\n'
    exit 0
fi
exec {real_systemctl} "$@"
"""
    systemd_run_wrapper = f"""#!/bin/bash
{real_systemd_run} "$@"
status=$?
: > {launcher_done}
exit "$status"
"""
    _replace_command(bin_directory, "systemctl", systemctl_wrapper)
    _replace_command(bin_directory, "systemd-run", systemd_run_wrapper)
    replacement_pid: int | None = None
    try:
        result = _run_guard(
            *_valid_prefix(unit),
            "/bin/true",
            environment={**os.environ, "PATH": str(bin_directory)},
        )
        replacement_pid = int(replacement_pid_file.read_text(encoding="ascii"))

        assert result.returncode == 0, result.stderr
        assert Path(f"/proc/{replacement_pid}").exists(), "guard killed the replacement unit from stale ownership"
        active = subprocess.run(
            ("systemctl", "--user", "is-active", f"{unit}.scope"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        assert active.returncode == 0 and active.stdout.strip() == "active"
    finally:
        _stop_exact_scope(unit)
        if replacement_pid is not None:
            _assert_pids_disappear((replacement_pid, replacement_pid, replacement_pid))
        _assert_scope_inactive(unit)


def _wait_for_pid_files(process: subprocess.Popen[str], pid_directory: Path) -> tuple[int, int, int]:
    deadline = time.monotonic() + 5.0
    paths = tuple(pid_directory / name for name in PID_NAMES)
    while time.monotonic() < deadline:
        if all(path.is_file() for path in paths):
            return tuple(int(path.read_text(encoding="ascii")) for path in paths)  # type: ignore[return-value]
        if process.poll() is not None:
            _, stderr = process.communicate(timeout=10.0)
            pytest.fail(f"guard exited before all PID files appeared: status={process.returncode}, stderr={stderr!r}")
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for controlled PID files: {[str(path) for path in paths if not path.exists()]}")


def _stop_exact_scope(unit: str) -> None:
    subprocess.run(
        ("systemctl", "--user", "kill", "--kill-whom=all", "--signal=KILL", f"{unit}.scope"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
    )


def _assert_pids_disappear(pids: tuple[int, int, int]) -> None:
    deadline = time.monotonic() + 5.0
    remaining = pids
    while time.monotonic() < deadline:
        remaining = tuple(pid for pid in pids if Path(f"/proc/{pid}").exists())
        if not remaining:
            return
        time.sleep(0.02)
    pytest.fail(f"controlled descendants remained after guard exit: {remaining}")


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if path.is_file():
            content = path.read_text(encoding="ascii").strip()
            if content.isdigit():
                return int(content)
        if process.poll() is not None:
            _, stderr = process.communicate(timeout=10.0)
            pytest.fail(f"process exited before {path} appeared: status={process.returncode}, stderr={stderr!r}")
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path}")


def _run_tree_probe(mode: str, pid_directory: Path, unit: str) -> tuple[int, tuple[int, int, int], str]:
    assert TREE_FIXTURE.is_file(), f"controlled process-tree fixture is missing: {TREE_FIXTURE}"
    limits = ("63M", "64M", "0", "5s", "200ms") if mode == "memory" else ("48M", "64M", "0", "300ms", "200ms")
    process = subprocess.Popen(
        _guard_argv(
            "--memory-high",
            limits[0],
            "--memory-max",
            limits[1],
            "--swap-max",
            limits[2],
            "--wall-time",
            limits[3],
            "--kill-after",
            limits[4],
            "--unit",
            unit,
            "--",
            sys.executable,
            str(TREE_FIXTURE),
            mode,
            str(pid_directory),
            "parent",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pids: tuple[int, int, int] | None = None
    try:
        pids = _wait_for_pid_files(process, pid_directory)
        _, stderr = process.communicate(timeout=10.0)
        assert process.returncode is not None
        return process.returncode, pids, stderr
    finally:
        if process.poll() is None:
            _stop_exact_scope(unit)
            process.kill()
            process.communicate(timeout=10.0)
        if pids is not None:
            _assert_pids_disappear(pids)
        _assert_scope_inactive(unit)


def test_wall_timeout_kills_session_separated_term_ignoring_tree(tmp_path: Path) -> None:
    """A timeout implementation that targets only one process group leaks the child or grandchild."""
    unit = _unit_name("wall")

    status, pids, stderr = _run_tree_probe("wall", tmp_path, unit)

    assert status in {124, 137}, stderr
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)

    signal_unit = _unit_name("signal")
    signal_directory = tmp_path / "signal"
    signal_directory.mkdir()
    process = subprocess.Popen(
        _guard_argv(
            "--memory-high",
            "48M",
            "--memory-max",
            "64M",
            "--swap-max",
            "0",
            "--wall-time",
            "30s",
            "--kill-after",
            "200ms",
            "--unit",
            signal_unit,
            "--",
            sys.executable,
            str(TREE_FIXTURE),
            "wall",
            str(signal_directory),
            "parent",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    signal_pids: tuple[int, int, int] | None = None
    try:
        signal_pids = _wait_for_pid_files(process, signal_directory)
        started = time.monotonic()
        process.terminate()
        _, stderr = process.communicate(timeout=2.0)
        assert process.returncode == 143, stderr
        assert time.monotonic() - started < 2.0
    finally:
        if process.poll() is None:
            _stop_exact_scope(signal_unit)
            process.kill()
            process.communicate(timeout=10.0)
        if signal_pids is not None:
            _assert_pids_disappear(signal_pids)
        _assert_scope_inactive(signal_unit)


def test_signal_during_activation_cannot_leave_a_late_scope(tmp_path: Path) -> None:
    """TERM before activation ownership is observed must stop the launcher before it can create a late scope."""
    unit = _unit_name("activation-signal")
    started_file = tmp_path / "launcher-started"
    pid_file = tmp_path / "late-child.pid"
    bin_directory = Path(_controlled_path(tmp_path / "activation-bin"))
    real_systemd_run = shutil.which("systemd-run")
    assert real_systemd_run is not None
    _replace_command(
        bin_directory,
        "systemd-run",
        f'#!/bin/bash\nprintf \'%s\\n\' "$$" > {started_file}\nsleep 0.5\nexec {real_systemd_run} "$@"\n',
    )
    child_code = (
        "import os,pathlib,signal,sys,time;"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='ascii');"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    )
    process = subprocess.Popen(
        _guard_argv(*_valid_prefix(unit), sys.executable, "-c", child_code, str(pid_file)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PATH": str(bin_directory)},
    )
    late_pid: int | None = None
    try:
        _wait_for_path(started_file, process)
        started = time.monotonic()
        process.terminate()
        _, stderr = process.communicate(timeout=2.0)
        assert process.returncode == 143, stderr
        assert time.monotonic() - started < 2.0
        if pid_file.is_file() and (content := pid_file.read_text(encoding="ascii").strip()).isdigit():
            late_pid = int(content)
            assert not Path(f"/proc/{late_pid}").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10.0)
        if (
            subprocess.run(
                ("systemctl", "--user", "is-active", f"{unit}.scope"),
                check=False,
                capture_output=True,
                text=True,
                timeout=10.0,
            ).returncode
            == 0
        ):
            _stop_exact_scope(unit)
        if late_pid is not None:
            _assert_pids_disappear((late_pid, late_pid, late_pid))
        _assert_scope_inactive(unit)


def test_signal_after_private_ack_before_parent_observation_cleans_owned_scope(tmp_path: Path) -> None:
    """A private ack written before the parent's ownership assignment must still authorize safe cleanup."""
    unit = _unit_name("ack-before-observation")
    launcher_started = tmp_path / "launcher-started"
    pid_directory = tmp_path / "tree"
    pid_directory.mkdir()
    ownership_root = tmp_path / "ownership"
    ownership_root.mkdir()
    bin_directory = Path(_controlled_path(tmp_path / "ack-bin"))
    real_setsid = shutil.which("setsid")
    assert real_setsid is not None
    _replace_command(
        bin_directory,
        "setsid",
        f'#!/bin/bash\nprintf \'%s\\n\' "$$" > {launcher_started}\nsleep 0.5\nexec {real_setsid} "$@"\n',
    )
    process = subprocess.Popen(
        _guard_argv(
            *_valid_prefix(unit)[:6],
            "--wall-time",
            "30s",
            *_valid_prefix(unit)[8:],
            sys.executable,
            str(TREE_FIXTURE),
            "wall",
            str(pid_directory),
            "parent",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PATH": str(bin_directory), "TMPDIR": str(ownership_root)},
    )
    pids: tuple[int, int, int] | None = None
    try:
        _wait_for_path(launcher_started, process)
        process.send_signal(signal.SIGSTOP)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            state = Path(f"/proc/{process.pid}/status").read_text(encoding="ascii")
            if "State:\tT" in state:
                break
            time.sleep(0.01)
        else:
            pytest.fail("guard parent did not stop before scope activation")

        pids = _wait_for_pid_files(process, pid_directory)
        acknowledgements = tuple(ownership_root.glob("trafficlab-test-guard.*/ack"))
        assert len(acknowledgements) == 1
        assert acknowledgements[0].read_text(encoding="ascii").startswith("trafficlab-test-guard-token-")

        started = time.monotonic()
        process.terminate()
        process.send_signal(signal.SIGCONT)
        _, stderr = process.communicate(timeout=2.0)

        assert process.returncode == 143, stderr
        assert time.monotonic() - started < 2.0
        _assert_pids_disappear(pids)
        _assert_scope_inactive(unit)
    finally:
        active = subprocess.run(
            ("systemctl", "--user", "is-active", f"{unit}.scope"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if active.returncode == 0:
            _stop_exact_scope(unit)
        if process.poll() is None:
            process.kill()
            process.send_signal(signal.SIGCONT)
        process.communicate(timeout=10.0)
        if pids is not None:
            _assert_pids_disappear(pids)
        _assert_scope_inactive(unit)


def test_signal_after_dispatch_cancels_delayed_scope_ownership(tmp_path: Path) -> None:
    """TERM after scope dispatch must stop the activation tree without an attempt-count ownership guess."""
    unit = _unit_name("post-dispatch-signal")
    dispatched = tmp_path / "request-dispatched"
    reveal_ownership = tmp_path / "reveal-ownership"
    late_pid_file = tmp_path / "late-child.pid"
    bin_directory = Path(_controlled_path(tmp_path / "post-dispatch-bin"))
    real_systemctl = shutil.which("systemctl")
    real_systemd_run = shutil.which("systemd-run")
    assert real_systemctl is not None and real_systemd_run is not None
    systemctl_wrapper = f"""#!/bin/bash
if [[ $1 == --user && $2 == show && $3 == {unit}.scope && $5 == Description && ! -e {reveal_ownership} ]]; then
    printf '\n'
    exit 0
fi
exec {real_systemctl} "$@"
"""
    systemd_run_wrapper = f"""#!/bin/bash
arguments=("$@")
prefix=("${{arguments[@]:0:15}}")
command=("${{arguments[@]:15}}")
exec {real_systemd_run} "${{prefix[@]}}" /bin/bash -c \
    'printf "%s\\n" "$$" > "$1"; sleep 3; : > "$2"; printf "%s\\n" "$$" > "$3"; exec "${{@:4}}"' \
    scope-delay {dispatched} {reveal_ownership} {late_pid_file} "${{command[@]}}"
"""
    _replace_command(bin_directory, "systemctl", systemctl_wrapper)
    _replace_command(bin_directory, "systemd-run", systemd_run_wrapper)
    child_code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    long_activation_prefix = (
        *_valid_prefix(unit)[:6],
        "--wall-time",
        "30s",
        *_valid_prefix(unit)[8:],
    )
    process = subprocess.Popen(
        _guard_argv(*long_activation_prefix, sys.executable, "-c", child_code),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PATH": str(bin_directory)},
    )
    late_pid: int | None = None
    try:
        _wait_for_path(dispatched, process)
        process.terminate()
        _, stderr = process.communicate(timeout=2.0)
        assert process.returncode == 143, stderr

        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and not late_pid_file.is_file():
            time.sleep(0.02)
        if late_pid_file.is_file():
            late_pid = int(late_pid_file.read_text(encoding="ascii"))
        assert late_pid is None, "a post-dispatch activation child outlived the terminated guard"
        _assert_scope_inactive(unit)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10.0)
        if (
            subprocess.run(
                ("systemctl", "--user", "is-active", f"{unit}.scope"),
                check=False,
                capture_output=True,
                text=True,
                timeout=10.0,
            ).returncode
            == 0
        ):
            _stop_exact_scope(unit)
        if late_pid is not None:
            _assert_pids_disappear((late_pid, late_pid, late_pid))
        _assert_scope_inactive(unit)


def test_fast_nonzero_command_preserves_status_after_collection(tmp_path: Path) -> None:
    """A command that starts and is collected between polls still returns its own status."""
    unit = _unit_name("fast-status")
    bin_directory = Path(_controlled_path(tmp_path / "fast-bin"))
    real_systemctl = shutil.which("systemctl")
    real_systemd_run = shutil.which("systemd-run")
    assert real_systemctl is not None and real_systemd_run is not None
    launcher_done = tmp_path / "launcher-done"
    wrapper = f"""#!/bin/bash
if [[ $1 == --user && $2 == is-active && $3 == {unit}.scope && ! -e {launcher_done} ]]; then
    printf 'inactive\\n'
    exit 4
fi
exec {real_systemctl} "$@"
"""
    _replace_command(bin_directory, "systemctl", wrapper)
    _replace_command(
        bin_directory,
        "systemd-run",
        f'#!/bin/bash\n{real_systemd_run} "$@"\nstatus=$?\n: > {launcher_done}\nexit "$status"\n',
    )

    result = _run_guard(
        *_valid_prefix(unit),
        "/bin/sh",
        "-c",
        "exit 42",
        environment={**os.environ, "PATH": str(bin_directory)},
    )

    assert result.returncode == 42, result.stderr
    _assert_scope_inactive(unit)


def test_hard_memory_limit_kills_three_allocating_roles_without_swap(tmp_path: Path) -> None:
    """A per-process or advisory-only limit lets the three-role allocation tree survive."""
    unit = _unit_name("memory")

    status, pids, stderr = _run_tree_probe("memory", tmp_path, unit)

    assert status == 137, f"memory probe did not produce the expected cgroup OOM status: {status}; {stderr}"
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
