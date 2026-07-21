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


@dataclass(frozen=True, slots=True)
class _RoadmapEntry:
    line: int
    level: int
    number: str
    status: str
    body: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class _RegistryRow:
    line: int
    cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RegistryTable:
    line: int
    section: str
    headers: tuple[str, ...]
    rows: tuple[_RegistryRow, ...]


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


def validate_naming(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return invalid architecture Markdown filename and directory issues."""
    filename_pattern = re.compile(r"(?=.*[A-Z])[A-Z0-9_]+\.md")
    directory_pattern = re.compile(r"(?=.*[a-z])[a-z0-9_]+")
    issues: list[ValidationIssue] = []

    markdown_paths = sorted(
        path
        for path in corpus.existing_paths
        if _is_beneath(path, corpus.architecture_root)
        and path.suffix.casefold() == ".md"
    )
    for path in markdown_paths:
        if filename_pattern.fullmatch(path.name) is None:
            issues.append(
                ValidationIssue(
                    path,
                    1,
                    "NAM001",
                    "Markdown filename must use uppercase snake case",
                )
            )

    for directory in sorted(corpus.directories):
        if not _is_beneath(directory, corpus.architecture_root):
            continue
        if directory_pattern.fullmatch(directory.name) is None:
            issues.append(
                ValidationIssue(
                    directory,
                    1,
                    "NAM001",
                    "architecture directory must use lowercase snake case",
                )
            )

    return tuple(sorted(issues))


def _component_directories(corpus: Corpus) -> tuple[PurePosixPath, ...]:
    component_roots = {
        corpus.architecture_root / name
        for name in (
            "apps",
            "contracts",
            "genetic_models",
            "libs",
            "scripts",
            "similarity_methods",
            "traffic_models",
        )
    }
    components = {
        directory
        for directory in corpus.directories
        if directory.parent in component_roots
    }
    components.update(
        directory
        for directory in (
            corpus.architecture_root / "infrastructure",
            corpus.architecture_root / "project",
        )
        if directory in corpus.directories
    )
    return tuple(sorted(components))


def validate_component_documents(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return missing required owner-document issues for each component."""
    required_documents = ("README.md", "ROADMAP.md", "SAD.md", "SRS.md")
    issues = [
        ValidationIssue(
            component / document,
            1,
            "DOC001",
            f"component document is missing: {document}",
        )
        for component in _component_directories(corpus)
        for document in required_documents
        if component / document not in corpus.existing_paths
    ]
    return tuple(sorted(issues))


def _level_two_heading_line(
    lines: Sequence[tuple[int, str]], heading: str
) -> int | None:
    pattern = re.compile(
        rf"^[ ]{{0,3}}##(?!#)[ \t]+{re.escape(heading)}"
        r"(?:[ \t]+#+)?[ \t]*$"
    )
    return next(
        (line_number for line_number, line in lines if pattern.fullmatch(line)), None
    )


def validate_srs_structure(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return missing acceptance-criterion and traceability structure issues."""
    acceptance_identifier = re.compile(
        r"\*\*[A-Z0-9]+(?:-[A-Z0-9]+)*-AC-[A-Z0-9]+(?::)?\*\*"
    )
    issues: list[ValidationIssue] = []

    for source in corpus.markdown_files:
        if source.path.name != "SRS.md":
            continue
        lines = _visible_markdown_lines(source.text)
        acceptance_line = _level_two_heading_line(lines, "Acceptance Criteria")
        traceability_line = _level_two_heading_line(lines, "Traceability")

        if acceptance_line is None:
            issues.append(
                ValidationIssue(
                    source.path,
                    1,
                    "SRS002",
                    "SRS is missing the Acceptance Criteria heading",
                )
            )
        if not any(acceptance_identifier.search(line) for _, line in lines):
            issues.append(
                ValidationIssue(
                    source.path,
                    acceptance_line or 1,
                    "SRS002",
                    "SRS does not declare an acceptance-criterion identifier",
                )
            )
        if traceability_line is None:
            issues.append(
                ValidationIssue(
                    source.path,
                    1,
                    "SRS002",
                    "SRS is missing the Traceability heading",
                )
            )
        elif not any(
            link.line > traceability_line
            and _resolved_local_link_target(source, link) is not None
            for link in _markdown_links(source)
        ):
            issues.append(
                ValidationIssue(
                    source.path,
                    traceability_line,
                    "SRS002",
                    "SRS Traceability section has no local link",
                )
            )

    return tuple(sorted(issues))


_ROADMAP_STATUS_PATTERN = (
    r"(?:PLAN|BLKD|CR_B|MK_B|MN_B|TSTR|DONE| {2}[1-9]%| [1-9][0-9]%)"
)
_ROADMAP_HEADING_PATTERNS = (
    re.compile(
        rf"^[ ]{{0,3}}## \[(?P<status>{_ROADMAP_STATUS_PATTERN})\] "
        r"STAGE (?P<number>[1-9][0-9]*) — \S.*$"
    ),
    re.compile(
        rf"^[ ]{{0,3}}### \[(?P<status>{_ROADMAP_STATUS_PATTERN})\] "
        r"STEP (?P<number>[1-9][0-9]*\.[1-9][0-9]*) — \S.*$"
    ),
    re.compile(
        rf"^[ ]{{0,3}}#### \[(?P<status>{_ROADMAP_STATUS_PATTERN})\] "
        r"SUBSTEP (?P<number>[1-9][0-9]*\.[1-9][0-9]*\.[1-9][0-9]*) "
        r"— \S.*$"
    ),
)
_ROADMAP_CANDIDATE_PATTERN = re.compile(
    r"^[ ]{0,3}#{1,6}[ \t]+(?:\[[^]\n]+\][ \t]+|.*\b(?:STAGE|STEP|SUBSTEP)\b)",
    re.IGNORECASE,
)


def _roadmap_entries(
    source: SourceFile,
) -> tuple[tuple[_RoadmapEntry, ...], tuple[ValidationIssue, ...]]:
    lines = _visible_markdown_lines(source.text)
    candidates: list[tuple[int, int, re.Match[str] | None]] = []
    issues: list[ValidationIssue] = []

    for index, (line_number, line) in enumerate(lines):
        if re.match(r"^[ \t]*(?:-[ \t]+)?\*\*Status:\*\*", line):
            issues.append(
                ValidationIssue(
                    source.path,
                    line_number,
                    "RDM001",
                    "roadmap status must appear in its hierarchy heading",
                )
            )
        if _ROADMAP_CANDIDATE_PATTERN.match(line) is None:
            continue
        match = next(
            (
                pattern.fullmatch(line)
                for pattern in _ROADMAP_HEADING_PATTERNS
                if pattern.fullmatch(line) is not None
            ),
            None,
        )
        candidates.append((index, line_number, match))
        if match is None:
            issues.append(
                ValidationIssue(
                    source.path,
                    line_number,
                    "RDM001",
                    "invalid roadmap hierarchy heading",
                )
            )

    entries: list[_RoadmapEntry] = []
    for candidate_index, (line_index, line_number, match) in enumerate(candidates):
        if match is None:
            continue
        next_line_index = (
            candidates[candidate_index + 1][0]
            if candidate_index + 1 < len(candidates)
            else len(lines)
        )
        status = match.group("status").strip()
        level = len(lines[line_index][1].lstrip().split(" ", maxsplit=1)[0])
        entries.append(
            _RoadmapEntry(
                line=line_number,
                level=level,
                number=match.group("number"),
                status=status,
                body=tuple(lines[line_index + 1 : next_line_index]),
            )
        )

    if not candidates:
        issues.append(
            ValidationIssue(
                source.path,
                1,
                "RDM001",
                "roadmap has no hierarchy headings",
            )
        )
    return tuple(entries), tuple(sorted(issues))


def _entry_has_field(entry: _RoadmapEntry, field: str) -> bool:
    pattern = re.compile(rf"^[ \t]*-[ \t]+\*\*{re.escape(field)}:\*\*[ \t]+.*\S[ \t]*$")
    return any(pattern.fullmatch(line) for _, line in entry.body)


def _expected_parent_status(statuses: Sequence[str]) -> str:
    for status in ("CR_B", "MK_B", "MN_B", "BLKD"):
        if status in statuses:
            return status
    if all(status == "PLAN" for status in statuses):
        return "PLAN"
    if all(status == "DONE" for status in statuses):
        return "DONE"
    if all(status in {"DONE", "TSTR"} for status in statuses):
        return "TSTR"

    estimates = [
        0
        if status == "PLAN"
        else 100
        if status in {"DONE", "TSTR"}
        else int(status.removesuffix("%"))
        for status in statuses
    ]
    rounded_mean = (2 * sum(estimates) + len(estimates)) // (2 * len(estimates))
    return f"{min(99, max(1, rounded_mean))}%"


def _roadmap_parent_issues(
    source: SourceFile, entries: Sequence[_RoadmapEntry]
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    children: dict[int, list[int]] = {index: [] for index in range(len(entries))}
    stack: list[int] = []

    for index, entry in enumerate(entries):
        while stack and entries[stack[-1]].level >= entry.level:
            stack.pop()
        if entry.level > 2:
            if not stack or entries[stack[-1]].level != entry.level - 1:
                issues.append(
                    ValidationIssue(
                        source.path,
                        entry.line,
                        "RDM001",
                        "roadmap hierarchy entry has no immediate parent",
                    )
                )
            else:
                parent_index = stack[-1]
                children[parent_index].append(index)
                expected_prefix = f"{entries[parent_index].number}."
                if not entry.number.startswith(expected_prefix):
                    issues.append(
                        ValidationIssue(
                            source.path,
                            entry.line,
                            "RDM001",
                            "roadmap hierarchy number does not match its parent",
                        )
                    )
        stack.append(index)

    for index, child_indexes in children.items():
        if not child_indexes:
            continue
        expected = _expected_parent_status(
            [entries[child_index].status for child_index in child_indexes]
        )
        parent = entries[index]
        if parent.status != expected:
            issues.append(
                ValidationIssue(
                    source.path,
                    parent.line,
                    "RDM004",
                    f"parent status must be {expected} from its immediate children",
                )
            )

    return tuple(sorted(issues))


def validate_roadmaps(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return roadmap heading, body, evidence, and parent-status issues."""
    common_fields = (
        "Task",
        "Deliverable",
        "Applicable test types",
        "Completion criteria",
    )
    substep_fields = (
        "Objective",
        "Implementation",
        "Affected files",
        "Dependencies",
        "Outputs",
        "Tests",
        "Validation",
        "Completion criteria",
    )
    issues: list[ValidationIssue] = []

    for source in corpus.markdown_files:
        if source.path.name != "ROADMAP.md":
            continue
        entries, parse_issues = _roadmap_entries(source)
        issues.extend(parse_issues)
        for entry in entries:
            required_fields = substep_fields if entry.level == 4 else common_fields
            for field in required_fields:
                if not _entry_has_field(entry, field):
                    issues.append(
                        ValidationIssue(
                            source.path,
                            entry.line,
                            "RDM002",
                            f"roadmap entry is missing field: {field}",
                        )
                    )
            if entry.status != "PLAN" and not _entry_has_field(entry, "Evidence"):
                issues.append(
                    ValidationIssue(
                        source.path,
                        entry.line,
                        "RDM003",
                        "non-plan roadmap entry is missing Evidence",
                    )
                )
        issues.extend(_roadmap_parent_issues(source, entries))

    return tuple(sorted(issues))


def _resolved_local_link_target(
    source: SourceFile, link: _MarkdownLink
) -> PurePosixPath | None:
    try:
        parsed = urlsplit(link.destination)
        if parsed.scheme or parsed.netloc:
            return None
        decoded_path = _decode_uri_component(parsed.path)
        if "\x00" in decoded_path or PurePosixPath(decoded_path).is_absolute():
            return None
        if decoded_path == "":
            return source.path
        return _normalize_repository_path(
            source.path.parent / decoded_path, allow_root=True
        )
    except (UnicodeDecodeError, ValueError):
        return None


def validate_roadmap_links(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return central/component roadmap reciprocity and cardinality issues."""
    roadmaps = tuple(
        source for source in corpus.markdown_files if source.path.name == "ROADMAP.md"
    )
    if not roadmaps:
        return ()

    central_path = corpus.architecture_root / "project" / "ROADMAP.md"
    sources = {source.path: source for source in roadmaps}
    central = sources.get(central_path)
    component_paths = tuple(sorted(path for path in sources if path != central_path))
    issues: list[ValidationIssue] = []

    if central is None:
        return tuple(
            ValidationIssue(
                path,
                1,
                "RDM005",
                "component roadmap cannot link missing central roadmap: "
                f"{central_path}",
            )
            for path in component_paths
        )

    central_targets = [
        target
        for link in _markdown_links(central)
        if (target := _resolved_local_link_target(central, link)) is not None
    ]
    for path in component_paths:
        count = central_targets.count(path)
        if count != 1:
            issues.append(
                ValidationIssue(
                    central.path,
                    1,
                    "RDM005",
                    f"central roadmap must link {path} exactly once; found {count}",
                )
            )

        component = sources[path]
        backlink_count = sum(
            _resolved_local_link_target(component, link) == central_path
            for link in _markdown_links(component)
        )
        if backlink_count != 1:
            issues.append(
                ValidationIssue(
                    component.path,
                    1,
                    "RDM005",
                    "component roadmap must link the central roadmap exactly once; "
                    f"found {backlink_count}",
                )
            )

    return tuple(sorted(issues))


_REGISTRY_IDENTITY_HEADERS = ("Selectable name", "Planned name", "Method", "Name")


def _markdown_table_cells(line: str) -> tuple[str, ...] | None:
    text = line.strip()
    if "|" not in text:
        return None

    cells: list[str] = []
    cell: list[str] = []
    escaped = False
    for character in text:
        if escaped:
            cell.append(character)
            escaped = False
        elif character == "\\":
            cell.append(character)
            escaped = True
        elif character == "|":
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(character)
    cells.append("".join(cell).strip())

    if text.startswith("|"):
        cells.pop(0)
    if text.endswith("|"):
        cells.pop()
    return tuple(cells)


def _is_table_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells
    )


def _nearest_level_two_heading(
    lines: Sequence[tuple[int, str]], before_line: int
) -> str:
    heading = ""
    for line_number, line in lines:
        if line_number >= before_line:
            break
        candidate = _heading_text(line)
        if re.match(r"^[ ]{0,3}##(?!#)(?:[ \t]+|$)", line) and candidate is not None:
            heading = candidate
    return heading


def _registry_tables(
    source: SourceFile,
) -> tuple[tuple[_RegistryTable, ...], tuple[ValidationIssue, ...]]:
    lines = _visible_markdown_lines(source.text)
    tables: list[_RegistryTable] = []
    issues: list[ValidationIssue] = []
    index = 0

    while index < len(lines):
        line_number, line = lines[index]
        headers = _markdown_table_cells(line)
        if headers is None or not any(
            header in _REGISTRY_IDENTITY_HEADERS for header in headers
        ):
            index += 1
            continue

        separator = (
            _markdown_table_cells(lines[index + 1][1])
            if index + 1 < len(lines)
            else None
        )
        if separator is None or not _is_table_separator(separator):
            issues.append(
                ValidationIssue(
                    source.path,
                    line_number,
                    "REG001",
                    "registry table header must be followed by a separator row",
                )
            )
            index += 1
            continue
        if len(separator) != len(headers):
            issues.append(
                ValidationIssue(
                    source.path,
                    lines[index + 1][0],
                    "REG001",
                    "registry table separator does not match its header",
                )
            )

        row_index = index + 2
        rows: list[_RegistryRow] = []
        while row_index < len(lines):
            row_line, row_text = lines[row_index]
            row_cells = _markdown_table_cells(row_text)
            if row_cells is None:
                break
            rows.append(_RegistryRow(row_line, row_cells))
            row_index += 1

        tables.append(
            _RegistryTable(
                line=line_number,
                section=_nearest_level_two_heading(lines, line_number),
                headers=headers,
                rows=tuple(rows),
            )
        )
        index = row_index

    return tuple(tables), tuple(sorted(issues))


def _registry_identity_index(headers: Sequence[str]) -> int:
    return next(
        index
        for index, header in enumerate(headers)
        if header in _REGISTRY_IDENTITY_HEADERS
    )


def _single_link_destination(cell: str, definitions: Mapping[str, str]) -> str | None:
    stripped = cell.strip()
    is_inline_link = re.fullmatch(r"\[[^]\n]+\]\(.+\)", stripped) is not None
    is_reference_link = (
        re.fullmatch(r"\[[^]\n]+\](?:\[[^]\n]*\])?", stripped) is not None
    )
    if not (is_inline_link or is_reference_link):
        return None

    destinations = (
        *tuple(_inline_destinations(stripped)),
        *tuple(_reference_destinations(stripped, definitions)),
    )
    return destinations[0] if len(destinations) == 1 else None


def _owner_cell_is_valid(
    source: SourceFile,
    cell: str,
    definitions: Mapping[str, str],
    component: PurePosixPath,
) -> bool:
    destination = _single_link_destination(cell, definitions)
    if destination is None:
        return False
    try:
        parsed = urlsplit(destination)
        decoded_path = _decode_uri_component(parsed.path)
    except (UnicodeDecodeError, ValueError):
        return False
    expected_destination = f"{component.name}/README.md"
    return (
        not parsed.scheme
        and not parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and decoded_path == expected_destination
        and _resolved_local_link_target(source, _MarkdownLink(1, destination))
        == component / "README.md"
    )


def _is_explicit_unselectable_section(heading: str) -> bool:
    normalized = " ".join(heading.casefold().split())
    return normalized == "unresolved, unselectable" or normalized.startswith(
        "unresolved, unselectable "
    )


def _genetic_status_is_valid(status: str) -> bool:
    normalized = " ".join(status.casefold().split())
    if "planned" in normalized or "unresolved" in normalized:
        return "unselectable" in normalized
    return normalized == "selectable"


def validate_registries(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return registry table, row, owner, and selectability issues."""
    registry_roots = tuple(
        corpus.architecture_root / name
        for name in ("traffic_models", "genetic_models", "similarity_methods")
    )
    sources = {source.path: source for source in corpus.markdown_files}
    issues: list[ValidationIssue] = []

    for root in registry_roots:
        components = tuple(
            sorted(
                directory
                for directory in corpus.directories
                if directory.parent == root
            )
        )
        if not components:
            continue
        registry_path = root / "README.md"
        registry = sources.get(registry_path)
        if registry is None:
            issues.append(
                ValidationIssue(
                    registry_path,
                    1,
                    "REG001",
                    "component registry README is missing",
                )
            )
            continue

        visible_lines = _visible_markdown_lines(registry.text)
        definitions, _ = _reference_definitions(visible_lines)
        tables, table_issues = _registry_tables(registry)
        issues.extend(table_issues)
        components_by_name = {component.name: component for component in components}
        rows_by_name: dict[str, list[int]] = {
            component.name: [] for component in components
        }

        for table in tables:
            identity_index = _registry_identity_index(table.headers)
            identity_header = table.headers[identity_index]
            owner_indexes = [
                index for index, header in enumerate(table.headers) if header == "Owner"
            ]
            owner_index = owner_indexes[0] if len(owner_indexes) == 1 else None
            if owner_index is None:
                issues.append(
                    ValidationIssue(
                        registry.path,
                        table.line,
                        "REG001",
                        "registry table must contain exactly one Owner column",
                    )
                )

            is_genetic = root.name == "genetic_models"
            status_indexes = [
                index
                for index, header in enumerate(table.headers)
                if header == "Status"
            ]
            status_index = status_indexes[0] if len(status_indexes) == 1 else None
            if is_genetic and status_index is None:
                issues.append(
                    ValidationIssue(
                        registry.path,
                        table.line,
                        "REG001",
                        "genetic registry table must contain exactly one Status column",
                    )
                )

            planned_table = identity_header == "Planned name"
            unselectable_section = _is_explicit_unselectable_section(table.section)
            if not is_genetic and planned_table and not unselectable_section:
                issues.append(
                    ValidationIssue(
                        registry.path,
                        table.line,
                        "REG001",
                        "planned registry table must be in an explicitly "
                        "Unresolved, Unselectable section",
                    )
                )

            for row in table.rows:
                if len(row.cells) != len(table.headers):
                    issues.append(
                        ValidationIssue(
                            registry.path,
                            row.line,
                            "REG001",
                            "registry row does not match its named columns",
                        )
                    )
                    continue

                identity_match = re.fullmatch(r"`([^`\n]+)`", row.cells[identity_index])
                if identity_match is None:
                    issues.append(
                        ValidationIssue(
                            registry.path,
                            row.line,
                            "REG001",
                            "registry component name must be one backtick-wrapped "
                            "directory name",
                        )
                    )
                    continue

                name = identity_match.group(1)
                component = components_by_name.get(name)
                if component is None:
                    issues.append(
                        ValidationIssue(
                            registry.path,
                            row.line,
                            "REG001",
                            f"registry names unknown component: {name}",
                        )
                    )
                    continue

                rows_by_name[name].append(row.line)
                if owner_index is None or not _owner_cell_is_valid(
                    registry,
                    row.cells[owner_index],
                    definitions,
                    component,
                ):
                    issues.append(
                        ValidationIssue(
                            registry.path,
                            row.line,
                            "REG001",
                            f"Owner cell must be exactly one link to {name}/README.md",
                        )
                    )

                if is_genetic and (
                    status_index is None
                    or not _genetic_status_is_valid(row.cells[status_index])
                ):
                    issues.append(
                        ValidationIssue(
                            registry.path,
                            row.line,
                            "REG001",
                            "genetic registry Status must be Selectable or describe "
                            "planned/unresolved work as unselectable",
                        )
                    )

        for name, row_lines in rows_by_name.items():
            if len(row_lines) != 1:
                issues.append(
                    ValidationIssue(
                        registry.path,
                        row_lines[0] if row_lines else 1,
                        "REG001",
                        f"registry must contain {name} exactly once; "
                        f"found {len(row_lines)}",
                    )
                )

    return tuple(sorted(issues))


def validate_hygiene(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Return trailing-whitespace and obsolete-gitkeep issues."""
    issues = [
        ValidationIssue(
            source.path,
            line_number,
            "HYG001",
            "architecture Markdown line has trailing whitespace",
        )
        for source in corpus.markdown_files
        for line_number, line in enumerate(source.text.splitlines(), start=1)
        if line.endswith((" ", "\t"))
    ]

    for gitkeep in sorted(
        path for path in corpus.existing_paths if path.name == ".gitkeep"
    ):
        has_other_path = any(
            path != gitkeep and path.parent == gitkeep.parent
            for path in corpus.existing_paths
        ) or any(directory.parent == gitkeep.parent for directory in corpus.directories)
        if has_other_path:
            issues.append(
                ValidationIssue(
                    gitkeep,
                    1,
                    "HYG002",
                    ".gitkeep is obsolete because its directory is not empty",
                )
            )

    return tuple(sorted(issues))


def validate(corpus: Corpus) -> tuple[ValidationIssue, ...]:
    """Run the pure architecture acceptance rules and sort combined issues."""
    issues = (
        *validate_links(corpus),
        *validate_identifiers(corpus),
        *validate_naming(corpus),
        *validate_component_documents(corpus),
        *validate_srs_structure(corpus),
        *validate_roadmaps(corpus),
        *validate_roadmap_links(corpus),
        *validate_registries(corpus),
        *validate_hygiene(corpus),
    )
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
