import errno
import io
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from trafficlab.libs.artifact_io import (
    ArtifactKind,
    ArtifactStatus,
    ArtifactStatusSecurityError,
    ArtifactValidationError,
    ArtifactWriteError,
    AtomicPublicationError,
    InvalidArtifactStatusError,
    InvalidPublicationPlanError,
    OrphanArtifactError,
    PublicationConflictError,
    PublicationPlan,
    UnsupportedAtomicPublicationError,
    build_file_plan,
    build_package_plan,
    parse_artifact_status,
    publish_file,
    render_artifact_status,
    validate_publication,
)
from trafficlab.libs.lineage import (
    MAX_CHUNK_SIZE,
    FileIdentity,
    FileSnapshotError,
    PathKind,
    snapshot_external_file,
)

filesystem = import_module("trafficlab.libs.artifact_io.filesystem")
publication = import_module("trafficlab.libs.artifact_io.publication")


@dataclass(frozen=True, slots=True)
class _FileFixture:
    plan: PublicationPlan
    launch: FileIdentity


_EvidenceFailure = (
    ArtifactWriteError
    | ArtifactValidationError
    | AtomicPublicationError
    | PublicationConflictError
    | OrphanArtifactError
)


def _open_directory(path: Path) -> int:
    return os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _file_fixture(tmp_path: Path) -> _FileFixture:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    launch_path = attempt / "launch.toml"
    _write_private(launch_path, b"immutable = true\n")
    plan = build_file_plan(attempt, attempt / "capture.pcapng")
    return _FileFixture(plan, snapshot_external_file(launch_path))


def _retained_staging(error: _EvidenceFailure) -> Path:
    assert len(error.retained_paths) == 1
    staging = error.retained_paths[0]
    assert staging.exists()
    assert staging != error.orphan_path
    return staging


def _assert_pre_artifact_failure(
    error: _EvidenceFailure,
    plan: PublicationPlan,
) -> Path:
    staging = _retained_staging(error)
    assert error.orphan_path is None
    assert not plan.artifact_path.exists()
    assert not plan.status_path.exists()
    assert str(error).splitlines() == [str(error)]
    return staging


def _assert_post_artifact_failure(
    error: _EvidenceFailure,
    plan: PublicationPlan,
    payload: bytes,
    *,
    expect_status_staging: bool,
) -> None:
    assert error.orphan_path == plan.artifact_path
    assert plan.artifact_path.read_bytes() == payload
    assert not plan.status_path.exists()
    if expect_status_staging:
        staging = _retained_staging(error)
        assert staging.parent == plan.attempt_dir
    else:
        assert error.retained_paths == ()
    assert str(error).splitlines() == [str(error)]


def _publish_bytes(
    fixture: _FileFixture,
    payload: bytes = b"single-file artifact\n",
    *,
    validate: Callable[[Path], None] | None = None,
) -> ArtifactStatus:
    def default_validator(path: Path) -> None:
        path.read_bytes()

    validator = validate if validate is not None else default_validator

    def writer(handle: BinaryIO) -> None:
        handle.write(payload)

    return publish_file(fixture.plan, fixture.launch, writer, validator)


@pytest.mark.integration
def test_atomic_noreplace_renames_private_sibling_to_absent_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".artifact.staging-token"
    destination = tmp_path / "artifact.bin"
    source.write_bytes(b"complete artifact")
    parent_fd = _open_directory(tmp_path)

    try:
        filesystem._atomic_rename_noreplace(
            parent_fd,
            source.name,
            parent_fd,
            destination.name,
            source_path=source,
        )
    finally:
        os.close(parent_fd)

    assert not source.exists()
    assert destination.read_bytes() == b"complete artifact"


