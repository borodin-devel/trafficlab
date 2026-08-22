"""Cohesive fitting behavior tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import trafficlab.artifacts.best_model as artifacts
import trafficlab.fitting.stage as fitting
from tests.support.fitting import (
    build_config,
    build_dependencies,
    build_inputs,
    build_outcome,
    valid_best_bytes,
)
from trafficlab.artifacts.best_model import publish_best_model
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scientific_schema import ScientificArtifactSchemaError
from trafficlab.fitting.stage import fit_experiment


@pytest.mark.parametrize("semantic", [True, False], ids=["schema", "publication"])
def test_fit_retains_the_owning_best_model_publisher_classification(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, semantic: bool
) -> None:
    """A publisher's typed schema error must not be overwritten by the collision fallback."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    def fail_publication(_path: Path, _content: bytes) -> object:
        if semantic:
            raise ScientificArtifactSchemaError(
                "best model schema is incompatible",
                corrective_action="refit under the current schema",
            )
        raise TrafficlabError("injected publication conflict", corrective_action="preserve the conflicting model")

    monkeypatch.setattr(fitting, "publish_best_model", fail_publication)

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == ("scientific_semantics_incompatible" if semantic else "publication_collision")


def test_best_model_is_exclusive_except_validated_identical_reuse(tmp_path: Path) -> None:
    """A rerun may prove exact identity but must never overwrite a distinct completed model."""
    destination = tmp_path / "best_model.json"
    first_content = valid_best_bytes(gene=1.0)
    other_content = valid_best_bytes(gene=1.5)

    first = publish_best_model(destination, first_content)
    reused = publish_best_model(destination, first_content)

    assert (first.path, first.content, first.created_by_call) == (destination, first_content, True)
    assert (reused.path, reused.content, reused.created_by_call) == (destination, first_content, False)
    with pytest.raises(TrafficlabError, match=r"best_model\.json already exists"):
        publish_best_model(destination, other_content)
    assert destination.read_bytes() == first_content


def test_best_model_rejects_malformed_prospective_or_existing_bytes_without_replacement(tmp_path: Path) -> None:
    """Byte equality alone cannot bless malformed state, and rejected caller state must be preserved."""
    destination = tmp_path / "best_model.json"
    with pytest.raises(TrafficlabError, match="best model|best-model"):
        publish_best_model(destination, b"{}\n")
    assert not destination.exists()

    document = json.loads(valid_best_bytes())
    noncanonical = (json.dumps(document, indent=2) + "\n").encode()
    with pytest.raises(TrafficlabError, match="not canonical"):
        publish_best_model(destination, noncanonical)
    assert not destination.exists()

    malformed = b"caller-owned malformed model\n"
    destination.write_bytes(malformed)
    with pytest.raises(TrafficlabError, match="best model|JSON"):
        publish_best_model(destination, valid_best_bytes())
    assert destination.read_bytes() == malformed


def test_best_model_reports_an_unreadable_existing_destination_without_replacing_it(tmp_path: Path) -> None:
    """A non-readable destination must be preserved instead of being treated as absence."""
    destination = tmp_path / "best_model.json"
    destination.mkdir()

    with pytest.raises(TrafficlabError, match="could not read best model"):
        publish_best_model(destination, valid_best_bytes())

    assert destination.is_dir()


