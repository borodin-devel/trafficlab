"""Run owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from scripts.validation_study.common import (
    PRESERVED_PRE_USER_AGENT_R6_STUDY_ID,
    JsonObject,
    candidate_identity,
    canonical_json,
    path_entry_exists,
    read_regular_prerequisite_rotation_target,
    require,
    write_candidate_bytes,
)
from scripts.validation_study.prerequisites.codec import (
    parse_prerequisite_results,
    parse_preserved_pre_user_agent_r6_predecessor,
    prerequisite_document,
    render_prerequisite_results,
    render_successful_prerequisite_marker,
    require_successful_prerequisite_marker_content,
    validate_prerequisite_document,
)
from scripts.validation_study.rotation.schema import (
    PrerequisiteRotationTarget,
    collection_attempt_root,
    parse_prerequisite_rotation_journal,
    prerequisite_raw_archive_path,
    prerequisite_rotation_expected_targets,
    prerequisite_rotation_journal_path,
    render_prerequisite_rotation_journal,
)
from scripts.validation_study.workloads import build_base_config, config_workload, portable_base_config, workload_specs
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError

if TYPE_CHECKING:
    from scripts.validation_study.common import WorkloadName
    from scripts.validation_study.records import CommandRunner, PrerequisiteResults


def _stage_prerequisite_rotation_file(
    destination: Path, content: bytes, *, validate: Callable[[Path, bytes], None], suffix: str = ".tmp"
) -> Path:
    """Write, fsync, reread, and validate a private prerequisite-publication staging file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=suffix, dir=destination.parent
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted = temporary.read_bytes()
        require(persisted == content, "staged prerequisite publication bytes changed before validation")
        validate(temporary, persisted)
        return temporary
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _fsync_prerequisite_rotation_directory(destination: Path) -> None:
    """Fsync the parent directory after a rotation-owned entry mutation."""
    descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit_prerequisite_fsync(destination: Path) -> None:
    """Durably record one committed prerequisite-publication directory entry."""
    _fsync_prerequisite_rotation_directory(destination)


def _after_prerequisite_rotation_commit(_destination: Path) -> None:
    """Private crash-injection seam after one durable rotation boundary."""


def _publish_prerequisite_rotation_exclusive_file(
    destination: Path, content: bytes, *, validate: Callable[[bytes], None], name: str
) -> None:
    """Durably link one previously-absent rotation file without replacement."""
    stage = _stage_prerequisite_rotation_file(
        destination, content, validate=lambda _stage, persisted: validate(persisted)
    )
    published = False
    try:
        os.link(stage, destination)
        published = True
        _fsync_prerequisite_rotation_directory(destination)
        persisted = read_regular_prerequisite_rotation_target(destination, name=name)
        require(persisted == content, f"published {name} bytes changed")
        validate(persisted)
    except BaseException:
        if published:
            try:
                persisted = read_regular_prerequisite_rotation_target(destination, name=name)
                if persisted == content:
                    destination.unlink()
                    _fsync_prerequisite_rotation_directory(destination)
            except OSError:
                pass
        raise
    finally:
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            pass


def validate_base_configs(
    repository_root: Path, prerequisites: PrerequisiteResults, *, require_absent_run_directories: bool = True
) -> dict[WorkloadName, ExperimentConfig]:
    root = repository_root.resolve()
    validated_prerequisites = validate_prerequisite_document(prerequisite_document(prerequisites), repository_root=root)
    capture_image_id = cast(str, validated_prerequisites.images["capture_image_id"])
    hashes = validated_prerequisites.config_sha256
    result: dict[WorkloadName, ExperimentConfig] = {}
    for workload in workload_specs(validated_prerequisites.url):
        path = root / "examples" / "validation_study" / "configs" / f"{workload.name}.toml"
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ValueError(f"could not read checked {workload.name} config: {error}") from error
        require(
            hashlib.sha256(content).hexdigest() == hashes[workload.name],
            f"checked {workload.name} config SHA-256 must equal prerequisite evidence",
        )
        config = load_experiment(path)
        expected = build_base_config(
            workload,
            repository_root=root,
            study_id=validated_prerequisites.study_id,
            url=validated_prerequisites.url,
            capture_image_id=capture_image_id,
            require_absent_run_directory=require_absent_run_directories,
        )
        require(config == expected, f"checked {workload.name} config must equal every locked Validation Study value")
        expected_content = render_effective_config(
            portable_base_config(
                expected,
                repository_root=root,
                workload=workload,
                require_absent_run_directory=require_absent_run_directories,
            )
        )
        require(content == expected_content, f"checked {workload.name} config must use exact portable TOML")
        result[workload.name] = config
    return result