@pytest.mark.integration
@pytest.mark.parametrize("destination_kind", ["file", "directory"])
def test_atomic_noreplace_preserves_existing_destination_and_source(
    tmp_path: Path,
    destination_kind: str,
) -> None:
    source = tmp_path / ".artifact.staging-token"
    destination = tmp_path / "artifact.bin"
    source.write_bytes(b"new artifact")
    if destination_kind == "file":
        destination.write_bytes(b"existing artifact")
    else:
        destination.mkdir()
        (destination / "evidence").write_bytes(b"existing directory")
    parent_fd = _open_directory(tmp_path)

    try:
        with pytest.raises(PublicationConflictError) as caught:
            filesystem._atomic_rename_noreplace(
                parent_fd,
                source.name,
                parent_fd,
                destination.name,
                source_path=source,
            )
    finally:
        os.close(parent_fd)

    assert caught.value.retained_paths == (source,)
    assert caught.value.orphan_path is None
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.EEXIST
    assert source.read_bytes() == b"new artifact"
    if destination_kind == "file":
        assert destination.read_bytes() == b"existing artifact"
    else:
        assert (destination / "evidence").read_bytes() == b"existing directory"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error_number", "expected_error"),
    [
        pytest.param(errno.EEXIST, PublicationConflictError, id="exists"),
        pytest.param(
            errno.EXDEV,
            UnsupportedAtomicPublicationError,
            id="cross-device",
        ),
        pytest.param(
            errno.ENOSYS,
            UnsupportedAtomicPublicationError,
            id="missing-syscall",
        ),
        pytest.param(
            errno.EINVAL,
            UnsupportedAtomicPublicationError,
            id="unsupported-flags",
        ),
        pytest.param(
            errno.EOPNOTSUPP,
            UnsupportedAtomicPublicationError,
            id="unsupported-filesystem",
        ),
    ],
)
def test_atomic_noreplace_classifies_injected_errors_and_retains_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    expected_error: type[Exception],
) -> None:
    source = tmp_path / ".artifact.staging-token"
    source.write_bytes(b"retained source")

    def failing_renameat2(
        source_parent_fd: int,
        source_name: bytes,
        destination_parent_fd: int,
        destination_name: bytes,
        flags: int,
    ) -> None:
        del (
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            flags,
        )
        raise OSError(error_number, "injected renameat2 failure")

    monkeypatch.setattr(filesystem, "_renameat2_call", failing_renameat2, raising=False)

    with pytest.raises(expected_error) as caught:
        filesystem._atomic_rename_noreplace(
            101,
            source.name,
            202,
            "artifact.bin",
            source_path=source,
        )

    error = cast(_EvidenceFailure, caught.value)
    assert error.retained_paths == (source,)
    assert error.orphan_path is None
    assert isinstance(error.__cause__, OSError)
    assert error.__cause__.errno == error_number
    assert source.read_bytes() == b"retained source"
    assert str(error).splitlines() == [str(error)]


