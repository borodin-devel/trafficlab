"""Cli behavior."""

from __future__ import annotations

import subprocess
import tempfile as tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.collection as vs_collection
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.records as vs_records
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner, write_prerequisite_repository_inputs
from trafficlab.common.errors import TrafficlabError


def test_collection_rejects_old_id_after_prerequisite_rotation_but_keeps_its_raw_archive(tmp_path: Path) -> None:
    """The collector accepts only the current canonical root, never an old ignored archive."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )
    r4_bytes = r4_archive.read_bytes()
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    vs_prereq_run.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"

    def forbidden_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("old collection must fail before environment work")

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        vs_collection.collection_inputs_from_prerequisites(
            repository,
            canonical,
            study_id=r4.study_id,
            url=r4.url,
            runner=cast(vs_records.CommandRunner, forbidden_runner),
            require_successful_prerequisite=True,
        )

    assert r4_archive.read_bytes() == r4_bytes


def test_collect_cli_freezes_its_attempt_before_any_input_bridge_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every post-syntax collection failure consumes the study ID before bridge validation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    marker = repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    calls = 0

    def reject_bridge(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert marker.is_file()
        raise TrafficlabError("synthetic input bridge failure", corrective_action="preserve the attempt")

    monkeypatch.setattr(vs_cli, "collection_inputs_from_prerequisites", reject_bridge)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )

    assert vs_cli.main(argv, repository_root=repository) == 2
    assert marker.is_file()
    assert not candidate.exists()
    assert vs_cli.main(argv, repository_root=repository) == 2
    assert calls == 1