def _archive_prerequisite_raw_document(repository_root: Path, *, study_id: str, content: bytes) -> bytes:
    """Persist the byte-exact canonical prerequisite document beside its irreversible attempt."""
    archive = prerequisite_raw_archive_path(repository_root, study_id)

    def validate(persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=repository_root)
        require(render_prerequisite_results(parsed) == content, "archived prerequisite document is not canonical")

    if path_entry_exists(archive):
        persisted = read_regular_prerequisite_rotation_target(archive, name="archived prerequisite document")
    else:
        _publish_prerequisite_rotation_exclusive_file(
            archive, content, validate=validate, name="archived prerequisite document"
        )
        persisted = read_regular_prerequisite_rotation_target(archive, name="archived prerequisite document")
    require(persisted == content, "archived prerequisite document must equal the canonical publication bytes")
    validate(persisted)
    return persisted


def _archive_preserved_pre_user_agent_r6_predecessor(
    repository_root: Path, *, content: bytes, runner: CommandRunner
) -> bytes:
    """Persist the exact retained r6 predecessor without exposing a general legacy codec."""
    archive = prerequisite_raw_archive_path(repository_root, PRESERVED_PRE_USER_AGENT_R6_STUDY_ID)

    def validate(persisted: bytes) -> None:
        parse_preserved_pre_user_agent_r6_predecessor(persisted, repository_root=repository_root, runner=runner)

    if path_entry_exists(archive):
        persisted = read_regular_prerequisite_rotation_target(
            archive, name="archived preserved pre-User-Agent r6 prerequisite document"
        )
    else:
        _publish_prerequisite_rotation_exclusive_file(
            archive, content, validate=validate, name="archived preserved pre-User-Agent r6 prerequisite document"
        )
        persisted = read_regular_prerequisite_rotation_target(
            archive, name="archived preserved pre-User-Agent r6 prerequisite document"
        )
    require(persisted == content, "archived preserved pre-User-Agent r6 document must equal canonical root bytes")
    validate(persisted)
    return persisted


def begin_phase_attempt(
    repository_root: Path, *, study_id: str, url: str, phase: Literal["prerequisites", "collection"]
) -> Path:
    """Persist one irreversible phase marker immediately after input syntax checks."""
    attempt = collection_attempt_root(repository_root, study_id)
    marker = attempt / f"{phase}.json"
    if path_entry_exists(marker):
        raise TrafficlabError(
            f"Validation Study {phase} already began for {study_id}; use a new study ID",
            corrective_action="preserve the failed attempt and restart with a new study ID",
        )
    write_candidate_bytes(marker, canonical_json(cast(JsonObject, {"phase": phase, "study_id": study_id, "url": url})))
    return attempt


def _publish_prerequisite_rotation_journal(
    repository_root: Path, *, study_id: str, targets: Sequence[PrerequisiteRotationTarget]
) -> Path:
    journal = prerequisite_rotation_journal_path(repository_root, study_id)
    content = render_prerequisite_rotation_journal(repository_root, study_id=study_id, targets=targets)

    def validate(persisted: bytes) -> None:
        parsed_study_id, _parsed_targets = parse_prerequisite_rotation_journal(
            persisted, repository_root=repository_root, journal=journal
        )
        require(parsed_study_id == study_id, "prerequisite rotation journal study ID changed")

    _publish_prerequisite_rotation_exclusive_file(
        journal, content, validate=validate, name="prerequisite rotation journal"
    )
    return journal


def _read_prerequisite_rotation_target_if_present(destination: Path, *, name: str) -> bytes | None:
    if not path_entry_exists(destination):
        return None
    return read_regular_prerequisite_rotation_target(destination, name=name)


def _remove_owned_prerequisite_rotation_path(path: Path, *, identity: JsonObject, name: str) -> None:
    content = _read_prerequisite_rotation_target_if_present(path, name=name)
    if content is None:
        return
    require(candidate_identity(content) == identity, f"{name} does not match its transaction-owned identity")
    path.unlink()
    _fsync_prerequisite_rotation_directory(path)


