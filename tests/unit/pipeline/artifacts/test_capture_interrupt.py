# pyright: reportPrivateUsage=false
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import trafficlab.artifacts.capture as artifacts
from tests.support.artifacts import capture_sources
from trafficlab.artifacts.capture import CapturePublication
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError


class _InterruptingTemporary:
    def __init__(self, real_factory: Any, *args: object, **kwargs: object) -> None:
        self._context = real_factory(*args, **kwargs)
        self._stream: Any = None

    def __enter__(self) -> _InterruptingTemporary:
        self._stream = self._context.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self._context.__exit__(*args)

    @property
    def name(self) -> str:
        return self._stream.name

    def write(self, content: bytes) -> int:
        del content
        raise KeyboardInterrupt


def test_publish_capture_pair_cleans_creator_temporary_on_copy_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_factory = artifacts.tempfile.NamedTemporaryFile

    def interrupting_factory(*args: object, **kwargs: object) -> _InterruptingTemporary:
        return _InterruptingTemporary(real_factory, *args, **kwargs)

    monkeypatch.setattr(artifacts.tempfile, "NamedTemporaryFile", interrupting_factory)

    with pytest.raises(KeyboardInterrupt):
        artifacts.publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert not tuple(run_directory.glob(".capture-pair.*"))
    assert not (run_directory / "capture.json").exists()
    assert not (run_directory / "reference.pcapng").exists()


@pytest.mark.parametrize("link_call", [1, 2])
def test_publish_capture_pair_cleans_temporaries_and_preserves_links_on_link_interrupt(
    link_call: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_link = os.link
    calls = 0

    def interrupted_link(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == link_call:
            raise KeyboardInterrupt
        real_link(source, destination)

    monkeypatch.setattr(artifacts.os, "link", interrupted_link)

    with pytest.raises(KeyboardInterrupt):
        artifacts.publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert not tuple(run_directory.glob(".capture-pair.*"))
    assert (run_directory / "capture.json").exists() is (link_call == 2)
    assert not (run_directory / "reference.pcapng").exists()


def test_publish_capture_pair_deadline_expires_inside_copy_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    monkeypatch.setattr(artifacts, "_CAPTURE_COPY_CHUNK_SIZE", 1)
    values = iter((0.0, 0.0, 2.0))

    def forbidden_validation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("deadline-expired copy reached validation")

    monkeypatch.setattr(artifacts, "validate_capture_pair", forbidden_validation)
    with pytest.raises(DeadlineExceededError, match="publication copy"):
        artifacts.publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=1.0,
            clock=lambda: next(values),
        )

    assert not tuple(run_directory.glob(".capture-pair.*"))


@pytest.mark.parametrize("boundary", ["copy-interrupt", "copy-deadline", "link-interrupt"])
def test_publish_capture_pair_reports_cleanup_failure_at_interruptible_boundaries(
    boundary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_unlink = os.unlink

    def failed_temp_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path).name.startswith(".capture-pair."):
            raise OSError("interrupt cleanup sentinel")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", failed_temp_unlink)

    def constant_clock() -> float:
        return 0.0

    clock: Callable[[], float]
    if boundary == "copy-interrupt":
        real_factory = artifacts.tempfile.NamedTemporaryFile

        def interrupting_factory(*args: object, **kwargs: object) -> _InterruptingTemporary:
            return _InterruptingTemporary(real_factory, *args, **kwargs)

        monkeypatch.setattr(
            artifacts.tempfile,
            "NamedTemporaryFile",
            interrupting_factory,
        )
        clock = constant_clock
        deadline = None
    elif boundary == "copy-deadline":
        monkeypatch.setattr(artifacts, "_CAPTURE_COPY_CHUNK_SIZE", 1)
        values = iter((0.0, 2.0))

        def sequence_clock() -> float:
            return next(values)

        clock = sequence_clock
        deadline = 1.0
    else:

        def interrupted_link(*_args: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(artifacts.os, "link", interrupted_link)
        clock = constant_clock
        deadline = None

    with pytest.raises(TrafficlabError, match="cleanup incomplete.*interrupt cleanup sentinel"):
        artifacts.publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=deadline,
            clock=clock,
        )


def test_capture_diagnostic_removal_and_created_publication_rollback(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    artifacts.remove_stable_capture_diagnostics(run_directory)
    sources = capture_sources(tmp_path / "sources")
    sources[0].rename(run_directory / "diagnostic-capture.json")
    sources[1].rename(run_directory / "diagnostic-reference.pcapng")
    artifacts.remove_stable_capture_diagnostics(run_directory)
    assert not tuple(run_directory.iterdir())

    sources = capture_sources(tmp_path / "second-sources")
    publication = artifacts.publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )
    assert isinstance(publication, CapturePublication)
    artifacts.rollback_capture_publication(run_directory, publication)
    assert not tuple(run_directory.iterdir())


def test_exclusive_capture_publication_preserves_any_existing_entry(tmp_path: Path) -> None:
    sources = capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    canonical = run_directory / "capture.json"
    canonical.symlink_to(run_directory / "missing")

    with pytest.raises(TrafficlabError, match="already exists"):
        artifacts.publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
            recover_invalid=False,
        )

    assert canonical.is_symlink()


def test_exclusive_capture_publication_creates_an_absent_pair(tmp_path: Path) -> None:
    sources = capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    publication = artifacts.publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
        recover_invalid=False,
    )

    assert publication.created_by_call is True