@pytest.mark.integration
def test_art_ac_003_file_publication_is_closed_validated_and_status_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    payload = b"pcapng bytes without an embedded digest\n"
    callback_events: list[str] = []
    handles: list[BinaryIO] = []
    staging_paths: list[Path] = []
    rename_targets: list[str] = []

    original_rename = filesystem._atomic_rename_noreplace

    def recording_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        if destination_name == plan.artifact_path.name:
            assert not plan.artifact_path.exists()
            assert not plan.status_path.exists()
            callback_events.append("artifact-rename")
        else:
            assert destination_name == plan.status_path.name
            assert plan.artifact_path.read_bytes() == payload
            assert not plan.status_path.exists()
            callback_events.append("status-rename")
        rename_targets.append(destination_name)
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )

    monkeypatch.setattr(filesystem, "_atomic_rename_noreplace", recording_rename)

    def writer(handle: BinaryIO) -> None:
        callback_events.append("writer")
        handles.append(handle)
        assert not isinstance(handle, io.BufferedIOBase)
        assert handle.writable()
        assert not handle.closed
        assert not plan.artifact_path.exists()
        assert not plan.status_path.exists()
        assert handle.write(payload) == len(payload)

    def validator(staging_path: Path) -> None:
        callback_events.append("validator")
        staging_paths.append(staging_path)
        assert handles[0].closed
        assert staging_path != plan.artifact_path
        assert staging_path.parent == plan.artifact_path.parent
        metadata = staging_path.stat()
        assert stat.S_ISREG(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1
        assert staging_path.read_bytes() == payload
        assert not plan.artifact_path.exists()
        assert not plan.status_path.exists()

    status = publish_file(plan, fixture.launch, writer, validator, chunk_size=7)

    assert callback_events == [
        "writer",
        "validator",
        "artifact-rename",
        "status-rename",
    ]
    assert rename_targets == [plan.artifact_path.name, plan.status_path.name]
    assert handles[0].closed
    assert not staging_paths[0].exists()
    assert plan.artifact_path.read_bytes() == payload
    assert fixture.launch == snapshot_external_file(plan.launch_path, chunk_size=7)
    final_identity = snapshot_external_file(plan.artifact_path, chunk_size=7)
    assert status == ArtifactStatus(
        schema_version=1,
        state="published",
        artifact_kind=ArtifactKind.FILE,
        artifact_path=str(plan.artifact_path),
        digest_path=str(plan.artifact_path),
        sha256=final_identity.sha256,
        launch_path=str(plan.launch_path),
        launch_sha256=fixture.launch.sha256,
    )
    status_metadata = plan.status_path.stat()
    assert stat.S_ISREG(status_metadata.st_mode)
    assert stat.S_IMODE(status_metadata.st_mode) == 0o600
    assert status_metadata.st_nlink == 1
    status_bytes = plan.status_path.read_bytes()
    assert status_bytes == render_artifact_status(status)
    assert parse_artifact_status(status_bytes) == status
    assert validate_publication(plan, chunk_size=7) == status


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        pytest.param("wrong-plan-value", InvalidPublicationPlanError, id="plan-type"),
        pytest.param("package-plan", InvalidPublicationPlanError, id="package-plan"),
        pytest.param(
            "wrong-launch-value", InvalidPublicationPlanError, id="launch-type"
        ),
        pytest.param("local-launch", InvalidPublicationPlanError, id="launch-kind"),
        pytest.param(
            "wrong-launch-path", InvalidPublicationPlanError, id="launch-path"
        ),
        pytest.param("write-not-callable", InvalidPublicationPlanError, id="writer"),
        pytest.param(
            "validate-not-callable", InvalidPublicationPlanError, id="validator"
        ),
        pytest.param("boolean-chunk", ValueError, id="boolean-chunk"),
        pytest.param("zero-chunk", ValueError, id="zero-chunk"),
        pytest.param("oversize-chunk", ValueError, id="oversize-chunk"),
    ],
)
def test_file_publication_rejects_invalid_inputs_before_filesystem_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: type[Exception],
) -> None:
    fixture = _file_fixture(tmp_path)
    plan: object = fixture.plan
    launch: object = fixture.launch

    def valid_writer(handle: BinaryIO) -> None:
        del handle

    def valid_validator(path: Path) -> None:
        del path

    writer: object = valid_writer
    validator: object = valid_validator
    chunk_size: object = 65_536
    if case == "wrong-plan-value":
        plan = object()
    elif case == "package-plan":
        plan = build_package_plan(
            fixture.plan.attempt_dir,
            fixture.plan.artifact_path,
            members=("payload.bin",),
        )
    elif case == "wrong-launch-value":
        launch = object()
    elif case == "local-launch":
        launch = FileIdentity(PathKind.LOCAL, "launch.toml", fixture.launch.sha256)
    elif case == "wrong-launch-path":
        launch = FileIdentity(
            PathKind.EXTERNAL,
            str(tmp_path / "other-launch.toml"),
            fixture.launch.sha256,
        )
    elif case == "write-not-callable":
        writer = object()
    elif case == "validate-not-callable":
        validator = object()
    elif case == "boolean-chunk":
        chunk_size = True
    elif case == "zero-chunk":
        chunk_size = 0
    else:
        chunk_size = MAX_CHUNK_SIZE + 1

    def forbidden_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise AssertionError("invalid inputs reached a filesystem effect")

    monkeypatch.setattr(filesystem, "_open_fd", forbidden_open)

    with pytest.raises(expected_error):
        publish_file(
            cast(PublicationPlan, plan),
            cast(FileIdentity, launch),
            cast(Callable[[BinaryIO], None], writer),
            cast(Callable[[Path], None], validator),
            chunk_size=cast(int, chunk_size),
        )

    assert not fixture.plan.artifact_path.exists()
    assert not fixture.plan.status_path.exists()
    assert tuple(fixture.plan.artifact_path.parent.glob(".*trafficlab-staging-*")) == ()


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_status_shape", ["line-separator", "oversized"])
def test_file_publication_rejects_unrepresentable_status_shape_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_status_shape: str,
) -> None:
    fixture = _file_fixture(tmp_path)
    separator = "\u2028"
    artifact_path = (
        fixture.plan.attempt_dir / f"capture{separator}.pcapng"
        if unsafe_status_shape == "line-separator"
        else Path("/" + ("nested/" * 2_400) + "artifact.bin")
    )
    plan = build_file_plan(fixture.plan.attempt_dir, artifact_path)
    opened = False
    called = False

    def forbidden_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        nonlocal opened
        opened = True
        raise AssertionError("unrepresentable status shape reached filesystem open")

    def writer(handle: BinaryIO) -> None:
        del handle
        nonlocal called
        called = True

    def validator(path: Path) -> None:
        del path
        nonlocal called
        called = True

    monkeypatch.setattr(filesystem, "_open_fd", forbidden_open)

    with pytest.raises(InvalidPublicationPlanError) as caught:
        publish_file(plan, fixture.launch, writer, validator)

    assert opened is False
    assert called is False
    if unsafe_status_shape == "line-separator":
        assert not plan.artifact_path.exists()
    assert not plan.status_path.exists()
    assert isinstance(caught.value.__cause__, InvalidArtifactStatusError)
    message = str(caught.value)
    assert message.splitlines() == [message]
    assert separator not in message


