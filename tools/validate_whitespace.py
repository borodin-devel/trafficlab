"""Validate the complete Git index and tracked working tree."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_EOL_WHITESPACE = frozenset(b" \t\r\v\f")
_CONFLICT_MARKERS = tuple(character * 7 for character in (b"<", b"=", b">", b"|"))
_WHITESPACE_MESSAGE_ORDER = {
    "trailing whitespace.": 0,
    "space before tab in indent.": 1,
    "leftover conflict marker": 2,
    "new blank line at EOF.": 3,
}


def empty_tree_argv() -> tuple[str, ...]:
    """Return the object-format-aware empty-tree command vector."""
    return ("git", "hash-object", "-t", "tree", "--stdin")


def diff_check_argv(base_tree: str, *, cached: bool) -> tuple[str, ...]:
    """Return a complete-index or complete-worktree command vector."""
    if cached:
        return ("git", "diff", "--cached", "--check", base_tree)
    return ("git", "diff", "--check", base_tree)


def _index_argv() -> tuple[str, ...]:
    """Return the raw complete-index listing command vector."""
    return ("git", "ls-files", "--stage", "-z")


def _blob_argv(object_id: str) -> tuple[str, ...]:
    """Return a raw object-reading command vector for one validated object id."""
    return ("git", "cat-file", "blob", object_id)


def _regular_index_entries(output: bytes) -> tuple[tuple[bytes, str], ...]:
    """Parse deterministic stage-zero regular-file paths and blob identities."""
    entries: list[tuple[bytes, str]] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            header, path = record.split(b"\t", maxsplit=1)
            mode, raw_object_id, stage = header.split(b" ")
            object_id = raw_object_id.decode("ascii")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("Git returned a malformed index entry") from error
        if not object_id or any(
            character not in "0123456789abcdefABCDEF" for character in object_id
        ):
            raise ValueError("Git returned an invalid index object id")
        if stage == b"0" and mode.startswith(b"100"):
            entries.append((path, object_id))
    return tuple(sorted(entries))


def _physical_lines(content: bytes) -> tuple[bytes, ...]:
    """Return physical lines without LF terminators or a terminal sentinel."""
    if not content:
        return ()
    lines = content.split(b"\n")
    if content.endswith(b"\n"):
        lines.pop()
    return tuple(lines)


def _has_space_before_tab_in_indent(line: bytes) -> bool:
    """Return whether initial space/tab indentation contains `space + tab`."""
    indentation_end = 0
    while indentation_end < len(line) and line[indentation_end] in {0x20, 0x09}:
        indentation_end += 1
    return b" \t" in line[:indentation_end]


def _is_conflict_marker(line: bytes) -> bool:
    """Return whether a line begins with a default-size Git conflict marker."""
    return any(
        line.startswith(marker)
        and (len(line) == len(marker) or line[len(marker)] in _EOL_WHITESPACE)
        for marker in _CONFLICT_MARKERS
    )


def _is_blank_line(line: bytes) -> bool:
    return all(character in _EOL_WHITESPACE for character in line)


def whitespace_issues(content: bytes) -> tuple[tuple[int, str], ...]:
    """Return Git-default whitespace issues for one complete byte snapshot."""
    lines = _physical_lines(content)
    issues: set[tuple[int, str]] = set()
    for line_number, line in enumerate(lines, start=1):
        if line and line[-1] in _EOL_WHITESPACE:
            issues.add((line_number, "trailing whitespace."))
        if _has_space_before_tab_in_indent(line):
            issues.add((line_number, "space before tab in indent."))
        if _is_conflict_marker(line):
            issues.add((line_number, "leftover conflict marker"))

    blank_start = len(lines)
    while blank_start > 0 and _is_blank_line(lines[blank_start - 1]):
        blank_start -= 1
    if blank_start < len(lines):
        issues.add((blank_start + 1, "new blank line at EOF."))

    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue[0],
                _WHITESPACE_MESSAGE_ORDER[issue[1]],
            ),
        )
    )


def _diagnostic_path(path: bytes) -> str:
    """Render an index path on one stable, printable diagnostic line."""
    rendered: list[str] = []
    for character in os.fsdecode(path):
        if character.isprintable():
            rendered.append(character)
        elif character == "\t":
            rendered.append(r"\t")
        elif character == "\n":
            rendered.append(r"\n")
        elif character == "\r":
            rendered.append(r"\r")
        else:
            codepoint = ord(character)
            width = 2 if codepoint <= 0xFF else 4 if codepoint <= 0xFFFF else 8
            prefix = "x" if width == 2 else "u" if width == 4 else "U"
            rendered.append(f"\\{prefix}{codepoint:0{width}x}")
    return "".join(rendered)


def _open_repository(cwd: Path | None) -> int:
    """Pin a repository directory without following supplied path components."""
    if cwd is None:
        return os.open(".", _DIRECTORY_OPEN_FLAGS)

    absolute = Path(os.path.abspath(os.fspath(cwd)))
    descriptor = os.open("/", _DIRECTORY_OPEN_FLAGS)
    try:
        for component in absolute.parts[1:]:
            expected_status = os.stat(
                component,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(expected_status.st_mode):
                raise ValueError(
                    "whitespace repository must not contain symlinked path "
                    f"components: {cwd}"
                )
            if not stat.S_ISDIR(expected_status.st_mode):
                raise ValueError(f"whitespace repository is not a directory: {cwd}")
            child_descriptor = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=descriptor,
            )
            try:
                actual_status = os.fstat(child_descriptor)
                if not stat.S_ISDIR(actual_status.st_mode) or not os.path.samestat(
                    expected_status, actual_status
                ):
                    raise OSError(
                        errno.ESTALE,
                        f"whitespace repository changed while being opened: {cwd}",
                    )
            except BaseException:
                os.close(child_descriptor)
                raise
            os.close(descriptor)
            descriptor = child_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _tracked_path_components(path: bytes) -> tuple[bytes, ...]:
    """Validate and split one repository-relative index path."""
    components = tuple(path.split(b"/"))
    if not components or any(
        component in {b"", b".", b".."} for component in components
    ):
        raise ValueError("Git returned an unsafe tracked path")
    return components


def _read_worktree_file(repository_descriptor: int, path: bytes) -> bytes | None:
    """Read one tracked file through pinned, no-follow directory descriptors."""
    components = _tracked_path_components(path)
    directory_descriptor = os.dup(repository_descriptor)
    try:
        for component in components[:-1]:
            try:
                expected_status = os.stat(
                    component,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                if error.errno in {errno.ENOENT, errno.ENOTDIR}:
                    return None
                raise
            if not stat.S_ISDIR(expected_status.st_mode):
                return None
            try:
                child_descriptor = os.open(
                    component,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=directory_descriptor,
                )
            except OSError as error:
                if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                    return None
                raise
            try:
                actual_status = os.fstat(child_descriptor)
                if not stat.S_ISDIR(actual_status.st_mode) or not os.path.samestat(
                    expected_status, actual_status
                ):
                    raise OSError(
                        errno.ESTALE,
                        "tracked directory changed during whitespace validation: "
                        f"{_diagnostic_path(path)}",
                    )
            except BaseException:
                os.close(child_descriptor)
                raise
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor

        try:
            descriptor = os.open(
                components[-1],
                _FILE_OPEN_FLAGS,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                return None
            raise
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read()
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)


def validate_whitespace(*, cwd: Path | None = None) -> int:
    """Validate index blobs and tracked worktree bytes under repository policy."""
    try:
        repository_descriptor = _open_repository(cwd)
    except (OSError, ValueError) as error:
        print(f"whitespace validation error: {error}", file=sys.stderr)
        return 2
    try:
        pinned_cwd = Path(f"/proc/{os.getpid()}/fd/{repository_descriptor}")
        index = subprocess.run(  # noqa: S603 - vector is fixed above.
            _index_argv(),
            check=False,
            cwd=pinned_cwd,
            stdout=subprocess.PIPE,
        )
        if index.returncode != 0:
            return index.returncode
        try:
            entries = _regular_index_entries(index.stdout)
        except ValueError as error:
            print(f"whitespace validation error: {error}", file=sys.stderr)
            return 2

        issues: set[tuple[bytes, int, str]] = set()
        blobs: dict[str, bytes] = {}
        for path, object_id in entries:
            content = blobs.get(object_id)
            if content is None:
                blob = subprocess.run(  # noqa: S603 - id is hex-validated.
                    _blob_argv(object_id),
                    check=False,
                    cwd=pinned_cwd,
                    stdout=subprocess.PIPE,
                )
                if blob.returncode != 0:
                    return blob.returncode
                content = blob.stdout
                blobs[object_id] = content
            issues.update(
                (path, line_number, message)
                for line_number, message in whitespace_issues(content)
            )

        for path, _ in entries:
            try:
                content = _read_worktree_file(repository_descriptor, path)
            except (OSError, ValueError) as error:
                print(f"whitespace validation error: {error}", file=sys.stderr)
                return 2
            if content is not None:
                issues.update(
                    (path, line_number, message)
                    for line_number, message in whitespace_issues(content)
                )

        for path, line_number, message in sorted(
            issues,
            key=lambda issue: (
                issue[0],
                issue[1],
                _WHITESPACE_MESSAGE_ORDER[issue[2]],
            ),
        ):
            print(f"{_diagnostic_path(path)}:{line_number}: {message}")
        if issues:
            return 2
        return 0
    finally:
        os.close(repository_descriptor)


def main() -> int:
    """Run the whitespace validator in the current repository."""
    return validate_whitespace()


if __name__ == "__main__":
    raise SystemExit(main())