def _restore_prerequisite_rotation_target(target: PrerequisiteRotationTarget) -> None:
    """Restore one journal-owned target only when each extant byte sequence is expected."""
    destination_content = _read_prerequisite_rotation_target_if_present(
        target.destination, name=f"prerequisite rotation {target.kind} destination"
    )
    stage = target.stage
    if stage is None:
        raise ValueError("prerequisite rotation target must retain its staged path")
    if target.before_identity is None:
        if destination_content is not None:
            require(
                candidate_identity(destination_content) == target.target_identity,
                f"prerequisite rotation {target.kind} destination is not transaction-owned",
            )
            target.destination.unlink()
            _commit_prerequisite_fsync(target.destination)
    else:
        backup = target.backup
        if backup is None:
            raise ValueError("prerequisite rotation prior bytes require a backup")
        backup_content = _read_prerequisite_rotation_target_if_present(
            backup, name=f"prerequisite rotation {target.kind} backup"
        )
        if backup_content is not None:
            require(
                candidate_identity(backup_content) == target.before_identity,
                f"prerequisite rotation {target.kind} backup bytes changed",
            )
        destination_identity = candidate_identity(destination_content) if destination_content is not None else None
        if destination_identity != target.before_identity:
            require(
                destination_identity is None or destination_identity == target.target_identity,
                f"prerequisite rotation {target.kind} destination is not transaction-owned",
            )
            require(backup_content is not None, f"prerequisite rotation {target.kind} backup is unavailable")
            os.replace(backup, target.destination)
            _commit_prerequisite_fsync(target.destination)
        restored = read_regular_prerequisite_rotation_target(
            target.destination, name=f"restored prerequisite rotation {target.kind} destination"
        )
        require(
            candidate_identity(restored) == target.before_identity,
            f"prerequisite rotation {target.kind} restore bytes changed",
        )
        if backup_content is not None:
            _remove_owned_prerequisite_rotation_path(
                backup, identity=target.before_identity, name=f"prerequisite rotation {target.kind} backup"
            )
    _remove_owned_prerequisite_rotation_path(
        stage, identity=target.target_identity, name=f"prerequisite rotation {target.kind} stage"
    )


def _rollback_prerequisite_rotation(
    committed: Sequence[PrerequisiteRotationTarget],
) -> tuple[list[str], list[PrerequisiteRotationTarget]]:
    """Restore committed prerequisite targets in reverse order after a controlled failure."""
    failures: list[str] = []
    failed_targets: list[PrerequisiteRotationTarget] = []
    for target in reversed(committed):
        try:
            _restore_prerequisite_rotation_target(target)
        except (OSError, ValueError) as error:
            failed_targets.append(target)
            retained = (
                f"; retained recovery backup: {target.backup}"
                if target.backup is not None and path_entry_exists(target.backup)
                else ""
            )
            failures.append(f"{target.destination}: {error}{retained}")
    return (failures, failed_targets)


def _cleanup_prerequisite_rotation_staging(targets: Sequence[PrerequisiteRotationTarget], *, strict: bool) -> list[str]:
    """Discard only journal-owned staging and backup files after restore or success."""
    failures: list[str] = []
    for target in targets:
        owned: list[tuple[Path, JsonObject, str]] = []
        if target.stage is not None:
            owned.append((target.stage, target.target_identity, f"prerequisite rotation {target.kind} stage"))
        if target.backup is not None and target.before_identity is not None:
            owned.append((target.backup, target.before_identity, f"prerequisite rotation {target.kind} backup"))
        for path, identity, name in owned:
            try:
                _remove_owned_prerequisite_rotation_path(path, identity=identity, name=name)
            except (OSError, ValueError) as error:
                if strict:
                    failures.append(f"{path}: {error}")
    return failures


