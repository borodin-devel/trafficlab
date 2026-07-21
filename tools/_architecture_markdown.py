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


def is_table_separator(cells: Sequence[str]) -> bool:
    """Return whether every cell is one GFM delimiter-table separator."""
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells
    )


def _table_row_content(line: str) -> tuple[int, str] | None:
    """Return blockquote depth and table-visible text, excluding code blocks."""
    content = line
    blockquote_depth = 0
    blockquote_marker = re.compile(r"^[ ]{0,3}>[ \t]?")
    while (match := blockquote_marker.match(content)) is not None:
        blockquote_depth += 1
        content = content[match.end() :]

    if re.match(r"^(?: {4}|\t)", content) is not None:
        return None
    return blockquote_depth, content


def _structural_table_row_lines(
    lines: Sequence[tuple[int, str]],
) -> frozenset[int]:
    table_rows: set[int] = set()
    index = 0

    while index + 1 < len(lines):
        header_line, header_text = lines[index]
        delimiter_line, delimiter_text = lines[index + 1]
        header_row = _table_row_content(header_text)
        delimiter_row = _table_row_content(delimiter_text)
        header = markdown_table_cells(header_row[1]) if header_row is not None else None
        delimiter = (
            markdown_table_cells(delimiter_row[1])
            if delimiter_row is not None
            else None
        )
        if (
            delimiter_line != header_line + 1
            or header_row is None
            or delimiter_row is None
            or header_row[0] != delimiter_row[0]
            or header is None
            or delimiter is None
            or len(header) != len(delimiter)
            or not is_table_separator(delimiter)
        ):
            index += 1
            continue

        table_rows.update((header_line, delimiter_line))
        previous_line = delimiter_line
        row_index = index + 2
        while row_index < len(lines):
            row_line, row_text = lines[row_index]
            if row_line != previous_line + 1:
                break
            row = _table_row_content(row_text)
            if (
                row is None
                or row[0] != header_row[0]
                or markdown_table_cells(row[1]) is None
            ):
                break
            table_rows.add(row_line)
            previous_line = row_line
            row_index += 1
        index = row_index

    return frozenset(table_rows)


def _inline_markdown_blocks(
    lines: Sequence[tuple[int, str]],
) -> Iterator[tuple[int, str]]:
    block: list[str] = []
    block_start = 0
    block_kind = ""
    previous_line = 0
    atx_heading = re.compile(r"^[ ]{0,3}#{1,6}(?:[ \t]+|$)")
    blockquote = re.compile(r"^[ ]{0,3}>")
    blank_blockquote = re.compile(r"^[ ]{0,3}>[ \t]*$")
    indented_code = re.compile(r"^(?: {4}|\t)")
    list_item = re.compile(r"^[ ]{0,3}(?:[-+*]|[0-9]+[.)])[ \t]+")
    thematic_break = re.compile(
        r"^[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
    )
    table_row_lines = _structural_table_row_lines(lines)

    def finish_block() -> tuple[int, str] | None:
        nonlocal block, block_kind
        if not block:
            return None
        finished = (block_start, "\n".join(block))
        block = []
        block_kind = ""
        return finished

    for line_number, line in lines:
        if line_number != previous_line + 1 or not line.strip():
            if (finished := finish_block()) is not None:
                yield finished
            if not line.strip():
                previous_line = line_number
                continue

        if not block and indented_code.match(line) is not None:
            previous_line = line_number
            continue

        is_edge_pipe_row = line.lstrip().startswith("|") or line.rstrip().endswith("|")
        if line_number in table_row_lines or is_edge_pipe_row:
            if (finished := finish_block()) is not None:
                yield finished
            yield line_number, line
            previous_line = line_number
            continue

        if thematic_break.fullmatch(line) or blank_blockquote.fullmatch(line):
            if (finished := finish_block()) is not None:
                yield finished
            previous_line = line_number
            continue

        if atx_heading.match(line) is not None:
            if (finished := finish_block()) is not None:
                yield finished
            yield line_number, line
            previous_line = line_number
            continue

        if blockquote.match(line) is not None:
            quote_content = blockquote.sub("", line, count=1).lstrip()
            quote_starts_paragraph = bool(quote_content) and not (
                atx_heading.match(quote_content) is not None
                or thematic_break.fullmatch(quote_content) is not None
                or indented_code.match(quote_content) is not None
                or list_item.match(quote_content) is not None
                or quote_content.startswith("|")
                or quote_content.endswith("|")
            )
            if not quote_starts_paragraph:
                if (finished := finish_block()) is not None:
                    yield finished
                yield line_number, line
                previous_line = line_number
                continue
            if block_kind != "blockquote_paragraph":
                if (finished := finish_block()) is not None:
                    yield finished
                block_start = line_number
                block_kind = "blockquote_paragraph"
            block.append(line)
            previous_line = line_number
            continue

        if list_item.match(line) is not None:
            if (finished := finish_block()) is not None:
                yield finished
            block_start = line_number
            block_kind = "list_item"
            block.append(line)
            previous_line = line_number
            continue

        if block_kind == "blockquote_paragraph":
            block.append(line)
            previous_line = line_number
            continue
        if not block:
            block_start = line_number
            block_kind = "paragraph"
        block.append(line)
        previous_line = line_number

    if (finished := finish_block()) is not None:
        yield finished


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
