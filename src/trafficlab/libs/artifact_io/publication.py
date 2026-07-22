"""Imperative transactions for validated atomic artifact publication."""

from __future__ import annotations

import errno
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Never, cast

from trafficlab.libs.lineage import (
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_SIZE,
    FileIdentity,
    PathKind,
    Sha256Digest,
    snapshot_external_file,
    validate_external_file,
)

from . import filesystem
from .errors import (
    ArtifactIoError,
    ArtifactValidationError,
    ArtifactWriteError,
    InvalidArtifactStatusError,
    InvalidPublicationPlanError,
    OrphanArtifactError,
    PublicationConflictError,
)
from .status import (
    parse_artifact_status,
    render_artifact_status,
    validate_publication,
)
from .values import (
    CURRENT_ARTIFACT_STATUS_VERSION,
    MAX_ARTIFACT_STATUS_BYTES,
    ArtifactKind,
    ArtifactStatus,
    PublicationPlan,
)

# Narrow aliases keep every Lineage and status boundary independently injectable.
_snapshot_external_file = snapshot_external_file
_validate_external_file = validate_external_file
_render_artifact_status = render_artifact_status
_parse_artifact_status = parse_artifact_status
_validate_publication = validate_publication
_PREFLIGHT_DIGEST = Sha256Digest("0" * 64)


def _first_cause(error: BaseException) -> BaseException:
    if isinstance(error, ArtifactIoError) and error.__cause__ is not None:
        return error.__cause__
    return error


def _raise_write(
    message: str,
    cause: BaseException,
    *,
    retained_paths: tuple[Path, ...] = (),
    orphan_path: Path | None = None,
) -> Never:
    raise ArtifactWriteError(
        message,
        retained_paths=retained_paths,
        orphan_path=orphan_path,
    ) from _first_cause(cause)


def _raise_validation(
    message: str,
    cause: BaseException,
    *,
    retained_paths: tuple[Path, ...] = (),
    orphan_path: Path | None = None,
) -> Never:
    raise ArtifactValidationError(
        message,
        retained_paths=retained_paths,
        orphan_path=orphan_path,
    ) from _first_cause(cause)


def _validate_inputs(
    plan: PublicationPlan,
    launch: FileIdentity,
    write: Callable[[BinaryIO], None],
    validate: Callable[[Path], None],
    chunk_size: int,
) -> None:
    if not isinstance(cast(object, plan), PublicationPlan):
        raise InvalidPublicationPlanError("file publication requires a PublicationPlan")
    PublicationPlan(
        plan.attempt_dir,
        plan.artifact_path,
        plan.artifact_kind,
        plan.member_paths,
    )
    if plan.artifact_kind is not ArtifactKind.FILE or not plan.artifact_path.name:
        raise InvalidPublicationPlanError("file publication requires a file plan")
    if not isinstance(cast(object, launch), FileIdentity):
        raise InvalidPublicationPlanError(
            "file publication requires an external launch identity"
        )
    if launch.kind is not PathKind.EXTERNAL or launch.path != str(plan.launch_path):
        raise InvalidPublicationPlanError(
            "launch identity must name the exact external startup record"
        )
    if not callable(write):
        raise InvalidPublicationPlanError("file publication write must be callable")
    if not callable(validate):
        raise InvalidPublicationPlanError("file publication validate must be callable")
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be an integer from 1 through {MAX_CHUNK_SIZE}"
        )
    try:
        prospective_status = ArtifactStatus(
            schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
            state="published",
            artifact_kind=ArtifactKind.FILE,
            artifact_path=str(plan.artifact_path),
            digest_path=str(plan.artifact_path),
            sha256=_PREFLIGHT_DIGEST,
            launch_path=str(plan.launch_path),
            launch_sha256=launch.sha256,
        )
        render_artifact_status(prospective_status)
    except InvalidArtifactStatusError as error:
        raise InvalidPublicationPlanError(
            "publication plan cannot produce canonical detached status"
        ) from error


def _run_owned_writer(
    staged: filesystem._StagedFile,  # pyright: ignore[reportPrivateUsage]
    handle: BinaryIO,
    write: Callable[[BinaryIO], None],
    *,
    orphan_path: Path | None,
) -> None:
    first_failure: BaseException | None = None
    try:
        write(handle)
    except BaseException as error:
        first_failure = error
    if first_failure is None:
        try:
            filesystem._flush_writer(handle)  # pyright: ignore[reportPrivateUsage]
        except BaseException as error:
            first_failure = error
    try:
        filesystem._close_writer(handle)  # pyright: ignore[reportPrivateUsage]
    except BaseException as error:
        if first_failure is None:
            first_failure = error
    if first_failure is None and not handle.closed:
        first_failure = OSError(errno.EIO, "publication writer did not close")
    if first_failure is not None:
        _raise_write(
            "cannot write and close private publication staging",
            first_failure,
            retained_paths=(staged.path,),
            orphan_path=orphan_path,
        )


