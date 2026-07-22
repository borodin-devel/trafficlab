import errno
import os
from importlib import import_module
from pathlib import Path

import pytest

from trafficlab.libs.lineage import (
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_SIZE,
    FileChangedError,
    FileIdentity,
    FileSnapshotError,
    HashMismatchError,
    InvalidLineagePathError,
    ManifestValidationError,
    PathKind,
    Sha256Digest,
    sha256_bytes,
    sha256_file,
    snapshot_external_file,
    snapshot_local_file,
    validate_external_file,
    validate_local_file,
)

hashing = import_module("trafficlab.libs.lineage.hashing")

LINE_SEPARATOR = chr(0x2028)
UNPAIRED_SURROGATE = chr(0xD800)


def _assert_control_safe_path_message(
    error: BaseException,
    *,
    unsafe_character: str,
    escaped_character: str,
) -> None:
    message = str(error)
    assert message.splitlines() == [message]
    assert unsafe_character not in message
    assert escaped_character in message
    assert "雪" in message


@pytest.mark.unit
def test_chunk_size_limits_are_public_and_fixed() -> None:
    assert DEFAULT_CHUNK_SIZE == 65_536
    assert MAX_CHUNK_SIZE == 1_048_576


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    ],
)
def test_sha256_bytes_matches_published_vectors(payload: bytes, expected: str) -> None:
    assert str(sha256_bytes(payload)) == expected


@pytest.mark.unit
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 64, 65_536, 1_048_576])
def test_sha256_file_is_chunk_boundary_invariant(
    tmp_path: Path, chunk_size: int
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc" * 100_003)
    assert sha256_file(source, chunk_size=chunk_size) == sha256_bytes(
        source.read_bytes()
    )


@pytest.mark.unit
def test_sha256_file_requests_only_the_selected_bounded_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"bounded-read" * 10)
    requested_sizes: list[int] = []

    def recording_read(fd: int, size: int) -> bytes:
        requested_sizes.append(size)
        return os.read(fd, size)

    monkeypatch.setattr(hashing, "_read_chunk", recording_read)

    assert sha256_file(source, chunk_size=17) == sha256_bytes(source.read_bytes())
    assert requested_sizes
    assert set(requested_sizes) == {17}