def test_best_model_rejects_and_preserves_a_dangling_destination_symlink(tmp_path: Path) -> None:
    """A dangling symlink is an existing malformed artifact entry, not permission to publish through its name."""
    destination = tmp_path / "best_model.json"
    destination.symlink_to(tmp_path / "missing-target.json")

    with pytest.raises(TrafficlabError, match="existing best model entry.*unreadable"):
        publish_best_model(destination, valid_best_bytes())

    assert destination.is_symlink()
    assert os.readlink(destination) == str(tmp_path / "missing-target.json")
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_absence_probe_oserror_is_translated_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed lstat cannot be treated as proof that an exclusive destination name is absent."""
    destination = tmp_path / "best_model.json"
    real_read_bytes = Path.read_bytes
    real_lstat = Path.lstat

    def missing_read(path: Path) -> bytes:
        if path == destination:
            raise FileNotFoundError("injected missing read")
        return real_read_bytes(path)

    def fail_lstat(path: Path) -> os.stat_result:
        if path == destination:
            raise OSError("injected lstat failure")
        return real_lstat(path)

    monkeypatch.setattr(Path, "read_bytes", missing_read)
    monkeypatch.setattr(Path, "lstat", fail_lstat)

    with pytest.raises(TrafficlabError, match="could not inspect best model entry.*lstat failure"):
        publish_best_model(destination, valid_best_bytes())

    assert not destination.exists()


@pytest.mark.parametrize("collision", [False, True], ids=["existing", "link-race-winner"])
def test_best_model_reuse_rejects_an_entry_replaced_immediately_after_its_validation_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision: bool,
) -> None:
    """Validated best-model bytes cannot authorize reuse of a subsequently replaced canonical entry."""
    destination = tmp_path / "best_model.json"
    expected = valid_best_bytes(gene=1.0)
    replacement = valid_best_bytes(gene=1.5)
    if not collision:
        destination.write_bytes(expected)

    real_read_bytes = Path.read_bytes
    real_link = os.link
    replaced = False

    def replace_after_read(path: Path) -> bytes:
        nonlocal replaced
        content = real_read_bytes(path)
        if path == destination and not replaced:
            replacement_path = tmp_path / "replacement-best-model.json"
            replacement_path.write_bytes(replacement)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(expected)
        real_link(source, target)

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    if collision:
        monkeypatch.setattr(artifacts.os, "link", collide)

    with pytest.raises(TrafficlabError, match="changed during.*validation"):
        publish_best_model(destination, expected)

    assert replaced is True
    assert real_read_bytes(destination) == replacement
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


@pytest.mark.parametrize("racing_bytes_match", [True, False])
def test_best_model_publication_race_preserves_and_validates_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, racing_bytes_match: bool
) -> None:
    """Losing an exclusive-link race must validate the winner and clean only this call's temporary file."""
    destination = tmp_path / "best_model.json"
    prospective = valid_best_bytes(gene=1.0)
    winner = prospective if racing_bytes_match else valid_best_bytes(gene=1.5)
    real_link = os.link

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(winner)
        real_link(source, target)

    monkeypatch.setattr(artifacts.os, "link", collide)

    if racing_bytes_match:
        publication = publish_best_model(destination, prospective)
        assert publication.created_by_call is False
    else:
        with pytest.raises(TrafficlabError, match=r"best_model\.json already exists"):
            publish_best_model(destination, prospective)

    assert destination.read_bytes() == winner
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_race_winner_directory_entry_is_made_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A losing publisher must durably acknowledge the racing winner before reporting identical reuse."""
    destination = tmp_path / "best_model.json"
    content = valid_best_bytes()
    real_link = os.link
    real_open = os.open
    events: list[str] = []

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(content)
        real_link(source, target)

    def observed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & getattr(os, "O_DIRECTORY", 0):
            events.append("directory_open")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifacts.os, "link", collide)
    monkeypatch.setattr(artifacts.os, "open", observed_open)

    publication = publish_best_model(destination, content)

    assert publication.created_by_call is False
    assert events == ["directory_open"]


def test_best_model_reports_a_disappearing_collision_winner_without_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A race winner that disappears before validation is an actionable publication error, never an assertion."""
    destination = tmp_path / "best_model.json"
    real_link = os.link

    def disappear(source: str | Path, target: str | Path) -> None:
        target_path = Path(target)
        target_path.write_bytes(valid_best_bytes())
        try:
            real_link(source, target)
        except FileExistsError:
            target_path.unlink()
            raise

    monkeypatch.setattr(artifacts.os, "link", disappear)

    with pytest.raises(TrafficlabError, match="publication race winner disappeared"):
        publish_best_model(destination, valid_best_bytes())

    assert not destination.exists()
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_publication_fsyncs_the_containing_directory_after_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flushed temporary file alone does not make the new directory entry crash-durable."""
    destination = tmp_path / "best_model.json"
    events: list[str] = []
    real_link = os.link
    real_open = os.open

    def observed_link(source: str | Path, target: str | Path) -> None:
        events.append("link")
        real_link(source, target)

    def observed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & getattr(os, "O_DIRECTORY", 0):
            events.append("directory_open")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifacts.os, "link", observed_link)
    monkeypatch.setattr(artifacts.os, "open", observed_open)

    publish_best_model(destination, valid_best_bytes())

    assert events == ["link", "directory_open"]


def test_best_model_directory_durability_failure_preserves_the_published_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-link directory failure is reportable but must never roll back the exclusive winner."""
    destination = tmp_path / "best_model.json"
    content = valid_best_bytes()
    real_open = os.open

    def fail_directory_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & getattr(os, "O_DIRECTORY", 0):
            raise OSError("injected directory durability failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifacts.os, "open", fail_directory_open)

    with pytest.raises(TrafficlabError, match="directory durability failure.*destination may be present") as caught:
        publish_best_model(destination, content)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "publication_failed"
    assert outcome.stage == "fit"
    assert outcome.affected_evidence == "best_model.json"
    assert outcome.evidence_state == "preserved"
    assert destination.read_bytes() == content
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_post_link_temp_cleanup_failure_preserves_the_published_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owned-temp cleanup failure after publication must not delete or overwrite the valid winner."""
    destination = tmp_path / "best_model.json"
    content = valid_best_bytes()
    real_unlink = os.unlink
    attempts = 0

    def fail_temp_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object, **kwargs: object
    ) -> None:
        nonlocal attempts
        if Path(os.fsdecode(path)).name.startswith(".best_model.json."):
            attempts += 1
            raise OSError("injected post-link cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", fail_temp_unlink)

    with pytest.raises(TrafficlabError, match="was published.*post-link cleanup failure"):
        publish_best_model(destination, content)

    assert attempts == 1
    assert destination.read_bytes() == content


def test_best_model_rejects_a_changed_persisted_temporary_copy_before_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation must inspect exact persisted temporary bytes, not only the prospective in-memory model."""
    destination = tmp_path / "best_model.json"
    content = valid_best_bytes()
    real_read_bytes = Path.read_bytes

    def changed_temp(path: Path) -> bytes:
        if path.name.startswith(".best_model.json."):
            return b"changed after fsync\n"
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", changed_temp)

    with pytest.raises(TrafficlabError, match="persisted temporary best model differs"):
        publish_best_model(destination, content)

    assert not destination.exists()
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_link_and_cleanup_failure_reports_both_without_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication failure cleanup stays limited to its owned temp and retains both failure details."""
    destination = tmp_path / "best_model.json"
    real_unlink = os.unlink
    cleanup_attempts = 0

    def fail_link(_source: str | Path, _target: str | Path) -> None:
        raise OSError("injected link failure")

    def fail_temp_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object, **kwargs: object
    ) -> None:
        nonlocal cleanup_attempts
        if Path(os.fsdecode(path)).name.startswith(".best_model.json."):
            cleanup_attempts += 1
            raise OSError("injected failure cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "link", fail_link)
    monkeypatch.setattr(artifacts.os, "unlink", fail_temp_unlink)

    with pytest.raises(TrafficlabError, match="injected link failure.*cleanup incomplete.*cleanup failure"):
        publish_best_model(destination, valid_best_bytes())

    assert cleanup_attempts == 1
    assert not destination.exists()


def test_best_model_unexpected_link_error_propagates_after_owned_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected programming failures must not be disguised as expected publication errors."""
    destination = tmp_path / "best_model.json"

    def fail_link(_source: str | Path, _target: str | Path) -> None:
        raise RuntimeError("injected unexpected link defect")

    monkeypatch.setattr(artifacts.os, "link", fail_link)

    with pytest.raises(RuntimeError, match="unexpected link defect"):
        publish_best_model(destination, valid_best_bytes())

    assert not destination.exists()
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_temp_creation_failure_has_no_cleanup_side_effect(tmp_path: Path) -> None:
    """A failure before temp ownership must report the write boundary without attempting cleanup."""
    missing_parent = tmp_path / "missing"
    destination = missing_parent / "best_model.json"

    with pytest.raises(TrafficlabError, match="could not publish best model"):
        publish_best_model(destination, valid_best_bytes())

    assert not missing_parent.exists()