def _write_all(handle: BinaryIO, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = handle.write(data[offset:])
        if type(written) is not int or not 1 <= written <= len(data) - offset:
            raise OSError(errno.EIO, "publication write returned an invalid count")
        offset += written


def _create_staging(
    parent: filesystem._PinnedDirectory,  # pyright: ignore[reportPrivateUsage]
    destination_name: str,
    *,
    orphan_path: Path | None,
) -> tuple[
    filesystem._StagedFile,  # pyright: ignore[reportPrivateUsage]
    BinaryIO,
]:
    try:
        return filesystem._create_staged_file(  # pyright: ignore[reportPrivateUsage]
            parent,
            destination_name,
        )
    except BaseException as error:
        retained = error.retained_paths if isinstance(error, ArtifactWriteError) else ()
        _raise_write(
            "cannot create private publication staging",
            error,
            retained_paths=retained,
            orphan_path=orphan_path,
        )


def _validate_launch_identity(
    launch: FileIdentity,
    *,
    chunk_size: int,
    orphan_path: Path | None,
) -> FileIdentity:
    try:
        actual = _validate_external_file(launch, chunk_size=chunk_size)
        if actual != launch:
            raise OSError(errno.ESTALE, "startup record identity changed")
    except BaseException as error:
        _raise_validation(
            "startup record failed publication validation",
            error,
            orphan_path=orphan_path,
        )
    return actual


def _snapshot_staging(
    staged: filesystem._StagedFile,  # pyright: ignore[reportPrivateUsage]
    *,
    chunk_size: int,
) -> FileIdentity:
    try:
        identity = _snapshot_external_file(staged.path, chunk_size=chunk_size)
        if identity.kind is not PathKind.EXTERNAL or identity.path != str(staged.path):
            raise OSError(errno.ESTALE, "staged artifact identity is invalid")
        filesystem._revalidate_staged_file(  # pyright: ignore[reportPrivateUsage]
            staged
        )
        filesystem._revalidate_pinned_directory(  # pyright: ignore[reportPrivateUsage]
            staged.parent
        )
    except BaseException as error:
        _raise_validation(
            "staged artifact failed publication validation",
            error,
            retained_paths=(staged.path,),
        )
    return identity


def _publish_artifact(
    plan: PublicationPlan,
    parent: filesystem._PinnedDirectory,  # pyright: ignore[reportPrivateUsage]
    staged: filesystem._StagedFile,  # pyright: ignore[reportPrivateUsage]
) -> None:
    try:
        filesystem._atomic_rename_noreplace(  # pyright: ignore[reportPrivateUsage]
            parent.fd,
            staged.name,
            parent.fd,
            plan.artifact_path.name,
            source_path=staged.path,
        )
    except PublicationConflictError as error:
        raise PublicationConflictError(
            str(error),
            retained_paths=error.retained_paths,
            orphan_path=plan.artifact_path,
        ) from _first_cause(error)


def _validate_published_artifact(
    plan: PublicationPlan,
    parent: filesystem._PinnedDirectory,  # pyright: ignore[reportPrivateUsage]
    staged: filesystem._StagedFile,  # pyright: ignore[reportPrivateUsage]
    staged_identity: FileIdentity,
    *,
    chunk_size: int,
) -> FileIdentity:
    expected = FileIdentity(
        PathKind.EXTERNAL,
        str(plan.artifact_path),
        staged_identity.sha256,
    )
    try:
        filesystem._revalidate_published_file(  # pyright: ignore[reportPrivateUsage]
            parent,
            plan.artifact_path.name,
            staged,
        )
        actual = _validate_external_file(expected, chunk_size=chunk_size)
        if actual != expected:
            raise OSError(errno.ESTALE, "published artifact identity changed")
    except BaseException as error:
        _raise_validation(
            "published artifact failed publication validation",
            error,
            orphan_path=plan.artifact_path,
        )
    return actual


def _build_file_status(
    plan: PublicationPlan,
    artifact: FileIdentity,
    launch: FileIdentity,
) -> ArtifactStatus:
    return ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.FILE,
        artifact_path=str(plan.artifact_path),
        digest_path=str(plan.artifact_path),
        sha256=artifact.sha256,
        launch_path=str(plan.launch_path),
        launch_sha256=launch.sha256,
    )


def _stage_status(
    plan: PublicationPlan,
    attempt: filesystem._PinnedDirectory,  # pyright: ignore[reportPrivateUsage]
    status: ArtifactStatus,
    *,
    chunk_size: int,
) -> filesystem._StagedFile:  # pyright: ignore[reportPrivateUsage]
    try:
        data = _render_artifact_status(status)
    except BaseException as error:
        _raise_validation(
            "detached status failed canonical rendering",
            error,
            orphan_path=plan.artifact_path,
        )
    staged, handle = _create_staging(
        attempt,
        plan.status_path.name,
        orphan_path=plan.artifact_path,
    )
    _run_owned_writer(
        staged,
        handle,
        lambda writer: _write_all(writer, data),
        orphan_path=plan.artifact_path,
    )
    try:
        snapshot = filesystem._snapshot_staged_status(  # pyright: ignore[reportPrivateUsage]
            staged,
            max_bytes=MAX_ARTIFACT_STATUS_BYTES,
            chunk_size=chunk_size,
        )
        parsed = _parse_artifact_status(snapshot)
        if parsed != status:
            raise OSError(errno.ESTALE, "staged status does not bind publication")
    except BaseException as error:
        _raise_validation(
            "staged detached status failed publication validation",
            error,
            retained_paths=(staged.path,),
            orphan_path=plan.artifact_path,
        )
    return staged