def _prerequisite_rotation_is_complete(
    repository_root: Path, *, study_id: str, targets: Sequence[PrerequisiteRotationTarget]
) -> bool:
    try:
        for target in targets:
            content = _read_prerequisite_rotation_target_if_present(
                target.destination, name=f"prerequisite rotation {target.kind} destination"
            )
            if content is None or candidate_identity(content) != target.target_identity:
                return False
        root_target = next(target for target in targets if target.kind == "root")
        marker_target = next(target for target in targets if target.kind == "marker")
        prerequisite_content = read_regular_prerequisite_rotation_target(
            root_target.destination, name="canonical prerequisite target"
        )
        prerequisite = parse_prerequisite_results(prerequisite_content, repository_root=repository_root)
        require(prerequisite.study_id == study_id, "completed rotation prerequisite study ID is invalid")
        marker_content = read_regular_prerequisite_rotation_target(
            marker_target.destination, name="completed prerequisite success marker"
        )
        require_successful_prerequisite_marker_content(
            marker_content, study_id=study_id, url=prerequisite.url, prerequisite_content=prerequisite_content
        )
        validate_base_configs(repository_root, prerequisite)
    except (OSError, TypeError, ValueError, TrafficlabError):
        return False
    return True


def _clear_prerequisite_rotation_journal(journal: Path) -> None:
    read_regular_prerequisite_rotation_target(journal, name="prerequisite rotation journal")
    journal.unlink()
    _fsync_prerequisite_rotation_directory(journal)


def _recover_prerequisite_rotation_journal(repository_root: Path, journal: Path) -> None:
    content = read_regular_prerequisite_rotation_target(journal, name="prerequisite rotation journal")
    study_id, targets = parse_prerequisite_rotation_journal(content, repository_root=repository_root, journal=journal)
    if _prerequisite_rotation_is_complete(repository_root, study_id=study_id, targets=targets):
        failures = _cleanup_prerequisite_rotation_staging(targets, strict=True)
    else:
        failures, failed_targets = _rollback_prerequisite_rotation(targets)
        failed_target_ids = {id(target) for target in failed_targets}
        failures.extend(
            _cleanup_prerequisite_rotation_staging(
                [target for target in targets if id(target) not in failed_target_ids], strict=True
            )
        )
    if failures:
        raise ValueError(
            f"could not recover prerequisite rotation journal {journal}; retained recovery paths: {'; '.join(failures)}"
        )
    _clear_prerequisite_rotation_journal(journal)


