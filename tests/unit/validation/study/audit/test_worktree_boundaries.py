"""Worktree Boundaries behavior."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

import pytest

import scripts.validation_study.audit.artifacts as vs_audit_artifacts
import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.environment as vs_audit_environment
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
from tests.support.validation_study.repository import copy_validation_study_candidate
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (("different", "artifact_foreign"), ("invalid", "artifact_corrupt"), ("noncanonical", "artifact_foreign")),
)
def test_offline_auditor_rejects_untrusted_fixture_profile_source_bytes(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    """Fixture compatibility derives its profile from checked source bytes, never candidate bytes."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_bytes()))
    source = repository / "examples/data/fit/experiment.toml"
    original = source.read_bytes()
    if case == "different":
        source.write_bytes(b"different\n")
    else:
        replacement = b"invalid = [\n" if case == "invalid" else b"# retained comment\n" + original
        source.write_bytes(replacement)

        def recorded_fixture_profile(*_args: object, **_kwargs: object) -> bytes:
            return replacement

        monkeypatch.setattr(vs_audit_artifacts, "git_bytes", recorded_fixture_profile)

    with pytest.raises(vs_audit_common.Issue) as error:
        vs_audit_artifacts.fixture_profile(
            repository,
            source_commit=cast(str, environment["source_commit"]),
            workload="short",
            url="https://downloads.example.test/object.bin",
            environment=environment,
        )

    assert error.value.kind == expected_kind


@pytest.mark.parametrize("failure", ("directory", "entry"))
def test_prerequisite_worktree_entry_scan_rejects_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["directory", "entry"],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    entry = repository / "source.py"
    entry.write_text("pass\n", encoding="utf-8")

    if failure == "directory":

        def unavailable_iterdir(path: Path) -> Any:
            assert path == repository
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "iterdir", unavailable_iterdir)
    else:

        def one_entry(path: Path) -> Any:
            assert path == repository
            return iter((entry,))

        def unavailable_lstat(path: Path) -> Any:
            assert path == entry
            raise OSError("entry unavailable")

        monkeypatch.setattr(Path, "iterdir", one_entry)
        monkeypatch.setattr(Path, "lstat", unavailable_lstat)

    with pytest.raises(ValueError):
        vs_prereq_commands._prerequisite_worktree_entries(repository)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_worktree_cleanliness_rejects_unignored_special_entries(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "ordinary.txt").write_text("ordinary\n", encoding="utf-8")
    special = repository / "foreign.fifo"
    os.mkfifo(special)
    runner = ScriptedPrerequisiteRunner(repository)
    runner.ignored_worktree_paths = frozenset()

    with pytest.raises(ValueError, match="non-regular entry"):
        vs_prereq_commands.require_clean_prerequisite_worktree(repository, runner=runner)


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (".superpowers/state", True),
        (".coverage", True),
        ("TASK.md", True),
        (".env.local", True),
        (".coverage.local", True),
        ("pkg/__pycache__/x.pyc", True),
        ("pkg.egg-info/METADATA", True),
        ("module.pyd", True),
        ("collector.log", True),
        ("runs/local/state.json", True),
        ("examples/validation_study/configs/short.toml", True),
        ("examples/validation_study/results.json", True),
        ("examples/validation_study/.study-work/state", True),
        ("examples/validation_study/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.study-1.tmp/state", True),
        ("foreign.py", False),
    ),
)
def test_auditor_ignored_worktree_path_policy_is_explicit(path: str, expected: bool) -> None:
    assert vs_audit_environment._permitted_ignored_relocated_worktree_path(path) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (".superpowers/state", True),
        (".coverage", True),
        ("TASK.md", True),
        (".env.local", True),
        (".coverage.local", True),
        ("pkg/__pycache__/x.pyc", True),
        ("pkg.egg-info/METADATA", True),
        ("module.pyd", True),
        ("collector.log", True),
        ("runs/local/state.json", True),
        ("examples/validation_study/prerequisites.json", True),
        ("examples/validation_study/results.json", True),
        ("examples/validation_study/configs/short.toml", True),
        ("examples/validation_study/.study-work/state", True),
        ("examples/validation_study/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.study-1.tmp/state", True),
        ("foreign.py", False),
    ),
)
def test_prerequisite_ignored_worktree_path_policy_is_explicit(path: str, expected: bool) -> None:
    assert vs_prereq_commands._permitted_ignored_prerequisite_worktree_path(path) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("status", (b"", b"?? foreign\0"))
