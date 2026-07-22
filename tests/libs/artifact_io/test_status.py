import errno
import os
import stat
import tomllib
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, replace
from importlib import import_module
from pathlib import Path

import pytest

from trafficlab.libs.artifact_io import (
    CURRENT_ARTIFACT_STATUS_VERSION,
    MAX_ARTIFACT_STATUS_BYTES,
    ArtifactKind,
    ArtifactStatus,
    ArtifactStatusSecurityError,
    ArtifactValidationError,
    InvalidArtifactStatusError,
    MissingArtifactStatusError,
    OrphanArtifactError,
    PublicationPlan,
    build_file_plan,
    build_package_plan,
    parse_artifact_status,
    render_artifact_status,
    validate_publication,
)
from trafficlab.libs.lineage import (
    MAX_CHUNK_SIZE,
    FileIdentity,
    FileSnapshotError,
    HashMismatchError,
    LineageError,
    Sha256Digest,
    sha256_bytes,
)

filesystem = import_module("trafficlab.libs.artifact_io.filesystem")
status_module = import_module("trafficlab.libs.artifact_io.status")

ZERO = "0123456789abcdef" * 4
ONE = "fedcba9876543210" * 4


@dataclass(frozen=True, slots=True)
class _PublicationFixture:
    plan: PublicationPlan
    status: ArtifactStatus
    launch_bytes: bytes
    artifact_bytes: bytes


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _write_status(plan: PublicationPlan, status: ArtifactStatus) -> None:
    _write_private(plan.status_path, render_artifact_status(status))


def _file_publication(tmp_path: Path) -> _PublicationFixture:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    launch_bytes = b"immutable launch record\n"
    artifact_bytes = b"published artifact bytes\n"
    _write_private(attempt / "launch.toml", launch_bytes)
    artifact = tmp_path / "capture.pcapng"
    _write_private(artifact, artifact_bytes)
    plan = build_file_plan(attempt, artifact)
    status_value = ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.FILE,
        artifact_path=str(artifact),
        digest_path=str(artifact),
        sha256=sha256_bytes(artifact_bytes),
        launch_path=str(plan.launch_path),
        launch_sha256=sha256_bytes(launch_bytes),
    )
    _write_status(plan, status_value)
    return _PublicationFixture(plan, status_value, launch_bytes, artifact_bytes)


def _package_publication(tmp_path: Path) -> _PublicationFixture:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    launch_bytes = b"immutable package launch record\n"
    manifest_bytes = b'{"schema_version":1,"members":["launch.toml"]}\n'
    _write_private(attempt / "launch.toml", launch_bytes)
    artifact = tmp_path / "artifact"
    artifact.mkdir(mode=0o700)
    _write_private(artifact / "manifest.json", manifest_bytes)
    _write_private(artifact / "launch.toml", launch_bytes)
    plan = build_package_plan(attempt, artifact, members=("payload.bin",))
    status_value = ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.PACKAGE,
        artifact_path=str(artifact),
        digest_path=str(artifact / "manifest.json"),
        sha256=sha256_bytes(manifest_bytes),
        launch_path=str(plan.launch_path),
        launch_sha256=sha256_bytes(launch_bytes),
    )
    _write_status(plan, status_value)
    return _PublicationFixture(plan, status_value, launch_bytes, manifest_bytes)


def _empty_attempt(tmp_path: Path) -> Path:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    return attempt


def _assert_safe_diagnostic(error: BaseException) -> None:
    message = str(error)
    assert message
    assert message.splitlines() == [message]


PACKAGE_GOLDEN = (
    b"schema_version = 1\n"
    b'state = "published"\n'
    b'artifact_kind = "package"\n'
    b'artifact_path = "/absolute/attempt/artifact"\n'
    b'digest_path = "/absolute/attempt/artifact/manifest.json"\n'
    b'sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"\n'
    b'launch_path = "/absolute/attempt/launch.toml"\n'
    b'launch_sha256 = "fedcba9876543210fedcba9876543210'
    b'fedcba9876543210fedcba9876543210"\n'
)


def _package_status() -> ArtifactStatus:
    return ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.PACKAGE,
        artifact_path="/absolute/attempt/artifact",
        digest_path="/absolute/attempt/artifact/manifest.json",
        sha256=Sha256Digest(ZERO),
        launch_path="/absolute/attempt/launch.toml",
        launch_sha256=Sha256Digest(ONE),
    )


def _file_status(
    *,
    artifact_path: str = "/absolute/output/capture.pcapng",
    launch_path: str = "/absolute/attempt/launch.toml",
) -> ArtifactStatus:
    return ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.FILE,
        artifact_path=artifact_path,
        digest_path=artifact_path,
        sha256=Sha256Digest(ZERO),
        launch_path=launch_path,
        launch_sha256=Sha256Digest(ONE),
    )


@pytest.mark.unit
def test_package_status_has_exact_golden_bytes_and_terminal_lf() -> None:
    rendered = render_artifact_status(_package_status())

    assert rendered == PACKAGE_GOLDEN
    assert rendered.endswith(b"\n")
    assert b"\r" not in rendered
    assert not rendered.startswith(b"\xef\xbb\xbf")
    assert parse_artifact_status(rendered) == _package_status()


@pytest.mark.unit
def test_file_status_round_trips_with_identical_canonical_bytes() -> None:
    status = _file_status()
    rendered = render_artifact_status(status)

    assert b'artifact_kind = "file"\n' in rendered
    assert rendered.count(b'/absolute/output/capture.pcapng"\n') == 2
    assert render_artifact_status(parse_artifact_status(rendered)) == rendered