@pytest.mark.integration
@pytest.mark.parametrize("conflict", ["artifact-file", "artifact-directory", "status"])
def test_file_publication_preserves_preexisting_destination_or_status_without_staging(
    tmp_path: Path,
    conflict: str,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    if conflict == "artifact-file":
        plan.artifact_path.write_bytes(b"existing artifact")
    elif conflict == "artifact-directory":
        plan.artifact_path.mkdir()
        (plan.artifact_path / "evidence").write_bytes(b"existing directory")
    else:
        _write_private(plan.status_path, b"existing status")

    with pytest.raises(PublicationConflictError) as caught:
        _publish_bytes(fixture)

    assert caught.value.retained_paths == ()
    assert caught.value.orphan_path == (
        plan.artifact_path if conflict.startswith("artifact") else None
    )
    if conflict == "artifact-file":
        assert plan.artifact_path.read_bytes() == b"existing artifact"
    elif conflict == "artifact-directory":
        assert (plan.artifact_path / "evidence").read_bytes() == b"existing directory"
    else:
        assert plan.status_path.read_bytes() == b"existing status"
        assert not plan.artifact_path.exists()
    assert tuple(plan.artifact_path.parent.glob(".*trafficlab-staging-*")) == ()


@pytest.mark.unit
def test_file_publication_wraps_token_failure_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    cause = RuntimeError("injected token failure")

    def failing_token() -> str:
        raise cause

    monkeypatch.setattr(filesystem, "_publication_token", failing_token, raising=False)

    with pytest.raises(ArtifactWriteError) as caught:
        _publish_bytes(fixture)

    assert caught.value.__cause__ is cause
    assert caught.value.retained_paths == ()
    assert caught.value.orphan_path is None
    assert not fixture.plan.artifact_path.exists()
    assert not fixture.plan.status_path.exists()


@pytest.mark.unit
def test_file_publication_wraps_staging_create_failure_without_false_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    cause = OSError(errno.ENOSPC, "injected staging create failure")
    original_open = filesystem._open_fd

    def failing_staging_open(
        path: str,
        flags: int,
        *,
        dir_fd: int | None = None,
        mode: int = 0o777,
    ) -> int:
        if flags & os.O_CREAT:
            raise cause
        return original_open(path, flags, dir_fd=dir_fd, mode=mode)

    monkeypatch.setattr(filesystem, "_open_fd", failing_staging_open)

    with pytest.raises(ArtifactWriteError) as caught:
        _publish_bytes(fixture)

    assert caught.value.__cause__ is cause
    assert caught.value.retained_paths == ()
    assert caught.value.orphan_path is None
    assert not fixture.plan.artifact_path.exists()
    assert not fixture.plan.status_path.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("boundary", "expected_error"),
    [
        pytest.param("writer", ArtifactWriteError, id="writer-callback"),
        pytest.param("underlying-write", ArtifactWriteError, id="underlying-write"),
        pytest.param("flush", ArtifactWriteError, id="flush"),
        pytest.param("close", ArtifactWriteError, id="close"),
        pytest.param(
            "component-validator",
            ArtifactValidationError,
            id="component-validator",
        ),
        pytest.param(
            "stage-snapshot",
            ArtifactValidationError,
            id="stage-snapshot-hash",
        ),
        pytest.param(
            "staging-revalidation",
            ArtifactValidationError,
            id="staging-revalidation",
        ),
        pytest.param(
            "parent-revalidation",
            ArtifactValidationError,
            id="parent-revalidation",
        ),
        pytest.param("artifact-rename", AtomicPublicationError, id="artifact-rename"),
    ],
)
def test_file_publication_retains_one_staging_file_at_every_pre_rename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_error: type[Exception],
) -> None:
    fixture = _file_fixture(tmp_path)
    payload = b"retained diagnostic payload"
    cause: BaseException = RuntimeError(f"injected {boundary} failure")
    handles: list[BinaryIO] = []

    def writer(handle: BinaryIO) -> None:
        handles.append(handle)
        if boundary == "writer":
            handle.write(b"partial")
            raise cause
        handle.write(payload)

    def validator(path: Path) -> None:
        assert handles[0].closed
        assert path.read_bytes() == payload
        if boundary == "component-validator":
            raise cause

    if boundary == "underlying-write":
        cause = OSError(errno.EIO, "injected write failure")

        def failing_write(descriptor: int, data: object) -> int:
            del descriptor, data
            raise cause

        monkeypatch.setattr(filesystem, "_write_fd", failing_write, raising=False)
    elif boundary == "flush":
        cause = OSError(errno.EIO, "injected flush failure")

        def failing_flush(handle: BinaryIO) -> None:
            del handle
            raise cause

        monkeypatch.setattr(filesystem, "_flush_writer", failing_flush, raising=False)
    elif boundary == "close":
        cause = OSError(errno.EIO, "injected close failure")
        original_close = filesystem._close_writer

        def failing_close(handle: BinaryIO) -> None:
            original_close(handle)
            raise cause

        monkeypatch.setattr(filesystem, "_close_writer", failing_close)
    elif boundary == "stage-snapshot":
        cause = FileSnapshotError("injected snapshot failure")

        def failing_snapshot(path: Path, *, chunk_size: int) -> FileIdentity:
            del path, chunk_size
            raise cause

        monkeypatch.setattr(
            publication,
            "_snapshot_external_file",
            failing_snapshot,
            raising=False,
        )
    elif boundary == "staging-revalidation":
        cause = OSError(errno.ESTALE, "injected staging binding failure")

        def failing_stage_revalidation(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise cause

        monkeypatch.setattr(
            filesystem,
            "_revalidate_staged_file",
            failing_stage_revalidation,
            raising=False,
        )
    elif boundary == "parent-revalidation":
        cause = OSError(errno.ESTALE, "injected parent binding failure")
        revalidation_calls = 0

        def failing_parent_revalidation(*args: object, **kwargs: object) -> None:
            nonlocal revalidation_calls
            del args, kwargs
            revalidation_calls += 1
            if revalidation_calls == 2:
                raise cause

        monkeypatch.setattr(
            filesystem,
            "_revalidate_pinned_directory",
            failing_parent_revalidation,
            raising=False,
        )
    elif boundary == "artifact-rename":
        cause = OSError(errno.EIO, "injected artifact rename failure")

        def failing_renameat2(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise cause

        monkeypatch.setattr(filesystem, "_renameat2_call", failing_renameat2)

    with pytest.raises(expected_error) as caught:
        publish_file(fixture.plan, fixture.launch, writer, validator)

    error = cast(_EvidenceFailure, caught.value)
    staging = _assert_pre_artifact_failure(error, fixture.plan)
    assert error.__cause__ is cause
    assert handles and handles[0].closed
    if boundary == "writer":
        assert staging.read_bytes() == b"partial"
    elif boundary == "underlying-write":
        assert staging.read_bytes() == b""
    else:
        assert staging.read_bytes() == payload


@pytest.mark.unit
def test_file_publication_revalidates_parent_before_private_staging_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    events: list[str] = []
    original_revalidate = filesystem._revalidate_pinned_directory
    tokens = iter(("artifact-token", "status-token"))

    def recording_revalidation(directory: object) -> None:
        events.append("parent-revalidation")
        original_revalidate(directory)

    def recording_token() -> str:
        events.append("token")
        return next(tokens)

    monkeypatch.setattr(
        filesystem,
        "_revalidate_pinned_directory",
        recording_revalidation,
    )
    monkeypatch.setattr(filesystem, "_publication_token", recording_token)

    _publish_bytes(fixture)

    assert events[0:2] == ["parent-revalidation", "token"]


@pytest.mark.unit
def test_writer_failure_remains_first_cause_when_close_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    writer_cause = RuntimeError("first writer failure")
    close_cause = OSError(errno.EIO, "later close failure")
    handles: list[BinaryIO] = []
    original_close = filesystem._close_writer

    def writer(handle: BinaryIO) -> None:
        handles.append(handle)
        handle.write(b"retained bytes")
        raise writer_cause

    def failing_close(handle: BinaryIO) -> None:
        original_close(handle)
        raise close_cause

    monkeypatch.setattr(filesystem, "_close_writer", failing_close)

    with pytest.raises(ArtifactWriteError) as caught:
        publish_file(fixture.plan, fixture.launch, writer, lambda path: None)

    _assert_pre_artifact_failure(caught.value, fixture.plan)
    assert caught.value.__cause__ is writer_cause
    assert handles[0].closed


@pytest.mark.unit
@pytest.mark.parametrize("invalid_count", [None, 0, -1, 1_000_000])
def test_status_write_rejects_invalid_low_level_counts_with_retained_orphan_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_count: object,
) -> None:
    fixture = _file_fixture(tmp_path)
    payload = b"artifact before invalid status write count"
    original_write = filesystem._write_fd
    write_calls = 0

    def invalid_status_write(descriptor: int, data: object) -> object:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            return invalid_count
        return original_write(descriptor, data)

    monkeypatch.setattr(filesystem, "_write_fd", invalid_status_write)

    with pytest.raises(ArtifactWriteError) as caught:
        _publish_bytes(fixture, payload)

    _assert_post_artifact_failure(
        caught.value,
        fixture.plan,
        payload,
        expect_status_staging=True,
    )
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.integration
def test_file_publication_rejects_staging_link_count_change_before_rename(
    tmp_path: Path,
) -> None:
    fixture = _file_fixture(tmp_path)
    diagnostic_link = tmp_path / "diagnostic-hard-link"

    def hard_linking_validator(staging_path: Path) -> None:
        os.link(staging_path, diagnostic_link)

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_bytes(fixture, validate=hard_linking_validator)

    staging = _assert_pre_artifact_failure(caught.value, fixture.plan)
    assert staging.stat().st_nlink == 2
    assert diagnostic_link.samefile(staging)
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.integration
def test_file_publication_rejects_writer_attempt_to_take_handle_ownership(
    tmp_path: Path,
) -> None:
    fixture = _file_fixture(tmp_path)
    handles: list[BinaryIO] = []

    def closing_writer(handle: BinaryIO) -> None:
        handles.append(handle)
        handle.write(b"producer bytes")
        handle.close()

    with pytest.raises(ArtifactWriteError) as caught:
        publish_file(fixture.plan, fixture.launch, closing_writer, lambda path: None)

    _assert_pre_artifact_failure(caught.value, fixture.plan)
    assert handles[0].closed
    assert isinstance(caught.value.__cause__, (OSError, ValueError))


@pytest.mark.integration
def test_file_publication_atomic_race_preserves_racer_and_retains_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    original_rename = filesystem._atomic_rename_noreplace

    def racing_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        if destination_name == plan.artifact_path.name:
            plan.artifact_path.write_bytes(b"racing destination")
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )

    monkeypatch.setattr(filesystem, "_atomic_rename_noreplace", racing_rename)

    with pytest.raises(PublicationConflictError) as caught:
        _publish_bytes(fixture, b"losing artifact")

    staging = _retained_staging(caught.value)
    assert staging.read_bytes() == b"losing artifact"
    assert plan.artifact_path.read_bytes() == b"racing destination"
    assert not plan.status_path.exists()
    assert caught.value.orphan_path == plan.artifact_path