def _publish_status(
    plan: PublicationPlan,
    attempt: filesystem._PinnedDirectory,  # pyright: ignore[reportPrivateUsage]
    staged: filesystem._StagedFile,  # pyright: ignore[reportPrivateUsage]
) -> None:
    filesystem._atomic_rename_noreplace(  # pyright: ignore[reportPrivateUsage]
        attempt.fd,
        staged.name,
        attempt.fd,
        plan.status_path.name,
        source_path=staged.path,
        orphan_path=plan.artifact_path,
    )


def _final_validation(
    plan: PublicationPlan,
    *,
    chunk_size: int,
) -> ArtifactStatus:
    try:
        return _validate_publication(plan, chunk_size=chunk_size)
    except BaseException as error:
        if isinstance(error, OrphanArtifactError):
            raise
        _raise_validation(
            "published artifact failed final detached validation",
            error,
            orphan_path=plan.artifact_path,
        )


def _close_pins(
    destination: filesystem._PinnedDirectory | None,  # pyright: ignore[reportPrivateUsage]
    attempt: filesystem._PinnedDirectory | None,  # pyright: ignore[reportPrivateUsage]
) -> None:
    if destination is not None:
        filesystem._close_pinned_directory(  # pyright: ignore[reportPrivateUsage]
            destination
        )
    if attempt is not None:
        filesystem._close_pinned_directory(attempt)  # pyright: ignore[reportPrivateUsage]


def publish_file(
    plan: PublicationPlan,
    launch: FileIdentity,
    write: Callable[[BinaryIO], None],
    validate: Callable[[Path], None],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ArtifactStatus:
    """Publish one validated file and then its detached success marker."""

    _validate_inputs(plan, launch, write, validate, chunk_size)
    attempt: filesystem._PinnedDirectory | None = None  # pyright: ignore[reportPrivateUsage]
    destination: filesystem._PinnedDirectory | None = None  # pyright: ignore[reportPrivateUsage]
    try:
        try:
            attempt = filesystem._pin_attempt_directory(  # pyright: ignore[reportPrivateUsage]
                plan.attempt_dir
            )
            destination = filesystem._pin_owned_directory(  # pyright: ignore[reportPrivateUsage]
                plan.artifact_path.parent
            )
            artifact_exists = filesystem._entry_exists(  # pyright: ignore[reportPrivateUsage]
                destination,
                plan.artifact_path.name,
            )
            status_exists = filesystem._entry_exists(  # pyright: ignore[reportPrivateUsage]
                attempt,
                plan.status_path.name,
            )
        except BaseException as error:
            _raise_validation("publication filesystem boundary is unsafe", error)

        if artifact_exists or status_exists:
            raise PublicationConflictError(
                "publication destination or detached status already exists",
                orphan_path=plan.artifact_path if artifact_exists else None,
            )

        _validate_launch_identity(launch, chunk_size=chunk_size, orphan_path=None)
        staged, handle = _create_staging(
            destination,
            plan.artifact_path.name,
            orphan_path=None,
        )
        _run_owned_writer(staged, handle, write, orphan_path=None)
        try:
            validate(staged.path)
        except BaseException as error:
            _raise_validation(
                "component validator rejected staged artifact",
                error,
                retained_paths=(staged.path,),
            )
        staged_identity = _snapshot_staging(staged, chunk_size=chunk_size)
        _publish_artifact(plan, destination, staged)
        artifact_identity = _validate_published_artifact(
            plan,
            destination,
            staged,
            staged_identity,
            chunk_size=chunk_size,
        )
        destination_close_error = filesystem._close_pinned_directory(  # pyright: ignore[reportPrivateUsage]
            destination
        )
        destination = None
        if destination_close_error is not None:
            attempt_close_error = filesystem._close_pinned_directory(  # pyright: ignore[reportPrivateUsage]
                attempt
            )
            attempt = None
            del attempt_close_error
            _raise_validation(
                "cannot close publication descriptors before status commit",
                destination_close_error,
                orphan_path=plan.artifact_path,
            )
        fresh_launch = _validate_launch_identity(
            launch,
            chunk_size=chunk_size,
            orphan_path=plan.artifact_path,
        )
        status = _build_file_status(plan, artifact_identity, fresh_launch)
        staged_status = _stage_status(
            plan,
            attempt,
            status,
            chunk_size=chunk_size,
        )
        _publish_status(plan, attempt, staged_status)
        attempt_close_error = filesystem._close_pinned_directory(  # pyright: ignore[reportPrivateUsage]
            attempt
        )
        attempt = None
        if attempt_close_error is not None:
            _raise_validation(
                "cannot close publication descriptors after status commit",
                attempt_close_error,
            )
        return _final_validation(plan, chunk_size=chunk_size)
    finally:
        _close_pins(destination, attempt)