@pytest.mark.unit
def test_status_toml_escaping_is_deterministic_and_tomllib_compatible() -> None:
    path = '/absolute/quote"/back\\slash/\u96ea\U0001f600file'
    rendered = render_artifact_status(_file_status(artifact_path=path))
    loaded = tomllib.loads(rendered.decode("utf-8"))

    assert loaded["artifact_path"] == path
    assert loaded["digest_path"] == path
    assert b'quote\\"' in rendered
    assert b"back\\\\slash" in rendered
    assert b"\\u96EA" in rendered
    assert b"\\U0001F600" in rendered
    assert render_artifact_status(parse_artifact_status(rendered)) == rendered


@pytest.mark.unit
def test_status_rejects_unicode_line_separator_with_sanitized_diagnostic() -> None:
    separator = "\u2028"

    with pytest.raises(InvalidArtifactStatusError) as caught:
        _file_status(artifact_path=f"/absolute/before{separator}after")

    message = str(caught.value)
    assert message.splitlines() == [message]
    assert separator not in message


@pytest.mark.unit
def test_status_values_are_frozen_and_slotted() -> None:
    status = _package_status()

    assert not hasattr(status, "__dict__")
    with pytest.raises(FrozenInstanceError):
        status.state = "other"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("schema_version", True, id="boolean-version"),
        pytest.param("schema_version", 2, id="unsupported-version"),
        pytest.param("schema_version", "1", id="string-version"),
        pytest.param("state", "failed", id="unsupported-state"),
        pytest.param("state", 1, id="non-string-state"),
        pytest.param("artifact_kind", "file", id="string-kind"),
        pytest.param("sha256", ZERO, id="string-sha256"),
        pytest.param("launch_sha256", ONE, id="string-launch-sha256"),
    ),
)
def test_direct_status_rejects_wrong_runtime_or_fixed_fields(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "schema_version": 1,
        "state": "published",
        "artifact_kind": ArtifactKind.FILE,
        "artifact_path": "/absolute/artifact",
        "digest_path": "/absolute/artifact",
        "sha256": Sha256Digest(ZERO),
        "launch_path": "/absolute/launch.toml",
        "launch_sha256": Sha256Digest(ONE),
    }
    values[field] = value

    with pytest.raises(InvalidArtifactStatusError):
        ArtifactStatus(**values)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "path"),
    (
        pytest.param("artifact_path", "", id="empty-artifact"),
        pytest.param("artifact_path", "relative", id="relative-artifact"),
        pytest.param("artifact_path", "//ambiguous", id="double-root-artifact"),
        pytest.param("artifact_path", "/path/../escape", id="dotdot-artifact"),
        pytest.param("artifact_path", "/path/./file", id="dot-artifact"),
        pytest.param("artifact_path", "/path//file", id="repeated-artifact"),
        pytest.param("artifact_path", "/path/file/", id="trailing-artifact"),
        pytest.param("artifact_path", "/bad\npath", id="control-artifact"),
        pytest.param("digest_path", "relative", id="relative-digest"),
        pytest.param("launch_path", "/bad\x7fpath", id="control-launch"),
        pytest.param("launch_path", 7, id="non-string-launch"),
    ),
)
def test_direct_status_rejects_noncanonical_paths(field: str, path: object) -> None:
    values: dict[str, object] = {
        "schema_version": 1,
        "state": "published",
        "artifact_kind": ArtifactKind.FILE,
        "artifact_path": "/absolute/artifact",
        "digest_path": "/absolute/artifact",
        "sha256": Sha256Digest(ZERO),
        "launch_path": "/absolute/launch.toml",
        "launch_sha256": Sha256Digest(ONE),
    }
    values[field] = path
    if field == "artifact_path":
        values["digest_path"] = path

    with pytest.raises(InvalidArtifactStatusError):
        ArtifactStatus(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_package_digest_must_be_direct_manifest_child() -> None:
    for digest_path in (
        "/absolute/attempt/manifest.json",
        "/absolute/attempt/artifact/nested/manifest.json",
        "/absolute/attempt/artifact/other.json",
    ):
        with pytest.raises(InvalidArtifactStatusError):
            ArtifactStatus(
                1,
                "published",
                ArtifactKind.PACKAGE,
                "/absolute/attempt/artifact",
                digest_path,
                Sha256Digest(ZERO),
                "/absolute/attempt/launch.toml",
                Sha256Digest(ONE),
            )


@pytest.mark.unit
def test_file_digest_path_must_equal_artifact_path() -> None:
    with pytest.raises(InvalidArtifactStatusError):
        ArtifactStatus(
            1,
            "published",
            ArtifactKind.FILE,
            "/absolute/artifact",
            "/absolute/other",
            Sha256Digest(ZERO),
            "/absolute/launch.toml",
            Sha256Digest(ONE),
        )


@pytest.mark.unit
def test_file_digest_cannot_claim_the_actual_detached_status_path() -> None:
    status_path = "/absolute/attempt/artifact-status.toml"

    with pytest.raises(InvalidArtifactStatusError):
        _file_status(artifact_path=status_path)

    canonical_file = render_artifact_status(_file_status())
    self_claiming = canonical_file.replace(
        b"/absolute/output/capture.pcapng", status_path.encode()
    )
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(self_claiming)


@pytest.mark.unit
def test_launch_digest_cannot_claim_the_actual_detached_status_path() -> None:
    status_path = "/absolute/attempt/artifact-status.toml"

    with pytest.raises(InvalidArtifactStatusError):
        _file_status(launch_path=status_path)

    canonical_file = render_artifact_status(_file_status())
    self_claiming = canonical_file.replace(
        b"/absolute/attempt/launch.toml", status_path.encode()
    )
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(self_claiming)


@pytest.mark.unit
def test_status_basename_outside_the_launch_attempt_is_not_a_self_domain() -> None:
    status = _file_status(artifact_path="/absolute/other/artifact-status.toml")

    assert parse_artifact_status(render_artifact_status(status)) == status


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param("not bytes", id="string"),
        pytest.param(bytearray(PACKAGE_GOLDEN), id="bytearray"),
        pytest.param(memoryview(PACKAGE_GOLDEN), id="memoryview"),
    ),
)
def test_parser_requires_bytes(data: object) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param(b"", id="empty"),
        pytest.param(b" " * (MAX_ARTIFACT_STATUS_BYTES + 1), id="oversized"),
        pytest.param(b"\xef\xbb\xbf" + PACKAGE_GOLDEN, id="bom"),
        pytest.param(PACKAGE_GOLDEN.replace(b"\n", b"\r\n"), id="crlf"),
        pytest.param(PACKAGE_GOLDEN + b"\x00", id="nul"),
        pytest.param(b"schema_version = \xff\n", id="invalid-utf8"),
        pytest.param(b"schema_version = [\n", id="invalid-toml"),
    ),
)
def test_parser_rejects_unsafe_or_invalid_byte_envelopes(data: bytes) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param(PACKAGE_GOLDEN + b'unknown = "value"\n', id="unknown-key"),
        pytest.param(
            b"\n".join(PACKAGE_GOLDEN.splitlines()[:-1]) + b"\n",
            id="missing-key",
        ),
        pytest.param(PACKAGE_GOLDEN + b"schema_version = 1\n", id="duplicate-key"),
        pytest.param(b"[nested]\n" + PACKAGE_GOLDEN, id="nested-table"),
        pytest.param(
            b"\n".join(
                (
                    PACKAGE_GOLDEN.splitlines()[1],
                    PACKAGE_GOLDEN.splitlines()[0],
                    *PACKAGE_GOLDEN.splitlines()[2:],
                )
            )
            + b"\n",
            id="reordered-keys",
        ),
    ),
)
def test_parser_rejects_unknown_missing_duplicate_nested_or_reordered_keys(
    data: bytes,
) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "new"),
    (
        pytest.param(
            b"schema_version = 1", b"schema_version = true", id="bool-version"
        ),
        pytest.param(
            b"schema_version = 1", b"schema_version = 1.0", id="float-version"
        ),
        pytest.param(
            b"schema_version = 1", b'schema_version = "1"', id="string-version"
        ),
        pytest.param(
            b"schema_version = 1", b"schema_version = 2", id="unsupported-version"
        ),
        pytest.param(b'state = "published"', b'state = "failed"', id="state"),
        pytest.param(
            b'artifact_kind = "package"', b'artifact_kind = "directory"', id="kind"
        ),
        pytest.param(
            b'sha256 = "' + ZERO.encode() + b'"', b"sha256 = 7", id="digest-type"
        ),
        pytest.param(
            b'sha256 = "' + ZERO.encode() + b'"',
            b'sha256 = "' + ("A" * 64).encode() + b'"',
            id="digest-spelling",
        ),
    ),
)
def test_parser_rejects_wrong_field_types_or_fixed_values(
    old: bytes, new: bytes
) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(PACKAGE_GOLDEN.replace(old, new))


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param(
            PACKAGE_GOLDEN.replace(b"schema_version = 1", b"schema_version=1"),
            id="whitespace",
        ),
        pytest.param(
            PACKAGE_GOLDEN.replace(b'state = "published"', b"state = 'published'"),
            id="literal-quote",
        ),
        pytest.param(
            PACKAGE_GOLDEN.replace(
                b'state = "published"', b'state = "p\\u0075blished"'
            ),
            id="equivalent-escape",
        ),
        pytest.param(PACKAGE_GOLDEN[:-1], id="missing-terminal-lf"),
        pytest.param(PACKAGE_GOLDEN + b"# comment\n", id="comment"),
    ),
)
def test_parser_rejects_toml_equivalent_noncanonical_bytes(data: bytes) -> None:
    assert tomllib.loads(data.decode("utf-8"))["state"] == "published"
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)