@pytest.mark.unit
@pytest.mark.parametrize("chunk_size", [0, -1, 1_048_577])
def test_hashing_rejects_out_of_range_chunk_sizes(
    tmp_path: Path, chunk_size: int
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")

    with pytest.raises(ValueError):
        sha256_file(source, chunk_size=chunk_size)
    with pytest.raises(ValueError):
        snapshot_local_file(tmp_path, source.name, chunk_size=chunk_size)
    with pytest.raises(ValueError):
        snapshot_external_file(source, chunk_size=chunk_size)


@pytest.mark.unit
def test_local_snapshot_and_validation_detect_one_byte_mutation(
    tmp_path: Path,
) -> None:
    member = tmp_path / "nested" / "member.bin"
    member.parent.mkdir()
    member.write_bytes(b"before")
    identity = snapshot_local_file(tmp_path, "nested/member.bin")
    assert validate_local_file(tmp_path, identity) == identity
    member.write_bytes(b"beFore")
    with pytest.raises(HashMismatchError):
        validate_local_file(tmp_path, identity)


@pytest.mark.unit
def test_external_snapshot_records_normalized_absolute_path(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    identity = snapshot_external_file(source)
    assert identity == FileIdentity(
        PathKind.EXTERNAL, str(source), sha256_bytes(b"source")
    )


@pytest.mark.unit
def test_snapshot_rejects_symlink_ancestor_and_leaf(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "member").write_bytes(b"x")
    (tmp_path / "alias").symlink_to(actual, target_is_directory=True)
    (tmp_path / "leaf").symlink_to(actual / "member")
    with pytest.raises(FileSnapshotError):
        snapshot_local_file(tmp_path, "alias/member")
    with pytest.raises(FileSnapshotError):
        snapshot_local_file(tmp_path, "leaf")


@pytest.mark.unit
def test_external_snapshot_rejects_symlink_ancestor_and_leaf(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "member").write_bytes(b"x")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    leaf = tmp_path / "leaf"
    leaf.symlink_to(actual / "member")

    with pytest.raises(FileSnapshotError):
        snapshot_external_file(alias / "member")
    with pytest.raises(FileSnapshotError):
        snapshot_external_file(leaf)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        pytest.param(Path("relative.bin"), id="relative"),
        # These are lexical validation fixtures; neither path is opened.
        pytest.param(Path("/tmp/../source.bin"), id="parent-component"),  # noqa: S108
        pytest.param(Path("//source.bin"), id="double-root"),
    ],
)
def test_external_hashing_rejects_relative_or_non_normalized_paths(
    path: Path,
) -> None:
    with pytest.raises(InvalidLineagePathError):
        sha256_file(path)
    with pytest.raises(InvalidLineagePathError):
        snapshot_external_file(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "relative_path",
    [
        pytest.param("../escape", id="traversal"),
        pytest.param("nested/../member", id="parent-component"),
        pytest.param("./member", id="dot-component"),
        pytest.param("nested//member", id="repeated-separator"),
        pytest.param("/absolute", id="absolute"),
        pytest.param("member/", id="trailing-separator"),
    ],
)
def test_local_snapshot_rejects_traversal_and_non_normalized_paths(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(InvalidLineagePathError):
        snapshot_local_file(tmp_path, relative_path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "root",
    [
        pytest.param(Path("relative-root"), id="relative"),
        pytest.param(Path("/tmp/root/../root"), id="parent-component"),  # noqa: S108
        pytest.param(Path("//root"), id="double-root"),
    ],
)
def test_local_snapshot_requires_normalized_absolute_root(root: Path) -> None:
    with pytest.raises(InvalidLineagePathError):
        snapshot_local_file(root, "member")


@pytest.mark.unit
def test_snapshot_wraps_missing_file_errors(tmp_path: Path) -> None:
    with pytest.raises(FileSnapshotError) as local_error:
        snapshot_local_file(tmp_path, "missing")
    with pytest.raises(FileSnapshotError) as external_error:
        snapshot_external_file(tmp_path / "missing")

    assert isinstance(local_error.value.__cause__, OSError)
    assert isinstance(external_error.value.__cause__, OSError)


@pytest.mark.unit
@pytest.mark.parametrize("path_kind", ["local", "external"])
def test_snapshot_wraps_unencodable_path_as_typed_control_safe_error(
    tmp_path: Path, path_kind: str
) -> None:
    component = f"printable-雪-{UNPAIRED_SURROGATE}-member"

    with pytest.raises(FileSnapshotError) as caught:
        if path_kind == "local":
            snapshot_local_file(tmp_path, component)
        else:
            snapshot_external_file(tmp_path / component)

    _assert_control_safe_path_message(
        caught.value,
        unsafe_character=UNPAIRED_SURROGATE,
        escaped_character=r"\ud800",
    )
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.unit
def test_snapshot_error_renders_line_separator_on_one_line(tmp_path: Path) -> None:
    component = f"printable-雪-{LINE_SEPARATOR}-missing"

    with pytest.raises(FileSnapshotError) as caught:
        snapshot_external_file(tmp_path / component)

    _assert_control_safe_path_message(
        caught.value,
        unsafe_character=LINE_SEPARATOR,
        escaped_character=r"\u2028",
    )


@pytest.mark.unit
def test_snapshot_rejects_directory_leaf(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(FileSnapshotError) as caught:
        snapshot_external_file(directory)

    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.unit
def test_snapshot_opens_fifo_leaf_nonblocking_and_no_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    real_open = os.open
    checked_leaf = False

    def checked_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal checked_leaf
        if path == fifo.name:
            checked_leaf = True
            assert flags == (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", checked_open)

    with pytest.raises(FileSnapshotError) as caught:
        snapshot_external_file(fifo)

    assert checked_leaf
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.unit
def test_snapshot_uses_exact_directory_and_leaf_open_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "nested" / "source.bin"
    source.parent.mkdir()
    source.write_bytes(b"source")
    real_open = os.open
    opened: list[tuple[str | bytes | os.PathLike[str], int, int | None]] = []

    def recording_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        opened.append((path, flags, dir_fd))
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", recording_open)

    assert snapshot_external_file(source).sha256 == sha256_bytes(b"source")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    leaf_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    *directory_opens, leaf_open = opened
    assert directory_opens
    assert directory_opens[0] == ("/", directory_flags, None)
    assert all(flags == directory_flags for _, flags, _ in directory_opens)
    assert all(dir_fd is not None for _, _, dir_fd in directory_opens[1:])
    assert leaf_open[0] == source.name
    assert leaf_open[1] == leaf_flags
    assert leaf_open[2] is not None


@pytest.mark.unit
def test_local_snapshot_rejects_symlink_root(tmp_path: Path) -> None:
    actual_root = tmp_path / "actual-root"
    actual_root.mkdir()
    (actual_root / "member").write_bytes(b"member")
    alias_root = tmp_path / "alias-root"
    alias_root.symlink_to(actual_root, target_is_directory=True)

    with pytest.raises(FileSnapshotError):
        snapshot_local_file(alias_root, "member")


@pytest.mark.unit
def test_validation_rejects_wrong_path_kind(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    digest = sha256_bytes(b"source")
    local = FileIdentity(PathKind.LOCAL, source.name, digest)
    external = FileIdentity(PathKind.EXTERNAL, str(source), digest)

    with pytest.raises(InvalidLineagePathError):
        validate_local_file(tmp_path, external)
    with pytest.raises(InvalidLineagePathError):
        validate_external_file(local)


@pytest.mark.unit
def test_external_validation_detects_file_changed_after_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"before")
    identity = snapshot_external_file(source)
    assert validate_external_file(identity) == identity

    source.write_bytes(b"after")

    with pytest.raises(HashMismatchError):
        validate_external_file(identity)


@pytest.mark.unit
def test_hash_mismatch_renders_line_separator_on_one_line(tmp_path: Path) -> None:
    component = f"printable-雪-{LINE_SEPARATOR}-member"
    (tmp_path / component).write_bytes(b"actual")
    expected = FileIdentity(
        PathKind.LOCAL,
        component,
        Sha256Digest("0" * 64),
    )

    with pytest.raises(HashMismatchError) as caught:
        validate_local_file(tmp_path, expected)

    _assert_control_safe_path_message(
        caught.value,
        unsafe_character=LINE_SEPARATOR,
        escaped_character=r"\u2028",
    )


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["rewrite", "replace", "unlink", "link"])
def test_snapshot_detects_deterministic_in_read_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    source = tmp_path / f"source-雪-{LINE_SEPARATOR}.bin"
    original = b"a" * 256
    source.write_bytes(original)
    initial = source.stat()
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"b" * 256)
    chunks: list[bytes] = []

    def mutating_read(fd: int, size: int) -> bytes:
        chunk = os.read(fd, size)
        chunks.append(chunk)
        if len(chunks) == 1:
            if mutation == "rewrite":
                source.write_bytes(b"c" * len(original))
                os.utime(
                    source,
                    ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000),
                )
            elif mutation == "replace":
                os.replace(replacement, source)
            elif mutation == "unlink":
                source.unlink()
            else:
                os.link(source, tmp_path / "additional-link.bin")
        return chunk

    monkeypatch.setattr(hashing, "_read_chunk", mutating_read)

    with pytest.raises(FileChangedError) as caught:
        snapshot_external_file(source, chunk_size=16)

    assert chunks
    if mutation == "rewrite":
        assert chunks[0] == b"a" * 16
        assert b"c" in b"".join(chunks[1:])
    _assert_control_safe_path_message(
        caught.value,
        unsafe_character=LINE_SEPARATOR,
        escaped_character=r"\u2028",
    )


