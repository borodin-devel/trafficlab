from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from ctypes import addressof, c_char, memset
from pathlib import Path

CHUNK_BYTES = 4 * 1024 * 1024
ROLES = ("parent", "child", "grandchild")


def _write_pid(pid_directory: Path, role: str) -> None:
    temporary = pid_directory / f".{role}.{os.getpid()}.tmp"
    temporary.write_text(f"{os.getpid()}\n", encoding="ascii")
    os.replace(temporary, pid_directory / f"{role}.pid")


def _start_next(mode: str, pid_directory: Path, role: str) -> subprocess.Popen[bytes] | None:
    role_index = ROLES.index(role)
    if role_index == len(ROLES) - 1:
        return None
    return subprocess.Popen(
        (sys.executable, __file__, mode, str(pid_directory), ROLES[role_index + 1]),
        start_new_session=True,
    )


def _wait_until_tree_ready(pid_directory: Path) -> None:
    paths = tuple(pid_directory / f"{role}.pid" for role in ROLES)
    while not all(path.is_file() for path in paths):
        time.sleep(0.01)


def _block() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(60.0)


def _allocate_worker(retained: list[bytearray], barrier: threading.Barrier) -> None:
    barrier.wait()
    while True:
        chunk = bytearray(CHUNK_BYTES)
        memset(addressof(c_char.from_buffer(chunk)), 1, CHUNK_BYTES)
        retained.append(chunk)


def _allocate() -> None:
    retained: list[bytearray] = []
    workers = 8
    barrier = threading.Barrier(workers)
    threads = tuple(threading.Thread(target=_allocate_worker, args=(retained, barrier)) for _ in range(workers))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] not in {"wall", "memory"} or sys.argv[3] not in ROLES:
        raise SystemExit("usage: process_guard_tree.py wall|memory PID_DIRECTORY parent|child|grandchild")
    mode = sys.argv[1]
    pid_directory = Path(sys.argv[2])
    role = sys.argv[3]
    if not pid_directory.is_dir():
        raise SystemExit(f"PID directory does not exist: {pid_directory}")

    _write_pid(pid_directory, role)
    child = _start_next(mode, pid_directory, role)
    _wait_until_tree_ready(pid_directory)
    try:
        if mode == "wall":
            _block()
        _allocate()
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10.0)


if __name__ == "__main__":
    main()