@pytest.mark.unit
def test_parser_rejects_noncanonical_path_and_digest_relationships() -> None:
    relative = PACKAGE_GOLDEN.replace(
        b'artifact_path = "/absolute/attempt/artifact"',
        b'artifact_path = "relative"',
    )
    wrong_manifest = PACKAGE_GOLDEN.replace(
        b'digest_path = "/absolute/attempt/artifact/manifest.json"',
        b'digest_path = "/absolute/attempt/artifact/other.json"',
    )

    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(relative)
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(wrong_manifest)


@pytest.mark.unit
def test_renderer_rejects_non_status_and_oversized_output() -> None:
    with pytest.raises(InvalidArtifactStatusError):
        render_artifact_status(object())  # type: ignore[arg-type]

    huge_path = "/" + "a" * MAX_ARTIFACT_STATUS_BYTES
    with pytest.raises(InvalidArtifactStatusError):
        render_artifact_status(_file_status(artifact_path=huge_path))


@pytest.mark.unit
def test_status_errors_are_sanitized_single_line_and_do_not_repeat_input() -> None:
    secret = b"DO_NOT_REPEAT_THIS_RAW_STATUS"
    malformed = PACKAGE_GOLDEN + b'unknown = "' + secret + b'"\n'

    with pytest.raises(InvalidArtifactStatusError) as caught:
        parse_artifact_status(malformed)

    message = str(caught.value)
    assert message.splitlines() == [message]
    assert secret.decode() not in message