@pytest.mark.unit
@pytest.mark.parametrize(
    ("boundary", "expected_error", "expect_status_staging"),
    [
        pytest.param(
            "final-artifact-validation",
            ArtifactValidationError,
            False,
            id="final-artifact-validation",
        ),
        pytest.param(
            "launch-revalidation",
            ArtifactValidationError,
            False,
            id="launch-revalidation",
        ),
        pytest.param("status-token", ArtifactWriteError, False, id="status-token"),
        pytest.param(
            "status-render",
            ArtifactValidationError,
            False,
            id="status-render",
        ),
        pytest.param("status-create", ArtifactWriteError, False, id="status-create"),
        pytest.param("status-write", ArtifactWriteError, True, id="status-write"),
        pytest.param("status-flush", ArtifactWriteError, True, id="status-flush"),
        pytest.param("status-close", ArtifactWriteError, True, id="status-close"),
        pytest.param(
            "status-envelope",
            ArtifactValidationError,
            True,
            id="status-envelope",
        ),
        pytest.param(
            "status-parse",
            ArtifactValidationError,
            True,
            id="status-parse-bind",
        ),
        pytest.param("status-rename", AtomicPublicationError, True, id="status-rename"),
        pytest.param(
            "final-validation",
            ArtifactValidationError,
            False,
            id="final-validation",
        ),
    ],
)
def test_file_publication_reports_orphan_at_every_post_artifact_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_error: type[Exception],
    expect_status_staging: bool,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    payload = b"immutable orphan payload"
    cause: BaseException = RuntimeError(f"injected {boundary} failure")

    if boundary in {"final-artifact-validation", "launch-revalidation"}:
        cause = FileSnapshotError(f"injected {boundary} failure")
        original_validate = publication._validate_external_file
        launch_calls = 0

        def failing_validation(
            expected: FileIdentity,
            *,
            chunk_size: int,
        ) -> FileIdentity:
            nonlocal launch_calls
            if expected.path == str(plan.launch_path):
                launch_calls += 1
                if boundary == "launch-revalidation" and launch_calls == 2:
                    raise cause
            elif boundary == "final-artifact-validation":
                raise cause
            return original_validate(expected, chunk_size=chunk_size)

        monkeypatch.setattr(
            publication,
            "_validate_external_file",
            failing_validation,
        )
    elif boundary == "status-token":
        token_calls = 0

        def failing_second_token() -> str:
            nonlocal token_calls
            token_calls += 1
            if token_calls == 2:
                raise cause
            return f"token-{token_calls}"

        monkeypatch.setattr(
            filesystem,
            "_publication_token",
            failing_second_token,
            raising=False,
        )
    elif boundary == "status-render":
        cause = InvalidArtifactStatusError("injected status render failure")

        def failing_render(status: ArtifactStatus) -> bytes:
            del status
            raise cause

        monkeypatch.setattr(
            publication,
            "_render_artifact_status",
            failing_render,
            raising=False,
        )
    elif boundary == "status-create":
        cause = OSError(errno.ENOSPC, "injected status create failure")
        original_open = filesystem._open_fd
        create_calls = 0

        def failing_second_create(
            path: str,
            flags: int,
            *,
            dir_fd: int | None = None,
            mode: int = 0o777,
        ) -> int:
            nonlocal create_calls
            if flags & os.O_CREAT:
                create_calls += 1
                if create_calls == 2:
                    raise cause
            return original_open(path, flags, dir_fd=dir_fd, mode=mode)

        monkeypatch.setattr(filesystem, "_open_fd", failing_second_create)
    elif boundary == "status-write":
        cause = OSError(errno.EIO, "injected status write failure")
        original_write = filesystem._write_fd
        write_calls = 0

        def failing_status_write(descriptor: int, data: object) -> int:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 2:
                raise cause
            return original_write(descriptor, data)

        monkeypatch.setattr(
            filesystem,
            "_write_fd",
            failing_status_write,
            raising=False,
        )
    elif boundary == "status-flush":
        cause = OSError(errno.EIO, "injected status flush failure")
        original_flush = filesystem._flush_writer
        flush_calls = 0

        def failing_status_flush(handle: BinaryIO) -> None:
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 2:
                raise cause
            original_flush(handle)

        monkeypatch.setattr(filesystem, "_flush_writer", failing_status_flush)
    elif boundary == "status-close":
        cause = OSError(errno.EIO, "injected status close failure")
        original_close = filesystem._close_writer
        close_calls = 0

        def failing_status_close(handle: BinaryIO) -> None:
            nonlocal close_calls
            close_calls += 1
            original_close(handle)
            if close_calls == 2:
                raise cause

        monkeypatch.setattr(filesystem, "_close_writer", failing_status_close)
    elif boundary == "status-envelope":
        cause = OSError(errno.ESTALE, "injected status envelope failure")

        def failing_status_snapshot(*args: object, **kwargs: object) -> bytes:
            del args, kwargs
            raise cause

        monkeypatch.setattr(
            filesystem,
            "_snapshot_staged_status",
            failing_status_snapshot,
            raising=False,
        )
    elif boundary == "status-parse":
        cause = ValueError("injected status parse failure")

        def failing_parse(data: bytes) -> ArtifactStatus:
            del data
            raise cause

        monkeypatch.setattr(
            publication,
            "_parse_artifact_status",
            failing_parse,
            raising=False,
        )
    elif boundary == "status-rename":
        cause = OSError(errno.EIO, "injected status rename failure")
        original_rename_call = filesystem._renameat2_call
        rename_calls = 0

        def failing_status_rename(*args: object, **kwargs: object) -> None:
            nonlocal rename_calls
            rename_calls += 1
            if rename_calls == 2:
                raise cause
            original_rename_call(*args, **kwargs)

        monkeypatch.setattr(filesystem, "_renameat2_call", failing_status_rename)
    else:
        cause = RuntimeError("injected final detached validation failure")

        def failing_final_validation(
            publication_plan: PublicationPlan,
            *,
            chunk_size: int,
        ) -> ArtifactStatus:
            del publication_plan, chunk_size
            raise cause

        monkeypatch.setattr(
            publication,
            "_validate_publication",
            failing_final_validation,
            raising=False,
        )

    with pytest.raises(expected_error) as caught:
        _publish_bytes(fixture, payload)

    error = cast(_EvidenceFailure, caught.value)
    if boundary == "final-validation":
        assert error.orphan_path == plan.artifact_path
        assert error.retained_paths == ()
        assert plan.artifact_path.read_bytes() == payload
        assert parse_artifact_status(
            plan.status_path.read_bytes()
        ).artifact_path == str(plan.artifact_path)
    else:
        _assert_post_artifact_failure(
            error,
            plan,
            payload,
            expect_status_staging=expect_status_staging,
        )
    assert error.__cause__ is cause


