"""Private, deterministic Markdown parsing for architecture validation."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    """One Markdown link with its physical start line and following block text."""

    line: int
    destination: str
    suffix: str = ""


@dataclass(frozen=True, slots=True)
class InlineLink:
    """One parsed link span within a Markdown text block."""

    start: int
    end: int
    destination: str


def visible_markdown_lines(text: str) -> tuple[tuple[int, str], ...]:
    """Return physical source lines outside fenced code blocks."""
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


def reference_definitions(
    lines: Sequence[tuple[int, str]],
) -> tuple[dict[str, str], frozenset[int]]:
    """Return normalized link definitions and their physical source lines."""
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


def _matched_brackets(line: str) -> dict[int, int]:
    unmatched_openings: list[int] = []
    matched: dict[int, int] = {}
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "[":
            unmatched_openings.append(index)
        elif character == "]" and unmatched_openings:
            matched[index] = unmatched_openings.pop()
    return matched


def _markdown_title_end(line: str, start: int) -> int | None:
    closing_character = {
        '"': '"',
        "'": "'",
        "(": ")",
    }.get(line[start])
    if closing_character is None:
        return None

    escaped = False
    for index in range(start + 1, len(line)):
        character = line[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == closing_character:
            return index + 1
    return None


def inline_links(line: str) -> Iterator[InlineLink]:
    """Yield valid inline links and exact spans from one Markdown text block."""
    matched_brackets = _matched_brackets(line)
    cursor = 0
    while True:
        opening = line.find("](", cursor)
        if opening < 0:
            return
        label_start = matched_brackets.get(opening)
        if label_start is None:
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
            suffix_start = destination_end + 1
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
            suffix_start = index

        suffix_index = suffix_start
        had_whitespace = False
        while suffix_index < len(line) and line[suffix_index].isspace():
            had_whitespace = True
            suffix_index += 1

        if suffix_index < len(line) and line[suffix_index] == ")":
            closing = suffix_index
        elif had_whitespace and suffix_index < len(line):
            title_end = _markdown_title_end(line, suffix_index)
            if title_end is None:
                cursor = opening + 2
                continue
            suffix_index = title_end
            while suffix_index < len(line) and line[suffix_index].isspace():
                suffix_index += 1
            if suffix_index >= len(line) or line[suffix_index] != ")":
                cursor = opening + 2
                continue
            closing = suffix_index
        else:
            cursor = opening + 2
            continue

        yield InlineLink(
            label_start,
            closing + 1,
            re.sub(
                r"\\([!\"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])",
                r"\1",
                destination,
            ),
        )
        cursor = closing + 1


def _reference_links(line: str, definitions: Mapping[str, str]) -> Iterator[InlineLink]:
    occupied: list[tuple[int, int]] = []
    explicit_pattern = re.compile(r"!?\[([^]\n]+)\]\[([^]\n]*)\]")
    for match in explicit_pattern.finditer(line):
        label = match.group(2) or match.group(1)
        destination = definitions.get(_reference_label(label))
        if destination is not None:
            occupied.append(match.span())
            yield InlineLink(match.start(), match.end(), destination)

    shortcut_pattern = re.compile(r"!?\[([^]\n]+)\]")
    for match in shortcut_pattern.finditer(line):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        following = line[match.end() : match.end() + 1]
        if following in {"(", "["}:
            continue
        destination = definitions.get(_reference_label(match.group(1)))
        if destination is not None:
            yield InlineLink(match.start(), match.end(), destination)


def reference_destinations(line: str, definitions: Mapping[str, str]) -> Iterator[str]:
    """Yield reference-style destinations used in one Markdown text block."""
    for link in _reference_links(line, definitions):
        yield link.destination


def _inline_markdown_blocks(
    lines: Sequence[tuple[int, str]],
) -> Iterator[tuple[int, str]]:
    block: list[str] = []
    block_start = 0
    previous_line = 0
    list_item = re.compile(r"^[ ]{0,3}(?:[-+*]|[0-9]+[.)])[ \t]+")

    for line_number, line in lines:
        starts_new_block = bool(block) and (
            line_number != previous_line + 1 or list_item.match(line) is not None
        )
        if not line.strip() or starts_new_block:
            if block:
                yield block_start, "\n".join(block)
                block = []
            if not line.strip():
                previous_line = line_number
                continue
        if not block:
            block_start = line_number
        block.append(line)
        previous_line = line_number

    if block:
        yield block_start, "\n".join(block)


def markdown_links(text: str) -> tuple[MarkdownLink, ...]:
    """Return inline and reference links with physical line locations."""
    lines = visible_markdown_lines(text)
    definitions, definition_lines = reference_definitions(lines)
    links: list[MarkdownLink] = []

    for block_start, block in _inline_markdown_blocks(lines):
        for link in inline_links(block):
            link_line = block_start + block[: link.start].count("\n")
            links.append(MarkdownLink(link_line, link.destination, block[link.end :]))
        for link in _reference_links(block, definitions):
            link_line = block_start + block[: link.start].count("\n")
            if link_line not in definition_lines:
                links.append(
                    MarkdownLink(link_line, link.destination, block[link.end :])
                )
    return tuple(links)


def heading_text(line: str) -> str | None:
    """Return normalized ATX heading text, or ``None`` for a non-heading."""
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


def heading_anchors(text: str) -> frozenset[str]:
    """Return deterministic GitHub-style anchors for visible headings."""
    anchors: set[str] = set()
    duplicate_counts: dict[str, int] = {}
    lines = visible_markdown_lines(text)
    setext_underline = re.compile(r"^[ ]{0,3}(?:=+|-+)[ \t]*$")
    for index, (line_number, line) in enumerate(lines):
        heading = heading_text(line)
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


def markdown_table_cells(line: str) -> tuple[str, ...] | None:
    """Split one pipe-table row while preserving escaped pipe characters."""
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
