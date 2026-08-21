"""Standard-library subprocess implementation for Docker commands."""

from __future__ import annotations

import math
import os
import signal
import subprocess
from collections.abc import Mapping

from trafficlab.capture.docker.types import CommandResult, ProcessHandle
from trafficlab.common.errors import TrafficlabError


def _process_error(action: str, error: OSError) -> TrafficlabError:
    return TrafficlabError(
        f"could not {action}: {error}",
        corrective_action="verify the Docker executable and local process permissions, then retry",
    )


def _decode_output(data: bytes, *, stream: str) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise TrafficlabError(
            f"Docker command returned invalid UTF-8 on {stream}",
            corrective_action="verify the Docker CLI installation and locale, then retry",
        ) from error


class _SubprocessHandle:
    def __init__(self, process: subprocess.Popen[bytes], *, process_group: int | None) -> None:
        self._process = process
        self._process_group = process_group
        self._result: CommandResult | None = None

    def wait(self, *, timeout: float) -> CommandResult | None:
        require_positive_finite(timeout, "process wait timeout")
        if self._result is not None:
            return self._result
        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        except OSError as error:
            raise _process_error("wait for Docker cleanup command", error) from error
        returncode = self._process.returncode
        if returncode is None:
            raise TrafficlabError(
                "Docker cleanup command ended without an exit status",
                corrective_action="inspect the local Docker CLI process and retry cleanup",
            )
        self._result = CommandResult(
            returncode=returncode,
            stdout=_decode_output(stdout, stream="stdout"),
            stderr=_decode_output(stderr, stream="stderr"),
        )
        return self._result

    def _signal(self, *, force: bool, action: str) -> None:
        try:
            if self._process_group is None:
                if force:
                    self._process.kill()
                else:
                    self._process.terminate()
            else:
                sent = signal.SIGKILL if force else signal.SIGTERM
                os.killpg(self._process_group, sent)
        except ProcessLookupError:
            self.reap()
        except OSError as error:
            raise _process_error(f"{action} Docker cleanup command group", error) from error

    def terminate(self) -> None:
        self._signal(force=False, action="terminate")

    def kill(self) -> None:
        self._signal(force=True, action="kill")

    def reap(self) -> bool:
        if self._result is not None:
            return True
        try:
            return self._process.poll() is not None
        except OSError as error:
            raise _process_error("reap Docker cleanup command", error) from error


class SubprocessBoundary:
    """Standard-library implementation of the direct command boundary."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        require_positive_finite(timeout, "command timeout")
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                env=None if environment is None else dict(environment),
                shell=False,
                timeout=timeout,
            )
        except FileNotFoundError as error:
            raise TrafficlabError(
                "Docker executable was not found",
                corrective_action="install Docker Engine with the Compose v2 plugin and retry",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise TrafficlabError(
                f"Docker command timed out after {timeout:g} seconds",
                corrective_action="check Docker daemon responsiveness and retry",
            ) from error
        except OSError as error:
            raise _process_error("launch Docker command", error) from error
        return CommandResult(
            returncode=completed.returncode,
            stdout=_decode_output(completed.stdout, stream="stdout"),
            stderr=_decode_output(completed.stderr, stream="stderr"),
        )

    def start(
        self,
        argv: tuple[str, ...],
        *,
        environment: Mapping[str, str] | None,
    ) -> ProcessHandle:
        try:
            process = subprocess.Popen(
                argv,
                env=None if environment is None else dict(environment),
                shell=False,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
                stdout=subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise TrafficlabError(
                "Docker executable was not found",
                corrective_action="install Docker Engine with the Compose v2 plugin and retry",
            ) from error
        except OSError as error:
            raise _process_error("launch Docker cleanup command", error) from error
        return _SubprocessHandle(process, process_group=process.pid if os.name == "posix" else None)


def require_positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficlabError(
            f"{name} must be a positive finite number",
            corrective_action=f"set {name} to a positive finite number",
        )
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise TrafficlabError(
            f"{name} must be a positive finite number",
            corrective_action=f"set {name} to a positive finite number",
        ) from error
    if not math.isfinite(converted) or converted <= 0.0:
        raise TrafficlabError(
            f"{name} must be a positive finite number",
            corrective_action=f"set {name} to a positive finite number",
        )
    return converted