@pytest.mark.integration
def test_file_publication_status_race_preserves_racer_and_reports_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    payload = b"published before status race"
    original_rename = filesystem._atomic_rename_noreplace

    def racing_status_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        if destination_name == plan.status_path.name:
            _write_private(plan.status_path, b"racing status")
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )

    monkeypatch.setattr(
        filesystem,
        "_atomic_rename_noreplace",
        racing_status_rename,
    )

    with pytest.raises(PublicationConflictError) as caught:
        _publish_bytes(fixture, payload)

    assert caught.value.orphan_path == plan.artifact_path
    assert plan.artifact_path.read_bytes() == payload
    assert plan.status_path.read_bytes() == b"racing status"
    staging = _retained_staging(caught.value)
    assert parse_artifact_status(staging.read_bytes()).artifact_path == str(
        plan.artifact_path
    )
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.EEXIST


@pytest.mark.integration
def test_file_publication_retries_private_name_collision_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _file_fixture(tmp_path)
    collision = fixture.plan.artifact_path.parent / ".trafficlab-staging-fixed"
    _write_private(collision, b"existing private evidence")
    tokens = iter(("fixed", "artifact-unique", "status-unique"))
    monkeypatch.setattr(filesystem, "_publication_token", lambda: next(tokens))

    status = _publish_bytes(fixture, b"collision-safe artifact")

    assert status == validate_publication(fixture.plan)
    assert collision.read_bytes() == b"existing private evidence"


