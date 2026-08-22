"""Runners owner for Validation Study tooling."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from scripts.validation_study.common import TARGET_REFERENCE
from scripts.validation_study.prerequisites.codec import build_expected_capability_argv
from scripts.validation_study.prerequisites.commands import command_live_argv, docker_matrix_argv, internet_smoke_argv
from tests.support.validation_study.constants import CAPTURE_IMAGE_ID, IMAGE_ID, ROOT
from trafficlab.capture.docker.image import (
    cold_capture_build_argv,
)


def write_prerequisite_repository_inputs(repository_root: Path) -> None:
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    for name in ("Dockerfile", "capture.sh", "image-lock.json"):
        shutil.copy2(ROOT / "docker" / "capture" / name, capture_root / name)
    shutil.copy2(ROOT / "uv.lock", repository_root / "uv.lock")


class ScriptedPrerequisiteRunner:
    def __init__(self, repository_root: Path, mutation: str = "happy", *, study_id: str = "study-1") -> None:
        self.root = repository_root
        self.mutation = mutation
        self.study_id = study_id
        self.url = "https://downloads.example.test/object.bin"
        self.final_url = "https://cdn.example.test/object.bin"
        self.target_id = f"sha256:{'b' * 64}"
        self.capture_id = CAPTURE_IMAGE_ID
        self.container_id = "e" * 64
        self.capability_name = f"trafficlab-validation-study-capability-{self.study_id}"
        self.evidence = (
            self.root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / self.study_id
            / "00-prerequisites"
        )
        self.mount = self.root / "examples" / "validation_study" / ".study-work" / "mount" / self.study_id
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.git_trees: dict[str, bytes] = {}
        self.ignored_worktree_paths: frozenset[str] = frozenset()
        self.ignored_worktree_protocol = "valid"
        self.container_running = False
        self.capability_finished = False

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        assert cwd == self.root
        assert check is False
        assert capture_output is True
        assert shell is False
        self.calls.append((command, timeout))
        identities: dict[tuple[str, ...], tuple[int, bytes, bytes]] = {
            ("git", "rev-parse", "HEAD"): (0, b"c" * 40 + b"\n", b""),
            ("git", "status", "--porcelain=v1", "--untracked-files=all"): (
                0,
                b"?? dirty\n" if self.mutation == "dirty-tree" else b"",
                b"",
            ),
            ("docker", "version", "--format", "{{.Server.Version}}"): (0, b"27.0.0\n", b""),
            ("docker", "compose", "version", "--short"): (0, b"2.29.0\n", b""),
            ("docker", "image", "pull", TARGET_REFERENCE): (0, b"pulled\n", b""),
        }
        if command[:2] == ("git", "rev-parse") and len(command) == 3 and (command[2] in self.git_trees):
            return subprocess.CompletedProcess(command, 0, stdout=self.git_trees[command[2]], stderr=b"")
        if command in identities:
            status, stdout, stderr = identities[command]
            return subprocess.CompletedProcess(command, status, stdout=stdout, stderr=stderr)
        if command == ("git", "check-ignore", "-z", "--stdin"):
            return self._check_ignored_worktree_paths(command, input=input)
        if command == ("docker", "image", "inspect", TARGET_REFERENCE):
            return self._inspect_target(command)
        if command[:2] == ("docker", "build"):
            return self._build_capture(command)
        if command == ("docker", "image", "rm", "--force", f"trafficlab-validation-{self.study_id}:capture"):
            return self._remove_capture_image(command)
        if command[:3] == ("docker", "container", "inspect"):
            return self._inspect_container(command)
        if command[:4] == ("docker", "container", "ls", "-a"):
            return self._list_container(command)
        if command[:3] == ("docker", "container", "rm"):
            return self._remove_container(command)
        if command[:3] == ("docker", "run", "--rm"):
            return self._run_capability(command, timeout)
        if command == command_live_argv("docker_matrix", docker_matrix_argv(self.study_id), repository_root=self.root):
            return self._run_test_scope(command, "docker")
        if command == command_live_argv(
            "internet_smoke", internet_smoke_argv(self.study_id, self.url), repository_root=self.root
        ):
            return self._run_test_scope(command, "internet")
        raise AssertionError(f"unexpected command: {command!r}")

    def _inspect_target(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        repo_digests = ["curlimages/curl@sha256:" + "f" * 64]
        if self.mutation != "target-digest-absent":
            repo_digests.append(TARGET_REFERENCE)
        inspected = [{"Id": self.target_id, "RepoDigests": repo_digests, "Config": {"User": "curl_user"}}]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(inspected).encode(), stderr=b"")

    def _build_capture(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        assert command == cold_capture_build_argv(
            f"trafficlab-validation-{self.study_id}:capture", self.evidence / "capture.iid"
        )
        if self.mutation != "capture-iid-missing":
            iid = "trafficlab-capture:local" if self.mutation == "capture-iid-tag" else self.capture_id
            (self.evidence / "capture.iid").write_text(f"{iid}\n", encoding="ascii")
        if self.mutation == "preexisting-cid":
            (self.evidence / "capability.cid").write_text(f"{self.container_id}\n", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout=b"built\n", stderr=b"")

    def _remove_capture_image(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        if self.mutation in {"capture-image-cleanup-failed", "docker-matrix-failed-cleanup-failed"}:
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"cleanup failed\n")
        return subprocess.CompletedProcess(command, 0, stdout=b"removed\n", stderr=b"")

    def _check_ignored_worktree_paths(
        self, command: tuple[str, ...], *, input: bytes | None
    ) -> subprocess.CompletedProcess[bytes]:
        if input is None or not input.endswith(b"\x00"):
            return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"missing NUL input\n")
        paths = tuple(record.decode("utf-8") for record in input[:-1].split(b"\x00"))
        if self.ignored_worktree_protocol == "nonzero":
            return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"synthetic ignore failure\n")
        if self.ignored_worktree_protocol == "truncated":
            return subprocess.CompletedProcess(command, 0, stdout=b"foreign", stderr=b"")
        if self.ignored_worktree_protocol == "nonempty-no-match":
            return subprocess.CompletedProcess(command, 1, stdout=b"foreign\x00", stderr=b"")
        if self.ignored_worktree_protocol == "empty-match":
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        matches = tuple(path for path in paths if path in self.ignored_worktree_paths)
        stdout = b"".join(path.encode("utf-8") + b"\x00" for path in matches)
        return subprocess.CompletedProcess(command, 0 if matches else 1, stdout=stdout, stderr=b"")

    def _inspect_container(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        identifier = command[-1]
        if (
            identifier == self.capability_name
            and (not self.container_running)
            and (self.mutation == "capability-daemon-error")
        ):
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        if (
            identifier == self.capability_name
            and (not self.container_running)
            and (self.mutation == "preexisting-name")
        ):
            return subprocess.CompletedProcess(command, 0, stdout=b"[{}]", stderr=b"")
        if self.container_running and identifier == self.container_id:
            label = (
                self.study_id
                if self.mutation
                in {
                    "capability-timeout-owned",
                    "capability-lingering-owned",
                    "capability-lingering-owned-name-reclaimed",
                }
                else "someone-else"
            )
            inspected = [
                {
                    "Id": self.container_id,
                    "Name": f"/{self.capability_name}",
                    "Config": {"Labels": {"org.trafficlab.validation-study.study": label}},
                }
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(inspected).encode(), stderr=b"")
        if self.container_running and identifier == self.capability_name:
            return subprocess.CompletedProcess(command, 0, stdout=b"[{}]", stderr=b"")
        return subprocess.CompletedProcess(command, 1, stdout=b"[]\n", stderr=b"not found\n")

    def _list_container(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        assert command[-2] == "--format" and command[-1] == "{{.ID}}"
        if self.mutation == "capability-daemon-error":
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        filtered = command[-3]
        if (
            self.capability_finished
            and self.mutation == "capability-post-id-daemon-error"
            and filtered.startswith("id=")
        ):
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        if (
            self.capability_finished
            and self.mutation == "capability-post-name-daemon-error"
            and filtered.startswith("name=")
        ):
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        name_reclaimed = self.capability_finished and self.mutation in {
            "capability-name-reclaimed",
            "capability-lingering-owned-name-reclaimed",
        }
        name_exists = self.mutation == "preexisting-name" and filtered.startswith("name=")
        if name_reclaimed and (not self.container_running) and filtered.startswith("name="):
            stdout = f"{'f' * 64}\n".encode()
        else:
            stdout = f"{self.container_id}\n".encode() if self.container_running or name_exists else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    def _remove_container(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        assert command == ("docker", "container", "rm", "--force", self.container_id)
        assert self.mutation in {
            "capability-timeout-owned",
            "capability-lingering-owned",
            "capability-lingering-owned-name-reclaimed",
        }
        self.container_running = False
        return subprocess.CompletedProcess(command, 0, stdout=f"{self.container_id}\n".encode(), stderr=b"")

    def _run_capability(self, command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
        assert command == self.expected_capability()
        assert "--user" not in command
        if self.mutation == "capability-start-error":
            raise OSError("simulated launch failure")
        if self.mutation != "capability-missing-cid":
            (self.evidence / "capability.cid").write_text(f"{self.container_id}\n", encoding="ascii")
        canary = self.mount / ".capability.headers"
        if self.mutation == "canary-replaced":
            canary.unlink()
            canary.touch(mode=438)
        if self.mutation != "canary-not-written":
            canary.write_bytes(self._capability_headers())
        if self.mutation in {"capability-timeout-owned", "capability-timeout-unowned"}:
            self.container_running = True
            self.capability_finished = True
            raise subprocess.TimeoutExpired(command, timeout, output=b"partial", stderr=b"timeout")
        if self.mutation in {
            "capability-lingering-owned",
            "capability-lingering-unowned",
            "capability-lingering-owned-name-reclaimed",
        }:
            self.container_running = True
        self.capability_finished = True
        stdout = self._write_out()
        status = 7 if self.mutation == "capability-nonzero" else 0
        return subprocess.CompletedProcess(command, status, stdout=stdout, stderr=b"curl diagnostic\n")

    def expected_capability(self) -> tuple[str, ...]:
        checked = list(build_expected_capability_argv(self.study_id, self.url))
        checked[8] = str(self.evidence / "capability.cid")
        checked[12] = f"type=bind,src={self.mount},dst=/trafficlab-study"
        return tuple(checked)

    def _capability_headers(self) -> bytes:
        if self.mutation == "range-ignored":
            return b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\n"
        total = 16777217 if self.mutation == "oversize-object" else 4194304
        return (
            b"HTTP/1.1 302 Found\r\nLocation: https://cdn.example.test/object.bin\r\n\r\n"
            + f"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-0/{total}\r\n".encode()
            + b"Content-Length: 1\r\n\r\n"
        )

    def _write_out(self) -> bytes:
        if self.mutation == "wrong-write-out":
            return b"status=206\nsize=2\n"
        return f"status=206\nsize=1\nurl={self.final_url}\nredirects=1\n".encode()

    def _run_test_scope(self, command: tuple[str, ...], kind: str) -> subprocess.CompletedProcess[bytes]:
        if kind == "docker" and self.mutation in {"docker-matrix-failed", "docker-matrix-failed-cleanup-failed"}:
            Path(command[-1]).write_bytes(
                b'<testsuites><testsuite tests="7" failures="1" errors="0" skipped="0"/></testsuites>'
            )
            return subprocess.CompletedProcess(command, 1, stdout=b"docker failed\n", stderr=b"failure\n")
        skipped = 1 if kind == "internet" and self.mutation == "internet-skipped" else 0
        total = 7 if kind == "docker" else 1
        Path(command[-1]).write_bytes(
            f'<testsuites><testsuite tests="{total}" failures="0" errors="0" skipped="{skipped}"/></testsuites>'.encode()
        )
        return subprocess.CompletedProcess(command, 0, stdout=f"{kind} pass\n".encode(), stderr=b"")


class StudyIdentityRunner:
    def __init__(
        self,
        repository_root: Path,
        *,
        target_image_id: str = IMAGE_ID,
        capture_image_id: str = CAPTURE_IMAGE_ID,
        target_config_user: str = "",
        dirty: bool = False,
        capture_image_present: bool = True,
        owned_capture_tags: set[str] | None = None,
        build_exit_status: int = 0,
        write_build_iid: bool = True,
        build_iid_content: str | None = None,
        inspected_capture_image_id: str | None = None,
        cleanup_exit_status: int = 0,
        on_target_inspect: Callable[[], None] | None = None,
    ) -> None:
        self.root = repository_root
        self.target_image_id = target_image_id
        self.capture_image_id = capture_image_id
        self.target_config_user = target_config_user
        self.target_repo_digests: tuple[str, ...] = (TARGET_REFERENCE,)
        self.docker_engine_version = "27.0.0"
        self.docker_compose_version = "2.29.0"
        self.dirty = dirty
        self.capture_image_present = capture_image_present
        self.owned_capture_tags: set[str] = set() if owned_capture_tags is None else set(owned_capture_tags)
        self.build_exit_status = build_exit_status
        self.write_build_iid = write_build_iid
        self.build_iid_content = build_iid_content
        self.inspected_capture_image_id = inspected_capture_image_id
        self.cleanup_exit_status = cleanup_exit_status
        self.on_target_inspect = on_target_inspect
        self.calls: list[tuple[str, ...]] = []
        self.capture_image_cleanup_tags: list[str] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout
        command = tuple(argv)
        assert cwd == self.root
        assert check is False
        assert capture_output is True
        assert shell is False
        self.calls.append(command)
        if command[:2] == ("docker", "build"):
            iidfile = Path(command[command.index("--iidfile") + 1])
            self.owned_capture_tags.add(command[command.index("--tag") + 1])
            if self.write_build_iid:
                iidfile.write_text(
                    f"{(self.capture_image_id if self.build_iid_content is None else self.build_iid_content)}\n",
                    encoding="ascii",
                )
            if self.build_exit_status == 0:
                self.capture_image_present = True
            return subprocess.CompletedProcess(
                command,
                self.build_exit_status,
                stdout=b"built\n" if self.build_exit_status == 0 else b"",
                stderr=b"" if self.build_exit_status == 0 else b"simulated build failure\n",
            )
        if command[:4] == ("docker", "image", "rm", "--force"):
            self.capture_image_cleanup_tags.append(command[4])
            if self.cleanup_exit_status == 0:
                self.capture_image_present = False
                self.owned_capture_tags.discard(command[4])
            return subprocess.CompletedProcess(
                command,
                self.cleanup_exit_status,
                stdout=b"removed\n" if self.cleanup_exit_status == 0 else b"",
                stderr=b"" if self.cleanup_exit_status == 0 else b"simulated cleanup failure\n",
            )
        if (
            len(command) == 6
            and command[:3] == ("docker", "image", "inspect")
            and command[3].startswith("trafficlab-validation-")
            and (command[4:] == ("--format", "{{.Id}}"))
        ):
            present = command[3] in self.owned_capture_tags
            return subprocess.CompletedProcess(
                command,
                0 if present else 1,
                stdout=f"{self.capture_image_id}\n".encode() if present else b"",
                stderr=b"" if present else b"not present\n",
            )
        if (
            len(command) == 6
            and command[:3] == ("docker", "image", "inspect")
            and command[3].startswith("sha256:")
            and (command[4:] == ("--format", "{{.Id}}"))
        ):
            inspected_capture_image_id = self.inspected_capture_image_id or self.capture_image_id
            return subprocess.CompletedProcess(
                command,
                0 if self.capture_image_present else 1,
                stdout=f"{inspected_capture_image_id}\n".encode() if self.capture_image_present else b"",
                stderr=b"" if self.capture_image_present else b"not present\n",
            )
        if command in {
            ("docker", "image", "inspect", self.capture_image_id),
            ("docker", "image", "inspect", self.capture_image_id, "--format", "{{.Id}}"),
        } and (not self.capture_image_present):
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not present\n")
        if command == ("docker", "image", "inspect", TARGET_REFERENCE) and self.on_target_inspect is not None:
            self.on_target_inspect()
        outputs: dict[tuple[str, ...], bytes] = {
            ("git", "rev-parse", "HEAD"): b"c" * 40 + b"\n",
            ("git", "rev-parse", "HEAD^{tree}"): b"e" * 40 + b"\n",
            ("git", "status", "--porcelain=v1", "--untracked-files=all"): b" M source.py\n" if self.dirty else b"",
            ("docker", "version", "--format", "{{.Server.Version}}"): f"{self.docker_engine_version}\n".encode(),
            ("docker", "compose", "version", "--short"): f"{self.docker_compose_version}\n".encode(),
            ("docker", "image", "inspect", TARGET_REFERENCE): json.dumps(
                [
                    {
                        "Id": self.target_image_id,
                        "RepoDigests": list(self.target_repo_digests),
                        "Config": {"User": self.target_config_user},
                    }
                ]
            ).encode(),
            ("docker", "image", "inspect", self.capture_image_id): json.dumps([{"Id": self.capture_image_id}]).encode(),
            (
                "docker",
                "image",
                "inspect",
                self.capture_image_id,
                "--format",
                "{{.Id}}",
            ): f"{self.capture_image_id}\n".encode(),
        }
        if command not in outputs:
            raise AssertionError(f"unexpected study command: {command!r}")
        return subprocess.CompletedProcess(command, 0, stdout=outputs[command], stderr=b"")
