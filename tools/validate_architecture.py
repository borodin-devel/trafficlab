"""Validate the repository architecture corpus deterministically."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote_to_bytes, urlsplit


@dataclass(frozen=True, order=True, slots=True)
class ValidationIssue:
    """One stable architecture-validation diagnostic."""

    path: PurePosixPath
    line: int
    code: str
    message: str

    def render(self) -> str:
        """Render the issue in a compiler-friendly, stable form."""
        return f"{self.path}:{self.line}: [{self.code}] {self.message}"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One repository-relative Markdown source and its decoded text."""

    path: PurePosixPath
    text: str


@dataclass(frozen=True, slots=True)
class Corpus:
    """An immutable snapshot consumed by pure validation rules."""

    architecture_root: PurePosixPath
    markdown_files: tuple[SourceFile, ...]
    existing_paths: frozenset[PurePosixPath]
    directories: frozenset[PurePosixPath]


@dataclass(frozen=True, slots=True)
class _MarkdownLink:
    line: int
    destination: str


def _normalize_repository_path(
    path: str | PurePosixPath, *, allow_root: bool = False
) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute():
        raise ValueError(f"path must be repository-relative: {path}")

    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized_parts:
                raise ValueError(f"path must be repository-relative: {path}")
            normalized_parts.pop()
            continue
        normalized_parts.append(part)

    if not normalized_parts:
        if allow_root:
            return PurePosixPath(".")
        raise ValueError(f"path must name a repository file: {path}")
    return PurePosixPath(*normalized_parts)


def _is_beneath(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def _parent_directories(path: PurePosixPath) -> Iterator[PurePosixPath]:
    parent = path.parent
    while True:
        yield parent
        if parent == PurePosixPath("."):
            return
        parent = parent.parent


def corpus_from_mapping(files: Mapping[str, str]) -> Corpus:
    """Build a deterministic in-memory corpus from repository-relative files."""
    architecture_root = PurePosixPath("architecture")
    normalized_files: dict[PurePosixPath, str] = {}
    for raw_path, text in files.items():
        path = _normalize_repository_path(raw_path)
        if path in normalized_files:
            raise ValueError(f"duplicate normalized repository path: {path}")
        normalized_files[path] = text

    existing_paths = frozenset(normalized_files)
    directories = frozenset(
        parent for path in normalized_files for parent in _parent_directories(path)
    )
    markdown_files = tuple(
        SourceFile(path, normalized_files[path])
        for path in sorted(normalized_files)
        if path.suffix == ".md" and _is_beneath(path, architecture_root)
    )
    return Corpus(
        architecture_root=architecture_root,
        markdown_files=markdown_files,
        existing_paths=existing_paths,
        directories=directories,
    )


def _resolve_directory(path: Path, description: str) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{description} is not a directory: {path}")
    return resolved


def _is_pruned_directory(name: str) -> bool:
    return name in {
        ".cache",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".worktrees",
        "__pycache__",
        "build",
        "dist",
    }


def _raise_walk_error(error: OSError) -> None:
    raise error


def load_corpus(repository_root: Path, architecture_root: Path) -> Corpus:
    """Load a safe immutable corpus snapshot from the filesystem boundary."""
    if architecture_root.is_symlink():
        raise ValueError(
            f"architecture root must not be a symlink: {architecture_root}"
        )
    resolved_repository = _resolve_directory(repository_root, "repository root")
    resolved_architecture = _resolve_directory(architecture_root, "architecture root")
    try:
        architecture_relative = resolved_architecture.relative_to(resolved_repository)
    except ValueError as error:
        raise ValueError(
            "architecture root must remain inside repository root: "
            f"{resolved_architecture}"
        ) from error

    existing_paths: set[PurePosixPath] = set()
    directories: set[PurePosixPath] = set()
    markdown_files: list[SourceFile] = []

    for current, directory_names, file_names in os.walk(
        resolved_repository,
        topdown=True,
        onerror=_raise_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        current_relative = PurePosixPath(
            current_path.relative_to(resolved_repository).as_posix()
        )
        directories.add(current_relative)

        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current_path / name
            mode = candidate.lstat().st_mode
            if not _is_pruned_directory(name) and stat.S_ISDIR(mode):
                kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            candidate = current_path / name
            if not stat.S_ISREG(candidate.lstat().st_mode):
                continue
            relative = PurePosixPath(
                candidate.relative_to(resolved_repository).as_posix()
            )
            existing_paths.add(relative)
            if candidate.suffix == ".md" and candidate.is_relative_to(
                resolved_architecture
            ):
                markdown_files.append(
                    SourceFile(relative, candidate.read_text(encoding="utf-8"))
                )

    return Corpus(
        architecture_root=PurePosixPath(architecture_relative.as_posix()),
        markdown_files=tuple(sorted(markdown_files, key=lambda source: source.path)),
        existing_paths=frozenset(existing_paths),
        directories=frozenset(directories),
    )


def _visible_markdown_lines(text: str) -> tuple[tuple[int, str], ...]:
    visible: list[tuple[int, str]] = []
    fence_character: str | None = None
    fence_length = 0
    opening_pattern = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")

    for line_number, line in enumerate(text.splitlines(), start=1):
        if fence_character is not None:
            closing_pattern = re.compile(
                rf"^[ ]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$"
            )
            if closing_pattern.match(line):
                fence_character = None
                fence_length = 0
            continue

        opening = opening_pattern.match(line)
        if opening is not None:
            fence = opening.group(1)
            fence_character = fence[0]
            fence_length = len(fence)
            continue
        visible.append((line_number, line))

    return tuple(visible)


def _reference_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _reference_definitions(
    lines: Sequence[tuple[int, str]],
) -> tuple[dict[str, str], frozenset[int]]:
    definition_pattern = re.compile(
        r"^[ ]{0,3}\[([^]\n]+)\]:[ \t]*(?:<([^>\n]*)>|([^\s]+))"
    )
    definitions: dict[str, str] = {}
    definition_lines: set[int] = set()
    for line_number, line in lines:
        match = definition_pattern.match(line)
        if match is None:
            continue
        label = _reference_label(match.group(1))
        definitions.setdefault(label, match.group(2) or match.group(3))
        definition_lines.add(line_number)
    return definitions, frozenset(definition_lines)


def _matched_bracket_closings(line: str) -> frozenset[int]:
    unmatched_openings: list[int] = []
    matched_closings: set[int] = set()
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "[":
            unmatched_openings.append(index)
        elif character == "]" and unmatched_openings:
            unmatched_openings.pop()
            matched_closings.add(index)
    return frozenset(matched_closings)


def _inline_destinations(line: str) -> Iterator[str]:
    matched_closings = _matched_bracket_closings(line)
    cursor = 0
    while True:
        opening = line.find("](", cursor)
        if opening < 0:
            return
        if opening not in matched_closings:
            cursor = opening + 2
            continue
        destination_start = opening + 2
        while destination_start < len(line) and line[destination_start].isspace():
            destination_start += 1

        if destination_start < len(line) and line[destination_start] == "<":
            destination_end = line.find(">", destination_start + 1)
            if destination_end < 0:
                cursor = opening + 2
                continue
            destination = line[destination_start + 1 : destination_end]
            closing = line.find(")", destination_end + 1)
            if closing < 0:
                cursor = opening + 2
                continue
        else:
            index = destination_start
            nested_parentheses = 0
            escaped = False
            while index < len(line):
                character = line[index]
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == "(":
                    nested_parentheses += 1
                elif character == ")":
                    if nested_parentheses == 0:
                        break
                    nested_parentheses -= 1
                elif character.isspace() and nested_parentheses == 0:
                    break
                index += 1
            destination = line[destination_start:index]
            closing = line.find(")", index)
            if closing < 0:
                cursor = opening + 2
                continue

        yield re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])", r"\1", destination)
        cursor = closing + 1