@pytest.mark.integration
def test_file_publication_uses_explicit_external_destination_parent(
    tmp_path: Path,
) -> None:
    fixture = _file_fixture(tmp_path)
    output_parent = tmp_path / "explicit-output"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)
    plan = build_file_plan(
        fixture.plan.attempt_dir,
        output_parent / "capture.pcapng",
    )
    explicit = _FileFixture(plan, fixture.launch)
    validator_paths: list[Path] = []

    status = _publish_bytes(
        explicit,
        b"explicit destination",
        validate=lambda path: validator_paths.append(path),
    )

    assert validator_paths[0].parent == output_parent
    assert status.artifact_path == str(plan.artifact_path)
    assert status.launch_path == str(plan.launch_path)
    assert validate_publication(plan) == status


@pytest.mark.integration
def test_file_publication_staging_name_does_not_expand_valid_destination_leaf(
    tmp_path: Path,
) -> None:
    fixture = _file_fixture(tmp_path)
    output_parent = tmp_path / "max-leaf-output"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)
    destination = output_parent / ("a" * 255)
    plan = build_file_plan(fixture.plan.attempt_dir, destination)

    status = _publish_bytes(
        _FileFixture(plan, fixture.launch),
        b"maximum filesystem leaf",
    )

    assert destination.read_bytes() == b"maximum filesystem leaf"
    assert validate_publication(plan) == status