@pytest.mark.unit
def test_snapshot_detects_post_open_ancestor_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ancestor = tmp_path / "ancestor"
    ancestor.mkdir()
    source = ancestor / "source.bin"
    source.write_bytes(b"stable" * 64)
    moved = tmp_path / "moved"
    calls = 0

    def renaming_read(fd: int, size: int) -> bytes:
        nonlocal calls
        chunk = os.read(fd, size)
        calls += 1
        if calls == 1:
            ancestor.rename(moved)
        return chunk

    monkeypatch.setattr(hashing, "_read_chunk", renaming_read)

    with pytest.raises(FileChangedError):
        snapshot_external_file(source, chunk_size=16)

    assert calls >= 1


@pytest.mark.unit
def test_snapshot_ignores_unrelated_directory_content_metadata_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    payload = b"stable" * 64
    source.write_bytes(payload)
    calls = 0

    def sibling_creating_read(fd: int, size: int) -> bytes:
        nonlocal calls
        chunk = os.read(fd, size)
        calls += 1
        if calls == 1:
            (tmp_path / "unrelated.bin").write_bytes(b"unrelated")
        return chunk

    monkeypatch.setattr(hashing, "_read_chunk", sibling_creating_read)

    assert snapshot_external_file(source, chunk_size=16) == FileIdentity(
        PathKind.EXTERNAL, str(source), sha256_bytes(payload)
    )