def _reference_destinations(line: str, definitions: Mapping[str, str]) -> Iterator[str]:
    occupied: list[tuple[int, int]] = []
    explicit_pattern = re.compile(r"!?\[([^]\n]+)\]\[([^]\n]*)\]")
    for match in explicit_pattern.finditer(line):
        label = match.group(2) or match.group(1)
        destination = definitions.get(_reference_label(label))
        if destination is not None:
            occupied.append(match.span())
            yield destination

    shortcut_pattern = re.compile(r"!?\[([^]\n]+)\]")
    for match in shortcut_pattern.finditer(line):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        following = line[match.end() : match.end() + 1]
        if following in {"(", "["}:
            continue
        destination = definitions.get(_reference_label(match.group(1)))
        if destination is not None:
            yield destination


def _markdown_links(source: SourceFile) -> tuple[_MarkdownLink, ...]:
    lines = _visible_markdown_lines(source.text)
    definitions, definition_lines = _reference_definitions(lines)
    links: list[_MarkdownLink] = []
    for line_number, line in lines:
        for destination in _inline_destinations(line):
            links.append(_MarkdownLink(line_number, destination))
        if line_number not in definition_lines:
            for destination in _reference_destinations(line, definitions):
                links.append(_MarkdownLink(line_number, destination))
    return tuple(links)


def _heading_text(line: str) -> str | None:
    match = re.match(r"^[ ]{0,3}#{1,6}(?:[ \t]+(.*?)|[ \t]*)$", line)
    if match is None:
        return None
    content = match.group(1) or ""
    return re.sub(r"[ \t]+#+[ \t]*$", "", content).strip()