def test_auditor_worktree_status_parser_accepts_empty_and_canonical_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: bytes,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git_bytes(*_args: object, **_kwargs: object) -> bytes:
        return status

    monkeypatch.setattr(vs_audit_environment, "git_bytes", git_bytes)
    vs_audit_environment._relocated_worktree_paths(repository)  # pyright: ignore[reportPrivateUsage]


def test_auditor_worktree_entry_scan_covers_regular_directory_special_and_skipped_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "source.py").write_text("pass\n", encoding="utf-8")
    nested = repository / "nested"
    nested.mkdir()
    (nested / "child.py").write_text("pass\n", encoding="utf-8")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    (repository / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (repository / ".venv" / "deep").mkdir(parents=True)
    (repository / ".venv" / "deep" / "ignored.py").write_text("pass\n", encoding="utf-8")
    special = repository / "special.fifo"
    os.mkfifo(special)

    entries, nonregular = vs_audit_environment._relocated_worktree_entry_paths(  # pyright: ignore[reportPrivateUsage]
        repository,
        candidate_paths=("candidate.txt",),
    )

    assert "source.py" in entries
    assert "nested/child.py" in entries
    assert "candidate.txt" not in entries
    assert ".git" not in entries
    assert ".venv/deep/ignored.py" not in entries
    assert nonregular == ("special.fifo",)


def test_prerequisite_cleanliness_uses_real_git_stdin_nul_records_for_ignored_foreign_names(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True, capture_output=True)
    names = ("foreign space", "foreign\nnewline")
    (repository / ".git" / "info" / "exclude").write_text("foreign*\n", encoding="utf-8")
    for name in names:
        (repository / name).write_text("ignored\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert cwd == repository
        assert check is False
        assert capture_output is True
        assert shell is False
        assert timeout == vs_common.SUBPROCESS_TIMEOUTS["git_or_version"]
        calls.append((tuple(argv), input))
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    with pytest.raises(ValueError, match="ignored prerequisite worktree entry is not permitted"):
        vs_prereq_commands.require_clean_prerequisite_worktree(repository, runner=runner)

    check_ignore = [call for call in calls if call[0][:3] == ("git", "check-ignore", "-z")]
    assert check_ignore == [
        (
            ("git", "check-ignore", "-z", "--stdin"),
            b"".join(os.fsencode(name) + b"\0" for name in sorted(names)),
        )
    ]


def test_prerequisite_cleanliness_rejects_non_utf8_ignored_git_record_after_byte_exact_input(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True, capture_output=True)
    raw_name = b"foreign-\xff"
    name = os.fsdecode(raw_name)
    (repository / ".git" / "info" / "exclude").write_text("foreign*\n", encoding="utf-8")
    (repository / name).write_text("ignored\n", encoding="utf-8")
    check_ignore_inputs: list[bytes | None] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert cwd == repository
        if tuple(argv[:3]) == ("git", "check-ignore", "-z"):
            check_ignore_inputs.append(input)
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    with pytest.raises(ValueError, match="ignored prerequisite path is not UTF-8"):
        vs_prereq_commands.require_clean_prerequisite_worktree(repository, runner=runner)

    assert check_ignore_inputs == [raw_name + b"\0"]


def test_prerequisite_ignored_path_codec_skips_real_git_for_an_empty_path_set(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[tuple[str, ...]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(argv))
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    assert vs_prereq_commands._ignored_prerequisite_worktree_paths(repository, (), runner=runner) == frozenset()  # pyright: ignore[reportPrivateUsage]
    assert calls == []