def recover_incomplete_prerequisite_rotations(repository_root: Path) -> None:
    """Recover each durable incomplete rotation before any new phase marker is consumed."""
    attempts = repository_root / "examples" / "validation_study" / ".study-work" / "attempts"
    try:
        mode = attempts.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(f"could not inspect prerequisite attempt directory {attempts}: {error}") from error
    require(
        stat.S_ISDIR(mode) and (not stat.S_ISLNK(mode)), "prerequisite attempt directory must be a regular directory"
    )
    try:
        attempt_paths = sorted(attempts.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ValueError(f"could not enumerate prerequisite attempts {attempts}: {error}") from error
    for attempt in attempt_paths:
        try:
            mode = attempt.lstat().st_mode
        except OSError as error:
            raise ValueError(f"could not inspect prerequisite attempt {attempt}: {error}") from error
        require(stat.S_ISDIR(mode) and (not stat.S_ISLNK(mode)), "prerequisite attempt must be a regular directory")
        journal = attempt / "prerequisites-rotation.json"
        if path_entry_exists(journal):
            _recover_prerequisite_rotation_journal(repository_root, journal)


def _bootstrap_current_prerequisite_archive(
    repository_root: Path, prerequisite_path: Path, *, runner: CommandRunner
) -> None:
    """Preserve a schema-1 canonical root that predates per-attempt raw archives."""
    if not path_entry_exists(prerequisite_path):
        return
    content = read_regular_prerequisite_rotation_target(prerequisite_path, name="canonical prerequisite target")
    try:
        prior = parse_prerequisite_results(content, repository_root=repository_root)
    except ValueError:
        prior = parse_preserved_pre_user_agent_r6_predecessor(content, repository_root=repository_root, runner=runner)
        require_successful_prerequisite_attempt(
            repository_root, study_id=prior.study_id, url=prior.url, prerequisite_content=content, require_archive=False
        )
        _archive_preserved_pre_user_agent_r6_predecessor(repository_root, content=content, runner=runner)
        return
    require_successful_prerequisite_attempt(
        repository_root, study_id=prior.study_id, url=prior.url, prerequisite_content=content, require_archive=False
    )
    _archive_prerequisite_raw_document(repository_root, study_id=prior.study_id, content=content)


def complete_prerequisite_attempt(
    repository_root: Path, *, study_id: str, url: str, prerequisite_content: bytes
) -> None:
    """Record a prerequisite success only after its canonical publication succeeds."""
    archived = _archive_prerequisite_raw_document(repository_root, study_id=study_id, content=prerequisite_content)
    marker = collection_attempt_root(repository_root, study_id) / "prerequisites-success.json"
    _publish_prerequisite_rotation_exclusive_file(
        marker,
        render_successful_prerequisite_marker(study_id=study_id, url=url, prerequisite_content=archived),
        validate=lambda persisted: require_successful_prerequisite_marker_content(
            persisted, study_id=study_id, url=url, prerequisite_content=archived
        ),
        name="successful prerequisite marker",
    )


def require_successful_prerequisite_attempt(
    repository_root: Path, *, study_id: str, url: str, prerequisite_content: bytes, require_archive: bool = True
) -> None:
    """Refuse collection unless the matching prerequisite phase completed successfully."""
    marker = collection_attempt_root(repository_root, study_id) / "prerequisites-success.json"
    try:
        require(
            not path_entry_exists(prerequisite_rotation_journal_path(repository_root, study_id)),
            "collection requires a completed prerequisite rotation",
        )
        content = read_regular_prerequisite_rotation_target(marker, name="successful prerequisite marker")
        require_successful_prerequisite_marker_content(
            content, study_id=study_id, url=url, prerequisite_content=prerequisite_content
        )
        if require_archive:
            archived = read_regular_prerequisite_rotation_target(
                prerequisite_raw_archive_path(repository_root, study_id), name="archived prerequisite document"
            )
            require(
                candidate_identity(archived) == candidate_identity(prerequisite_content),
                "collection requires a matching successful prerequisite marker",
            )
    except (OSError, TypeError, ValueError) as error:
        raise TrafficlabError(
            "Validation Study collection requires a matching successful prerequisite marker",
            corrective_action="complete the same-study prerequisite phase before collection",
        ) from error


def _publish_prerequisite_rotation_target(target: PrerequisiteRotationTarget) -> None:
    """Publish one staged target without overwriting an absent-only archive or marker."""
    stage = target.stage
    if stage is None:
        raise ValueError("prerequisite rotation target must be staged before publication")
    if target.must_be_absent:
        os.link(stage, target.destination)
    else:
        os.replace(stage, target.destination)


def commit_prerequisite_rotation(
    repository_root: Path,
    *,
    prerequisite_path: Path,
    configs: Sequence[tuple[ExperimentConfig, Path, bytes]],
    result: PrerequisiteResults,
    study_id: str,
    url: str,
    runner: CommandRunner,
) -> None:
    """Publish the coupled prerequisite artifacts as one marker-last rollback transaction."""
    root = repository_root.resolve()
    _bootstrap_current_prerequisite_archive(root, prerequisite_path, runner=runner)
    prerequisite_content = render_prerequisite_results(result)
    archive = prerequisite_raw_archive_path(root, study_id)
    marker = collection_attempt_root(root, study_id) / "prerequisites-success.json"
    marker_content = render_successful_prerequisite_marker(
        study_id=study_id, url=url, prerequisite_content=prerequisite_content
    )
    targets: list[tuple[str, Path, bytes, Callable[[Path, bytes], None], bool]] = []

    def validate_archive(_stage: Path, persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=root)
        require(
            render_prerequisite_results(parsed) == prerequisite_content, "staged prerequisite archive is not canonical"
        )

    targets.append(("archive", archive, prerequisite_content, validate_archive, True))
    expected = {
        kind: destination
        for kind, destination, _must_be_absent in prerequisite_rotation_expected_targets(root, study_id)
    }
    config_kinds: list[str] = []
    for config, destination, content in configs:
        workload = config_workload(config)
        kind = f"config-{workload.name}"
        require(kind in expected, "prerequisite rotation has an unknown checked config workload")
        require(destination == expected[kind], "prerequisite rotation checked config destination is invalid")
        config_kinds.append(kind)

        def validate_config(
            stage: Path, persisted: bytes, *, expected: ExperimentConfig = config, expected_content: bytes = content
        ) -> None:
            require(persisted == expected_content, "staged checked config bytes changed before validation")
            require(
                load_experiment(stage) == expected, "staged checked config must reload to its exact absolute oracle"
            )

        targets.append((kind, destination, content, validate_config, False))
    require(
        tuple(config_kinds) == ("config-short", "config-streaming", "config-bursty"),
        "prerequisite rotation must publish its exact checked config order",
    )

    def validate_prerequisite(_stage: Path, persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=root)
        require(
            render_prerequisite_results(parsed) == prerequisite_content, "staged prerequisite JSON is not canonical"
        )

    def validate_marker(_stage: Path, persisted: bytes) -> None:
        require_successful_prerequisite_marker_content(
            persisted, study_id=study_id, url=url, prerequisite_content=prerequisite_content
        )

    targets.extend(
        (
            ("root", prerequisite_path, prerequisite_content, validate_prerequisite, False),
            ("marker", marker, marker_content, validate_marker, True),
        )
    )
    prepared: list[PrerequisiteRotationTarget] = []
    journal: Path | None = None
    cleanup_staging = False
    strict_cleanup_complete = False
    retain_recovery = False
    try:
        for kind, destination, content, validate, must_be_absent in targets:
            previous = (
                read_regular_prerequisite_rotation_target(destination, name="prerequisite rotation target")
                if path_entry_exists(destination)
                else None
            )
            if must_be_absent:
                require(previous is None, f"prerequisite rotation target must be absent: {destination}")
            target = PrerequisiteRotationTarget(
                kind=kind,
                destination=destination,
                stage=None,
                backup=None,
                before_identity=candidate_identity(previous) if previous is not None else None,
                target_identity=candidate_identity(content),
                must_be_absent=must_be_absent,
            )
            prepared.append(target)
            if previous is not None:
                target.backup = _stage_prerequisite_rotation_file(
                    destination,
                    previous,
                    validate=lambda _stage, persisted, expected=previous: require(
                        persisted == expected, "staged prerequisite rollback bytes changed before validation"
                    ),
                    suffix=".bak",
                )
            target.stage = _stage_prerequisite_rotation_file(destination, content, validate=validate)
        journal = _publish_prerequisite_rotation_journal(root, study_id=study_id, targets=prepared)
        cleanup_staging = False
        committed: list[PrerequisiteRotationTarget] = []
        try:
            for target in prepared[:-1]:
                _publish_prerequisite_rotation_target(target)
                committed.append(target)
                _commit_prerequisite_fsync(target.destination)
                _after_prerequisite_rotation_commit(target.destination)
            validate_base_configs(root, result)
            marker_target = prepared[-1]
            _publish_prerequisite_rotation_target(marker_target)
            committed.append(marker_target)
            _commit_prerequisite_fsync(marker_target.destination)
            _after_prerequisite_rotation_commit(marker_target.destination)
        except (OSError, TypeError, ValueError, TrafficlabError) as error:
            rollback_failures, _failed_targets = _rollback_prerequisite_rotation(committed)
            if rollback_failures:
                retain_recovery = True
                raise ValueError(
                    f"prerequisite rotation rollback failed after {error}; retained recovery journal {journal}: {'; '.join(rollback_failures)}"
                ) from error
            cleanup_failures = _cleanup_prerequisite_rotation_staging(prepared, strict=True)
            if cleanup_failures:
                retain_recovery = True
                raise ValueError(
                    f"prerequisite rotation rollback cleanup failed after {error}; retained recovery journal {journal}: {'; '.join(cleanup_failures)}"
                ) from error
            strict_cleanup_complete = True
            _clear_prerequisite_rotation_journal(journal)
            journal = None
            raise
        cleanup_failures = _cleanup_prerequisite_rotation_staging(prepared, strict=True)
        if cleanup_failures:
            retain_recovery = True
            raise ValueError(
                f"prerequisite rotation postcommit cleanup failed; retained recovery journal {journal}: {'; '.join(cleanup_failures)}"
            )
        strict_cleanup_complete = True
        _clear_prerequisite_rotation_journal(journal)
        journal = None
    except (OSError, TypeError, ValueError, TrafficlabError):
        if journal is None and (not retain_recovery) and (not strict_cleanup_complete):
            cleanup_staging = True
        raise
    finally:
        if cleanup_staging:
            _cleanup_prerequisite_rotation_staging(prepared, strict=False)