def _heading_anchor(heading: str) -> str:
    anchor: list[str] = []
    for character in heading.casefold():
        if character.isspace():
            anchor.append("-")
        elif character.isalnum() or character in {"_", "-"}:
            anchor.append(character)
    return "".join(anchor)


def _heading_anchors(source: SourceFile) -> frozenset[str]:
    anchors: set[str] = set()
    duplicate_counts: dict[str, int] = {}
    lines = _visible_markdown_lines(source.text)
    setext_underline = re.compile(r"^[ ]{0,3}(?:=+|-+)[ \t]*$")
    for index, (line_number, line) in enumerate(lines):
        heading = _heading_text(line)
        if (
            heading is None
            and line.strip()
            and index + 1 < len(lines)
            and lines[index + 1][0] == line_number + 1
            and setext_underline.match(lines[index + 1][1])
        ):
            heading = line.strip()
        if heading is None:
            continue
        base = _heading_anchor(heading)
        duplicate_number = duplicate_counts.get(base, 0)
        anchor = base if duplicate_number == 0 else f"{base}-{duplicate_number}"
        duplicate_counts[base] = duplicate_number + 1
        anchors.add(anchor)
    return frozenset(anchors)


def _decode_uri_component(component: str) -> str:
    return unquote_to_bytes(component).decode("utf-8")


def _unsafe_link_issue(source: SourceFile, link: _MarkdownLink) -> ValidationIssue:
    return ValidationIssue(
        source.path,
        link.line,
        "LNK003",
        f"local target is unsafe: {link.destination}",
    )


def validate_links(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return missing, fragmented, and unsafe local Markdown-link issues."""
    anchors = {
        source.path: _heading_anchors(source) for source in corpus.markdown_files
    }
    issues: list[ValidationIssue] = []

    for source in corpus.markdown_files:
        for link in _markdown_links(source):
            try:
                parsed = urlsplit(link.destination)
                if parsed.scheme or parsed.netloc:
                    continue
                decoded_path = _decode_uri_component(parsed.path)
                decoded_fragment = _decode_uri_component(parsed.fragment)
                if "\x00" in decoded_path or PurePosixPath(decoded_path).is_absolute():
                    issues.append(_unsafe_link_issue(source, link))
                    continue
                target = (
                    source.path
                    if decoded_path == ""
                    else _normalize_repository_path(
                        source.path.parent / decoded_path, allow_root=True
                    )
                )
            except (UnicodeDecodeError, ValueError):
                issues.append(_unsafe_link_issue(source, link))
                continue

            if target not in corpus.existing_paths and target not in corpus.directories:
                issues.append(
                    ValidationIssue(
                        source.path,
                        link.line,
                        "LNK001",
                        f"local target does not exist: {target}",
                    )
                )
                continue
            if decoded_fragment and decoded_fragment not in anchors.get(
                target, frozenset()
            ):
                issues.append(
                    ValidationIssue(
                        source.path,
                        link.line,
                        "LNK002",
                        f"heading fragment does not exist: {target}#{decoded_fragment}",
                    )
                )

    return tuple(sorted(issues))


def validate_identifiers(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return one issue for every declaration of each duplicate SRS identifier."""
    declaration_pattern = re.compile(r"\*\*([A-Z0-9]+(?:-[A-Z0-9]+){2,})(?::)?\*\*")
    declarations: dict[str, list[tuple[PurePosixPath, int]]] = {}
    for source in corpus.markdown_files:
        if source.path.name != "SRS.md":
            continue
        for line_number, line in _visible_markdown_lines(source.text):
            for match in declaration_pattern.finditer(line):
                declarations.setdefault(match.group(1), []).append(
                    (source.path, line_number)
                )

    issues = [
        ValidationIssue(
            path,
            line,
            "SRS001",
            f"duplicate requirement identifier: {identifier}",
        )
        for identifier, occurrences in declarations.items()
        if len(occurrences) > 1
        for path, line in occurrences
    ]
    return tuple(sorted(issues))


def validate(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Run Task 2's pure acceptance rules and sort their combined issues."""
    issues = (*validate_links(corpus), *validate_identifiers(corpus))
    return tuple(sorted(issues))


def main(argv: Sequence[str] | None = None) -> int:
    """Load and validate an architecture root, returning a stable exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture_root", default="architecture", nargs="?")
    arguments = parser.parse_args(argv)
    repository_root = Path.cwd()
    requested_root = Path(arguments.architecture_root)
    if not requested_root.is_absolute():
        requested_root = repository_root / requested_root

    try:
        corpus = load_corpus(repository_root, requested_root)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"architecture validation error: {error}", file=sys.stderr)
        return 2

    issues = validate(corpus)
    for issue in issues:
        print(issue.render())
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