@pytest.mark.unit
def test_snapshot_closes_every_pinned_descriptor_after_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "nested" / "source.bin"
    source.parent.mkdir()
    source.write_bytes(b"source" * 32)
    real_open = os.open
    opened_descriptors: list[int] = []

    def recording_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is None:
            descriptor = real_open(path, flags, mode)
        else:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        opened_descriptors.append(descriptor)
        return descriptor

    calls = 0

    def unlinking_read(fd: int, size: int) -> bytes:
        nonlocal calls
        chunk = os.read(fd, size)
        calls += 1
        if calls == 1:
            source.unlink()
        return chunk

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(hashing, "_read_chunk", unlinking_read)

    with pytest.raises(FileChangedError):
        snapshot_external_file(source, chunk_size=8)

    assert opened_descriptors
    for descriptor in opened_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.unit
def test_snapshot_closes_in_reverse_and_continues_after_close_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "nested" / "source.bin"
    source.parent.mkdir()
    source.write_bytes(b"source")
    real_open = os.open
    real_close = os.close
    opened_descriptors: list[int] = []
    close_order: list[int] = []

    def recording_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is None:
            descriptor = real_open(path, flags, mode)
        else:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        opened_descriptors.append(descriptor)
        return descriptor

    def failing_close(descriptor: int) -> None:
        close_order.append(descriptor)
        real_close(descriptor)
        if descriptor == opened_descriptors[-1]:
            raise OSError(errno.EIO, "injected close failure")

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "close", failing_close)

    with pytest.raises(FileSnapshotError) as caught:
        snapshot_external_file(source)

    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.EIO
    assert close_order == list(reversed(opened_descriptors))
    for descriptor in opened_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.unit
def test_read_verified_local_bytes_returns_exact_bounded_snapshot(
    tmp_path: Path,
) -> None:
    payload = b"manifest bytes"
    source = tmp_path / "manifest.json"
    source.write_bytes(payload)
    expected = snapshot_local_file(tmp_path, source.name)

    assert (
        hashing._read_verified_local_bytes(tmp_path, expected, len(payload), 3)
        == payload
    )


@pytest.mark.unit
@pytest.mark.parametrize("max_bytes", [0, -1])
def test_read_verified_local_bytes_requires_positive_bound(
    tmp_path: Path, max_bytes: int
) -> None:
    expected = FileIdentity(PathKind.LOCAL, "manifest.json", Sha256Digest("0" * 64))

    with pytest.raises(ManifestValidationError):
        hashing._read_verified_local_bytes(tmp_path, expected, max_bytes, 16)


@pytest.mark.unit
def test_read_verified_local_bytes_rejects_oversized_content_without_bytes_in_error(
    tmp_path: Path,
) -> None:
    payload = b"secret-manifest-bytes"
    source = tmp_path / "manifest.json"
    source.write_bytes(payload)
    expected = snapshot_local_file(tmp_path, source.name)

    with pytest.raises(ManifestValidationError) as caught:
        hashing._read_verified_local_bytes(tmp_path, expected, len(payload) - 1, 4)

    assert payload.decode() not in str(caught.value)


@pytest.mark.unit
def test_manifest_bound_error_renders_line_separator_on_one_line(
    tmp_path: Path,
) -> None:
    component = f"printable-雪-{LINE_SEPARATOR}-manifest"
    source = tmp_path / component
    source.write_bytes(b"manifest")
    expected = snapshot_local_file(tmp_path, component)

    with pytest.raises(ManifestValidationError) as caught:
        hashing._read_verified_local_bytes(tmp_path, expected, 7, 4)

    _assert_control_safe_path_message(
        caught.value,
        unsafe_character=LINE_SEPARATOR,
        escaped_character=r"\u2028",
    )


@pytest.mark.unit
def test_read_verified_local_bytes_compares_digest_before_returning(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manifest.json"
    source.write_bytes(b"actual")
    expected = FileIdentity(PathKind.LOCAL, source.name, Sha256Digest("0" * 64))

    with pytest.raises(HashMismatchError):
        hashing._read_verified_local_bytes(tmp_path, expected, 64, 4)