@pytest.mark.integration
@pytest.mark.parametrize("unsafe_boundary", ["attempt-mode", "destination-symlink"])
def test_file_publication_rejects_unsafe_pinned_directory_boundaries(
    tmp_path: Path,
    unsafe_boundary: str,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    if unsafe_boundary == "attempt-mode":
        plan.attempt_dir.chmod(0o755)
    else:
        real_parent = tmp_path / "real-output"
        real_parent.mkdir()
        alias_parent = tmp_path / "alias-output"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        plan = build_file_plan(plan.attempt_dir, alias_parent / "capture.pcapng")
        fixture = _FileFixture(plan, fixture.launch)

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_bytes(fixture)

    assert caught.value.retained_paths == ()
    assert caught.value.orphan_path is None
    assert not plan.artifact_path.exists()
    assert not plan.status_path.exists()
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.unit
def test_publication_attempt_pin_rejects_noncanonical_path_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def forbidden_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        nonlocal opened
        opened = True
        raise AssertionError("noncanonical attempt reached filesystem open")

    monkeypatch.setattr(filesystem, "_open_fd", forbidden_open)

    with pytest.raises(ArtifactStatusSecurityError):
        filesystem._pin_attempt_directory(Path("relative-attempt"))

    assert opened is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "failed_close",
    [
        pytest.param(1, id="destination-pin-before-status"),
        pytest.param(2, id="attempt-pin-after-status"),
    ],
)
def test_file_publication_surfaces_pin_close_failure_at_exact_commit_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_close: int,
) -> None:
    fixture = _file_fixture(tmp_path)
    plan = fixture.plan
    payload = b"close-boundary artifact"
    cause = OSError(errno.EIO, "injected pinned-directory close failure")
    original_close = filesystem._close_pinned_directory
    close_calls = 0

    def failing_close(directory: object) -> OSError | None:
        nonlocal close_calls
        close_calls += 1
        result = original_close(directory)
        return cause if close_calls == failed_close else result

    monkeypatch.setattr(filesystem, "_close_pinned_directory", failing_close)

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_bytes(fixture, payload)

    assert close_calls == 2
    assert caught.value.__cause__ is cause
    assert caught.value.retained_paths == ()
    assert plan.artifact_path.read_bytes() == payload
    if failed_close == 1:
        assert caught.value.orphan_path == plan.artifact_path
        assert not plan.status_path.exists()
    else:
        assert caught.value.orphan_path is None
        committed = parse_artifact_status(plan.status_path.read_bytes())
        assert committed.artifact_path == str(plan.artifact_path)
        assert validate_publication(plan) == committed