@pytest.mark.unit
def test_status_rejects_unencodable_path_without_repeating_it() -> None:
    unencodable = "\udcff"

    with pytest.raises(InvalidArtifactStatusError) as caught:
        _file_status(artifact_path=f"/absolute/{unencodable}")

    message = str(caught.value)
    assert message.splitlines() == [message]
    assert unencodable not in message


@pytest.mark.integration
def test_art_ac_003_validates_golden_file_publication(tmp_path: Path) -> None:
    """Trace ART-AC-003 through detached file and launch digests."""
    publication = _file_publication(tmp_path)

    assert validate_publication(publication.plan, chunk_size=3) == publication.status


@pytest.mark.integration
def test_art_ac_003_validates_golden_package_and_copied_launch(
    tmp_path: Path,
) -> None:
    """Trace ART-AC-003 through manifest, live launch, and copied launch."""
    publication = _package_publication(tmp_path)

    assert validate_publication(publication.plan, chunk_size=5) == publication.status


@pytest.mark.unit
@pytest.mark.parametrize("chunk_size", (True, 0, -1, MAX_CHUNK_SIZE + 1))
def test_status_snapshot_rejects_invalid_chunk_size_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunk_size: object,
) -> None:
    attempt = _empty_attempt(tmp_path)
    opened = False

    def unexpected_open(
        path: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        del path, flags, dir_fd
        nonlocal opened
        opened = True
        raise AssertionError("invalid chunk size reached the filesystem")

    monkeypatch.setattr(filesystem, "_open_fd", unexpected_open)

    with pytest.raises(ValueError):
        filesystem._snapshot_status(attempt, chunk_size=chunk_size)
    assert opened is False


@pytest.mark.unit
def test_status_snapshot_rejects_relative_attempt_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def unexpected_open(
        path: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        del path, flags, dir_fd
        nonlocal opened
        opened = True
        raise AssertionError("relative attempt reached the filesystem")

    monkeypatch.setattr(filesystem, "_open_fd", unexpected_open)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(Path("relative/attempt"))
    assert opened is False
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_status_snapshot_rejects_attempt_wrong_mode(tmp_path: Path) -> None:
    attempt = _empty_attempt(tmp_path)
    attempt.chmod(0o755)
    _write_private(attempt / "artifact-status.toml", b"status")

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt)

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.unit
def test_status_snapshot_rejects_attempt_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = _empty_attempt(tmp_path)
    _write_private(attempt / "artifact-status.toml", b"status")
    monkeypatch.setattr(filesystem, "_effective_uid", lambda: os.geteuid() + 1)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt)

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_status_snapshot_rejects_non_directory_attempt(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    _write_private(attempt, b"not a directory")

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt)

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_status_snapshot_rejects_symlinked_attempt_component(tmp_path: Path) -> None:
    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    attempt = actual_parent / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    _write_private(attempt / "artifact-status.toml", b"status")
    alias = tmp_path / "alias"
    alias.symlink_to(actual_parent, target_is_directory=True)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(alias / "attempt")

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_missing_status_and_destination_is_missing_status_error(tmp_path: Path) -> None:
    attempt = _empty_attempt(tmp_path)
    plan = build_file_plan(attempt, tmp_path / "absent.bin")

    with pytest.raises(MissingArtifactStatusError) as caught:
        validate_publication(plan)

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_existing_destination_without_status_is_orphan(tmp_path: Path) -> None:
    attempt = _empty_attempt(tmp_path)
    artifact = tmp_path / "orphan.bin"
    _write_private(artifact, b"orphan")
    plan = build_file_plan(attempt, artifact)

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(plan)

    assert caught.value.orphan_path == artifact
    assert caught.value.retained_paths == ()
    assert isinstance(caught.value.__cause__, MissingArtifactStatusError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_broken_destination_symlink_without_status_is_orphan_without_following(
    tmp_path: Path,
) -> None:
    attempt = _empty_attempt(tmp_path)
    artifact = tmp_path / "orphan-link"
    artifact.symlink_to(tmp_path / "missing-target")
    plan = build_file_plan(attempt, artifact)

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(plan)

    assert caught.value.orphan_path == artifact
    assert artifact.is_symlink()
    _assert_safe_diagnostic(caught.value)


@pytest.mark.unit
def test_destination_inspection_failure_is_typed_and_preserves_os_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _empty_attempt(tmp_path)
    artifact = tmp_path / "artifact.bin"
    _write_private(artifact, b"artifact")
    plan = build_file_plan(attempt, artifact)
    injected = OSError(errno.EACCES, "private destination inspection failure")

    def failing_lstat(path: Path) -> os.stat_result:
        assert path == artifact
        raise injected

    monkeypatch.setattr(
        status_module,
        "_lstat_destination",
        failing_lstat,
        raising=False,
    )

    with pytest.raises(ArtifactValidationError) as caught:
        validate_publication(plan)

    assert caught.value.__cause__ is injected
    assert caught.value.orphan_path is None
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "shape",
    ("symlink", "hard-link", "directory", "fifo", "wrong-mode"),
)
def test_status_snapshot_rejects_unsafe_status_envelopes(
    tmp_path: Path,
    shape: str,
) -> None:
    attempt = _empty_attempt(tmp_path)
    status_path = attempt / "artifact-status.toml"
    if shape == "symlink":
        target = tmp_path / "target-status"
        _write_private(target, b"status")
        status_path.symlink_to(target)
    elif shape == "hard-link":
        target = tmp_path / "target-status"
        _write_private(target, b"status")
        status_path.hardlink_to(target)
    elif shape == "directory":
        status_path.mkdir(mode=0o700)
    elif shape == "fifo":
        os.mkfifo(status_path, mode=0o600)
    else:
        _write_private(status_path, b"status")
        status_path.chmod(0o640)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt)

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.unit
def test_status_snapshot_rejects_status_wrong_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _empty_attempt(tmp_path)
    _write_private(attempt / "artifact-status.toml", b"status")
    real_fstat = filesystem._fstat_fd

    def wrong_regular_owner(descriptor: int) -> os.stat_result:
        result = real_fstat(descriptor)
        if stat.S_ISREG(result.st_mode):
            fields = list(result)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(filesystem, "_fstat_fd", wrong_regular_owner)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt)

    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.unit
def test_status_envelope_validation_precedes_content_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _empty_attempt(tmp_path)
    status_path = attempt / "artifact-status.toml"
    _write_private(status_path, b"private untrusted status bytes")
    status_path.chmod(0o644)
    read_called = False

    def unexpected_read(descriptor: int, count: int) -> bytes:
        del descriptor, count
        nonlocal read_called
        read_called = True
        raise AssertionError("unsafe envelope reached content read")

    monkeypatch.setattr(filesystem, "_read_fd", unexpected_read)

    with pytest.raises(ArtifactStatusSecurityError):
        filesystem._snapshot_status(attempt)

    assert read_called is False


@pytest.mark.unit
def test_status_snapshot_uses_exact_descriptor_relative_no_follow_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _empty_attempt(tmp_path)
    payload = b"status bytes"
    _write_private(attempt / "artifact-status.toml", payload)
    real_open = os.open
    observed: list[tuple[str, int, int | None]] = []

    def recording_open(
        path: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        observed.append((path, flags, dir_fd))
        if dir_fd is None:
            return real_open(path, flags)
        return real_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(filesystem, "_open_fd", recording_open)

    assert filesystem._snapshot_status(attempt) == payload

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    status_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    *directory_opens, status_open = observed
    assert directory_opens[0] == ("/", directory_flags, None)
    assert all(flags == directory_flags for _, flags, _ in directory_opens)
    assert all(dir_fd is not None for _, _, dir_fd in directory_opens[1:])
    assert status_open[0] == "artifact-status.toml"
    assert status_open[1] == status_flags
    assert status_open[2] is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("size", "expected_requests", "expected_observed", "should_overflow"),
    (
        (
            MAX_ARTIFACT_STATUS_BYTES,
            [4096, 4096, 4096, 4096, 1],
            [4096, 4096, 4096, 4096, 0],
            False,
        ),
        (
            MAX_ARTIFACT_STATUS_BYTES + 1,
            [4096, 4096, 4096, 4096, 1],
            [4096, 4096, 4096, 4096, 1],
            True,
        ),
    ),
    ids=("exact-limit", "first-overflow-byte"),
)
def test_status_snapshot_reads_only_through_first_overflow_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    expected_requests: list[int],
    expected_observed: list[int],
    should_overflow: bool,
) -> None:
    attempt = _empty_attempt(tmp_path)
    payload = b"x" * size
    _write_private(attempt / "artifact-status.toml", payload)
    requested: list[int] = []
    observed: list[int] = []

    def recording_read(descriptor: int, count: int) -> bytes:
        requested.append(count)
        chunk = os.read(descriptor, count)
        observed.append(len(chunk))
        return chunk

    monkeypatch.setattr(filesystem, "_read_fd", recording_read)

    if should_overflow:
        with pytest.raises(ArtifactStatusSecurityError) as caught:
            filesystem._snapshot_status(attempt, chunk_size=4096)
        assert isinstance(caught.value.__cause__, OSError)
    else:
        assert filesystem._snapshot_status(attempt, chunk_size=4096) == payload
    assert requested == expected_requests
    assert observed == expected_observed
    assert sum(observed) <= MAX_ARTIFACT_STATUS_BYTES + 1


def _race_status_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[Path], None],
) -> ArtifactStatusSecurityError:
    attempt = _empty_attempt(tmp_path)
    status_path = attempt / "artifact-status.toml"
    _write_private(status_path, b"0123456789abcdef")
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        chunk = os.read(descriptor, count)
        if chunk and not changed:
            changed = True
            mutate(status_path)
        return chunk

    monkeypatch.setattr(filesystem, "_read_fd", racing_read)
    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt, chunk_size=4)
    assert changed
    _assert_safe_diagnostic(caught.value)
    return caught.value


@pytest.mark.integration
def test_status_snapshot_detects_in_read_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate_status(path: Path) -> None:
        path.write_bytes(b"fedcba9876543210!")

    error = _race_status_snapshot(tmp_path, monkeypatch, mutate_status)

    assert isinstance(error.__cause__, OSError)


@pytest.mark.integration
def test_status_snapshot_detects_in_read_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def replace_status(path: Path) -> None:
        path.rename(path.with_name("replaced-status"))
        _write_private(path, b"0123456789abcdef")

    error = _race_status_snapshot(tmp_path, monkeypatch, replace_status)

    assert isinstance(error.__cause__, OSError)


@pytest.mark.integration
def test_status_snapshot_detects_in_read_disappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _race_status_snapshot(tmp_path, monkeypatch, Path.unlink)

    assert isinstance(error.__cause__, OSError)


@pytest.mark.integration
def test_status_snapshot_detects_pinned_attempt_chain_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _empty_attempt(tmp_path)
    _write_private(attempt / "artifact-status.toml", b"status bytes")
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        chunk = os.read(descriptor, count)
        if chunk and not changed:
            changed = True
            attempt.chmod(0o711)
        return chunk

    monkeypatch.setattr(filesystem, "_read_fd", racing_read)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt, chunk_size=3)

    assert changed
    assert isinstance(caught.value.__cause__, OSError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.unit
@pytest.mark.parametrize("failed_hook", ("open", "fstat", "stat", "read", "close"))
def test_status_snapshot_wraps_each_syscall_failure_and_closes_every_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_hook: str,
) -> None:
    attempt = _empty_attempt(tmp_path)
    _write_private(attempt / "artifact-status.toml", b"status bytes")
    injected = OSError(errno.EIO, f"private injected {failed_hook} detail\nsecret")
    real_open = filesystem._open_fd
    real_fstat = filesystem._fstat_fd
    real_stat = filesystem._stat_entry
    real_read = filesystem._read_fd
    opened: list[int] = []
    closed: list[int] = []
    hook_calls: dict[str, int] = {
        name: 0 for name in ("open", "fstat", "stat", "read", "close")
    }

    def tracked_open(
        path: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        hook_calls["open"] += 1
        if failed_hook == "open" and hook_calls["open"] == 1:
            raise injected
        descriptor = real_open(path, flags, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    def tracked_fstat(descriptor: int) -> os.stat_result:
        hook_calls["fstat"] += 1
        if failed_hook == "fstat" and hook_calls["fstat"] == 1:
            raise injected
        return real_fstat(descriptor)

    def tracked_stat(
        path: str,
        *,
        dir_fd: int | None = None,
    ) -> os.stat_result:
        hook_calls["stat"] += 1
        if failed_hook == "stat" and hook_calls["stat"] == 1:
            raise injected
        return real_stat(path, dir_fd=dir_fd)

    def tracked_read(descriptor: int, count: int) -> bytes:
        hook_calls["read"] += 1
        if failed_hook == "read" and hook_calls["read"] == 1:
            raise injected
        return real_read(descriptor, count)

    def tracked_close(descriptor: int) -> None:
        hook_calls["close"] += 1
        os.close(descriptor)
        closed.append(descriptor)
        if failed_hook == "close" and hook_calls["close"] == 1:
            raise injected

    monkeypatch.setattr(filesystem, "_open_fd", tracked_open)
    monkeypatch.setattr(filesystem, "_fstat_fd", tracked_fstat)
    monkeypatch.setattr(filesystem, "_stat_entry", tracked_stat)
    monkeypatch.setattr(filesystem, "_read_fd", tracked_read)
    monkeypatch.setattr(filesystem, "_close_fd", tracked_close)

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(attempt, chunk_size=3)

    assert caught.value.__cause__ is injected
    assert closed == list(reversed(opened))
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    _assert_safe_diagnostic(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.unit
def test_status_snapshot_closes_descriptors_in_reverse_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _empty_attempt(tmp_path)
    payload = b"status bytes"
    _write_private(attempt / "artifact-status.toml", payload)
    real_open = filesystem._open_fd
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(
        path: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = real_open(path, flags, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        os.close(descriptor)
        closed.append(descriptor)

    monkeypatch.setattr(filesystem, "_open_fd", tracked_open)
    monkeypatch.setattr(filesystem, "_close_fd", tracked_close)

    assert filesystem._snapshot_status(attempt) == payload
    assert closed == list(reversed(opened))


@pytest.mark.unit
@pytest.mark.parametrize(
    "unsafe_path",
    (
        pytest.param(
            Path(f"/snow-雪-{chr(0x2028)}-missing"),
            id="unicode-line-separator",
        ),
        pytest.param(Path("/unencodable-\udcff"), id="unencodable"),
    ),
)
def test_status_snapshot_diagnostics_do_not_repeat_unsafe_paths(
    unsafe_path: Path,
) -> None:
    unsafe_text = unsafe_path.as_posix()

    with pytest.raises(ArtifactStatusSecurityError) as caught:
        filesystem._snapshot_status(unsafe_path)

    _assert_safe_diagnostic(caught.value)
    assert unsafe_text not in str(caught.value)
    assert "\u2028" not in str(caught.value)
    assert "\udcff" not in str(caught.value)


@pytest.mark.integration
def test_invalid_status_with_absent_destination_remains_status_error(
    tmp_path: Path,
) -> None:
    attempt = _empty_attempt(tmp_path)
    plan = build_file_plan(attempt, tmp_path / "absent.bin")
    _write_private(plan.status_path, b"not canonical status")

    with pytest.raises(InvalidArtifactStatusError) as caught:
        validate_publication(plan)

    assert not isinstance(caught.value, OrphanArtifactError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_invalid_status_with_existing_destination_is_orphan(tmp_path: Path) -> None:
    publication = _file_publication(tmp_path)
    _write_private(publication.plan.status_path, b"not canonical status")

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert caught.value.orphan_path == publication.plan.artifact_path
    assert isinstance(caught.value.__cause__, InvalidArtifactStatusError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_unsafe_status_with_existing_destination_is_orphan(tmp_path: Path) -> None:
    publication = _file_publication(tmp_path)
    publication.plan.status_path.chmod(0o644)

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert caught.value.orphan_path == publication.plan.artifact_path
    assert isinstance(caught.value.__cause__, ArtifactStatusSecurityError)
    _assert_safe_diagnostic(caught.value)


def _binding_mismatch(
    publication: _PublicationFixture,
    mismatch: str,
) -> ArtifactStatus:
    if mismatch == "kind":
        return ArtifactStatus(
            schema_version=1,
            state="published",
            artifact_kind=ArtifactKind.PACKAGE,
            artifact_path=str(publication.plan.artifact_path),
            digest_path=str(publication.plan.artifact_path / "manifest.json"),
            sha256=publication.status.sha256,
            launch_path=str(publication.plan.launch_path),
            launch_sha256=publication.status.launch_sha256,
        )
    if mismatch == "artifact":
        other = publication.plan.artifact_path.with_name("other.bin")
        return replace(
            publication.status,
            artifact_path=str(other),
            digest_path=str(other),
        )
    other_launch = publication.plan.launch_path.with_name("other-launch.toml")
    return replace(publication.status, launch_path=str(other_launch))


@pytest.mark.unit
@pytest.mark.parametrize("mismatch", ("kind", "artifact", "launch"))
def test_status_binds_to_explicit_plan_before_any_lineage_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    publication = _file_publication(tmp_path)
    _write_status(publication.plan, _binding_mismatch(publication, mismatch))
    lineage_calls: list[str] = []

    def unexpected_lineage(*args: object, **kwargs: object) -> object:
        del args, kwargs
        lineage_calls.append("called")
        raise AssertionError("binding mismatch reached Lineage")

    monkeypatch.setattr(
        status_module,
        "validate_external_file",
        unexpected_lineage,
    )
    monkeypatch.setattr(status_module, "validate_local_file", unexpected_lineage)

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert lineage_calls == []
    assert isinstance(caught.value.__cause__, InvalidArtifactStatusError)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.unit
def test_file_validation_uses_public_lineage_in_bound_order_and_forwards_chunk_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _file_publication(tmp_path)
    real_validate_external = status_module.validate_external_file
    calls: list[tuple[str, int]] = []

    def recording_external(expected: FileIdentity, *, chunk_size: int) -> FileIdentity:
        calls.append((expected.path, chunk_size))
        return real_validate_external(expected, chunk_size=chunk_size)

    monkeypatch.setattr(
        status_module,
        "validate_external_file",
        recording_external,
    )

    assert validate_publication(publication.plan, chunk_size=7) == publication.status
    assert calls == [
        (str(publication.plan.launch_path), 7),
        (str(publication.plan.artifact_path), 7),
    ]


@pytest.mark.unit
def test_package_validation_uses_manifest_and_copied_launch_public_lineage_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _package_publication(tmp_path)
    real_validate_external = status_module.validate_external_file
    real_validate_local = status_module.validate_local_file
    calls: list[tuple[str, str, int]] = []

    def recording_external(expected: FileIdentity, *, chunk_size: int) -> FileIdentity:
        calls.append(("external", expected.path, chunk_size))
        return real_validate_external(expected, chunk_size=chunk_size)

    def recording_local(
        root: Path,
        expected: FileIdentity,
        *,
        chunk_size: int,
    ) -> FileIdentity:
        assert root == publication.plan.artifact_path
        calls.append(("local", expected.path, chunk_size))
        return real_validate_local(root, expected, chunk_size=chunk_size)

    monkeypatch.setattr(
        status_module,
        "validate_external_file",
        recording_external,
    )
    monkeypatch.setattr(status_module, "validate_local_file", recording_local)

    assert validate_publication(publication.plan, chunk_size=11) == publication.status
    assert calls == [
        ("external", str(publication.plan.launch_path), 11),
        ("local", "manifest.json", 11),
        ("local", "launch.toml", 11),
    ]


@pytest.mark.unit
def test_validation_reads_and_parses_exactly_one_pinned_status_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _file_publication(tmp_path)
    real_snapshot = filesystem._snapshot_status
    calls: list[tuple[Path, int]] = []

    def recording_snapshot(attempt: Path, *, chunk_size: int) -> bytes:
        calls.append((attempt, chunk_size))
        return real_snapshot(attempt, chunk_size=chunk_size)

    monkeypatch.setattr(filesystem, "_snapshot_status", recording_snapshot)

    assert validate_publication(publication.plan, chunk_size=13) == publication.status
    assert calls == [(publication.plan.attempt_dir, 13)]


@pytest.mark.integration
@pytest.mark.parametrize("target_state", ("missing", "directory", "hash-mismatch"))
def test_live_launch_target_failures_are_typed_and_preserve_lineage_cause(
    tmp_path: Path,
    target_state: str,
) -> None:
    publication = _file_publication(tmp_path)
    launch = publication.plan.launch_path
    if target_state == "missing":
        launch.unlink()
    elif target_state == "directory":
        launch.unlink()
        launch.mkdir()
    else:
        launch.write_bytes(b"mutated launch record\n")

    with pytest.raises(ArtifactValidationError) as caught:
        validate_publication(publication.plan)

    assert isinstance(caught.value.__cause__, LineageError)
    if target_state == "hash-mismatch":
        assert isinstance(caught.value.__cause__, HashMismatchError)
    else:
        assert isinstance(caught.value.__cause__, FileSnapshotError)
    assert caught.value.orphan_path is None
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
@pytest.mark.parametrize("target_state", ("missing", "directory", "hash-mismatch"))
def test_file_artifact_target_failures_preserve_lineage_cause_and_orphan_state(
    tmp_path: Path,
    target_state: str,
) -> None:
    publication = _file_publication(tmp_path)
    artifact = publication.plan.artifact_path
    if target_state == "missing":
        artifact.unlink()
    elif target_state == "directory":
        artifact.unlink()
        artifact.mkdir()
    else:
        artifact.write_bytes(b"mutated artifact bytes\n")

    expected_error = (
        ArtifactValidationError if target_state == "missing" else OrphanArtifactError
    )
    with pytest.raises(expected_error) as caught:
        validate_publication(publication.plan)

    assert isinstance(caught.value.__cause__, LineageError)
    if target_state == "hash-mismatch":
        assert isinstance(caught.value.__cause__, HashMismatchError)
    else:
        assert isinstance(caught.value.__cause__, FileSnapshotError)
    expected_orphan = None if target_state == "missing" else artifact
    assert caught.value.orphan_path == expected_orphan
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_file_destination_symlink_is_rejected_without_following_target(
    tmp_path: Path,
) -> None:
    publication = _file_publication(tmp_path)
    artifact = publication.plan.artifact_path
    target = artifact.with_name("target.bin")
    artifact.rename(target)
    artifact.symlink_to(target)

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert artifact.is_symlink()
    assert target.read_bytes() == publication.artifact_bytes
    assert isinstance(caught.value.__cause__, FileSnapshotError)


PackageTargetMutation = Callable[[_PublicationFixture], None]


def _remove_manifest(publication: _PublicationFixture) -> None:
    (publication.plan.artifact_path / "manifest.json").unlink()


def _directory_manifest(publication: _PublicationFixture) -> None:
    manifest = publication.plan.artifact_path / "manifest.json"
    manifest.unlink()
    manifest.mkdir()


def _mutate_manifest(publication: _PublicationFixture) -> None:
    (publication.plan.artifact_path / "manifest.json").write_bytes(b"mutated")


def _remove_copied_launch(publication: _PublicationFixture) -> None:
    (publication.plan.artifact_path / "launch.toml").unlink()


def _directory_copied_launch(publication: _PublicationFixture) -> None:
    copied_launch = publication.plan.artifact_path / "launch.toml"
    copied_launch.unlink()
    copied_launch.mkdir()


def _mutate_copied_launch(publication: _PublicationFixture) -> None:
    (publication.plan.artifact_path / "launch.toml").write_bytes(b"mutated")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutate", "expected_cause"),
    (
        pytest.param(_remove_manifest, FileSnapshotError, id="missing-manifest"),
        pytest.param(_directory_manifest, FileSnapshotError, id="directory-manifest"),
        pytest.param(_mutate_manifest, HashMismatchError, id="manifest-hash"),
        pytest.param(
            _remove_copied_launch,
            FileSnapshotError,
            id="missing-copied-launch",
        ),
        pytest.param(
            _directory_copied_launch,
            FileSnapshotError,
            id="directory-copied-launch",
        ),
        pytest.param(
            _mutate_copied_launch,
            HashMismatchError,
            id="copied-launch-hash",
        ),
    ),
)
def test_package_target_failures_are_orphans_with_exact_lineage_causes(
    tmp_path: Path,
    mutate: PackageTargetMutation,
    expected_cause: type[LineageError],
) -> None:
    publication = _package_publication(tmp_path)
    mutate(publication)

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert caught.value.orphan_path == publication.plan.artifact_path
    assert isinstance(caught.value.__cause__, expected_cause)
    _assert_safe_diagnostic(caught.value)


@pytest.mark.integration
def test_package_destination_wrong_type_is_orphan(tmp_path: Path) -> None:
    publication = _package_publication(tmp_path)
    artifact = publication.plan.artifact_path
    for member in artifact.iterdir():
        member.unlink()
    artifact.rmdir()
    _write_private(artifact, b"not a package")

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert caught.value.orphan_path == artifact
    assert isinstance(caught.value.__cause__, LineageError)


@pytest.mark.unit
def test_target_validation_diagnostics_do_not_repeat_lineage_cause_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _file_publication(tmp_path)
    private_detail = "private hash failure\nwith secret bytes"
    cause = HashMismatchError(private_detail)
    calls = 0

    def injected_validation(expected: FileIdentity, *, chunk_size: int) -> FileIdentity:
        del chunk_size
        nonlocal calls
        calls += 1
        if calls == 1:
            return expected
        raise cause

    monkeypatch.setattr(
        status_module,
        "validate_external_file",
        injected_validation,
    )

    with pytest.raises(OrphanArtifactError) as caught:
        validate_publication(publication.plan)

    assert caught.value.__cause__ is cause
    _assert_safe_diagnostic(caught.value)
    assert private_detail not in str(caught.value)
