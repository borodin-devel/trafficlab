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


@dataclass(frozen=True, slots=True)
class _ListItemContent:
    content_indent: int
    content: str
    ordered_start: int | None
    is_indented_code: bool


@dataclass(frozen=True, slots=True)
class _ContainerLine:
    line: int
    raw: str
    content: str
    signature: tuple[tuple[str, int], ...]
    is_indented_code: bool
    starts_list_item: bool = False
    ordered_start: int | None = None


def visible_markdown_lines(text: str) -> tuple[tuple[int, str], ...]:
    """Return physical source lines outside fenced code blocks."""
    visible: list[tuple[int, str]] = []
    fence_character: str | None = None
    fence_length = 0
    fence_signature: tuple[tuple[str, int], ...] = ()
    physical_lines = tuple(enumerate(text.splitlines(), start=1))

    for normalized in _container_lines(physical_lines):
        if fence_character is not None:
            open_fence_character = fence_character
            preserves_list_container = any(
                container == "list" for container, _ in fence_signature
            )
            fenced_content = _strip_container_signature(normalized.raw, fence_signature)
            if not normalized.raw.strip():
                if any(container == "quote" for container, _ in fence_signature):
                    fence_character = None
                    fence_length = 0
                    fence_signature = ()
                else:
                    if preserves_list_container:
                        visible.append((normalized.line, ""))
                    continue
            if fenced_content is None:
                fence_character = None
                fence_length = 0
                fence_signature = ()
            else:
                closing_pattern = re.compile(
                    rf"^[ ]{{0,3}}{re.escape(open_fence_character)}"
                    rf"{{{fence_length},}}[ \t]*$"
                )
                if closing_pattern.match(fenced_content):
                    fence_character = None
                    fence_length = 0
                    fence_signature = ()
                if preserves_list_container:
                    visible.append((normalized.line, ""))
                continue

        line = normalized.content
        opening = None if normalized.is_indented_code else _fence_opening(line)
        if opening is not None:
            fence = opening.group(1)
            fence_character = fence[0]
            fence_length = len(fence)
            fence_signature = normalized.signature
            if any(container == "list" for container, _ in fence_signature):
                visible.append(
                    (normalized.line, _container_placeholder(fence_signature))
                )
            continue
        visible.append((normalized.line, normalized.raw))

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
    normalized_lines = _container_lines(lines)
    table_rows = _structural_table_row_lines(lines)
    at_block_start = True
    previous_line = 0
    for normalized in normalized_lines:
        if normalized.line != previous_line + 1:
            at_block_start = True
        previous_line = normalized.line
        if not normalized.content.strip():
            at_block_start = True
            continue
        if normalized.line in table_rows or normalized.is_indented_code:
            at_block_start = True
            continue
        if _is_block_interrupt(normalized.content):
            at_block_start = True
            continue
        if normalized.starts_list_item:
            at_block_start = True
        match = definition_pattern.match(normalized.content) if at_block_start else None
        if match is None:
            at_block_start = False
            continue
        label = _reference_label(match.group(1))
        definitions.setdefault(label, match.group(2) or match.group(3))
        definition_lines.add(normalized.line)
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


def _list_item_content(line: str) -> _ListItemContent | None:
    """Return a list item's content and its visual continuation indent."""
    match = re.match(
        r"^( {0,3})([-+*]|[0-9]{1,9}[.)])([ \t]+)(.*)$",
        line,
    )
    if match is None:
        return None

    marker_prefix = "".join(match.group(index) for index in range(1, 4))
    marker = "".join(match.group(index) for index in range(1, 3))
    content_indent = len(marker_prefix.expandtabs(4))
    marker_width = len(marker.expandtabs(4))
    padding = content_indent - marker_width
    ordered_start = int(match.group(2)[:-1]) if match.group(2)[0].isdigit() else None
    if 1 <= padding <= 4:
        return _ListItemContent(
            content_indent,
            match.group(4),
            ordered_start,
            False,
        )
    return _ListItemContent(
        marker_width + 1,
        match.group(4),
        ordered_start,
        bool(match.group(4)),
    )


def _strip_visual_indent(line: str, required_columns: int) -> str | None:
    """Strip at least the requested leading indentation columns."""
    columns = 0
    index = 0
    while index < len(line) and columns < required_columns:
        character = line[index]
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 4 - (columns % 4)
        else:
            return None
        index += 1
    if columns < required_columns:
        return None
    return (" " * (columns - required_columns)) + line[index:]


def _container_placeholder(signature: Sequence[tuple[str, int]]) -> str:
    """Build link-free source that establishes the same list containers."""
    placeholder: list[str] = []
    for container, width in signature:
        if container == "quote":
            placeholder.append("> ")
        else:
            placeholder.append("-" + (" " * max(width - 1, 1)))
    return "".join(placeholder)


def _strip_container_signature(
    line: str, signature: Sequence[tuple[str, int]]
) -> str | None:
    """Strip exactly the quote/list containers required by a fenced block."""
    content = line
    blockquote_marker = re.compile(r"^[ ]{0,3}>[ \t]?")
    for container, width in signature:
        if container == "list":
            content = _strip_visual_indent(content, width)
            if content is None:
                return None
        else:
            quote = blockquote_marker.match(content)
            if quote is None:
                return None
            content = content[quote.end() :]
    return content


def _is_indented_code(line: str) -> bool:
    """Return whether leading whitespace reaches four visual columns."""
    return _strip_visual_indent(line, 4) is not None


def _fence_opening(line: str) -> re.Match[str] | None:
    """Return a valid fenced-code opener in normalized container content."""
    opening = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", line)
    if (
        opening is not None
        and opening.group(1)[0] == "`"
        and "`" in line[opening.end() :]
    ):
        return None
    return opening


def _is_block_interrupt(line: str) -> bool:
    """Return whether normalized content starts a paragraph-interrupting block."""
    return bool(
        re.match(r"^[ ]{0,3}#{1,6}(?:[ \t]+|$)", line)
        or re.fullmatch(
            r"[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})",
            line,
        )
        or re.fullmatch(r"[ ]{0,3}(?:=+|-+)[ \t]*", line)
        or _fence_opening(line) is not None
    )


def _is_container_subsequence(
    candidate: Sequence[tuple[str, int]],
    original: Sequence[tuple[str, int]],
) -> bool:
    """Return whether explicit retained containers preserve their prior order."""
    original_index = 0
    for container in candidate:
        while original_index < len(original) and original[original_index] != container:
            original_index += 1
        if original_index == len(original):
            return False
        original_index += 1
    return True


def _container_lines(
    lines: Sequence[tuple[int, str]],
) -> tuple[_ContainerLine, ...]:
    """Normalize explicit quote/list containers while retaining physical lines."""
    normalized: list[_ContainerLine] = []
    active_list_indents: list[int] = []
    previous_line = 0
    previous_was_blank = False
    paragraph_open = False
    blockquote_marker = re.compile(r"^[ ]{0,3}>[ \t]?")

    for line_number, raw in lines:
        if line_number != previous_line + 1:
            active_list_indents = []
            previous_was_blank = True
            paragraph_open = False
        previous_line = line_number
        if not raw.strip():
            normalized.append(_ContainerLine(line_number, raw, "", (), False))
            previous_was_blank = True
            paragraph_open = False
            continue

        content = raw
        signature: list[tuple[str, int]] = []
        consumed_lists = 0
        ordered_start: int | None = None
        marker_code = False
        starts_list_item = False

        while True:
            if consumed_lists < len(active_list_indents):
                required_indent = active_list_indents[consumed_lists]
                stripped = _strip_visual_indent(content, required_indent)
                if stripped is not None:
                    content = stripped
                    signature.append(("list", required_indent))
                    consumed_lists += 1
                    continue

            quote = blockquote_marker.match(content)
            if quote is not None:
                content = content[quote.end() :]
                signature.append(("quote", 1))
                continue

            list_item = _list_item_content(content)
            if list_item is not None:
                if (
                    list_item.ordered_start not in {None, 1}
                    and paragraph_open
                    and consumed_lists == len(active_list_indents)
                ):
                    break
                active_list_indents = [
                    *active_list_indents[:consumed_lists],
                    list_item.content_indent,
                ]
                content = list_item.content
                signature.append(("list", list_item.content_indent))
                consumed_lists += 1
                ordered_start = list_item.ordered_start
                marker_code = marker_code or list_item.is_indented_code
                starts_list_item = True
                continue
            break

        is_indented_code = marker_code or _is_indented_code(content)
        is_block_interrupt = _is_block_interrupt(content)
        preserves_lazy_list = (
            not previous_was_blank
            and paragraph_open
            and not is_block_interrupt
            and not is_indented_code
            and not starts_list_item
            and consumed_lists < len(active_list_indents)
            and all(container == "list" for container, _ in signature)
        )
        if not preserves_lazy_list:
            active_list_indents = active_list_indents[:consumed_lists]
        normalized.append(
            _ContainerLine(
                line_number,
                raw,
                content,
                tuple(signature),
                is_indented_code,
                starts_list_item,
                ordered_start,
            )
        )
        previous_was_blank = False
        paragraph_open = bool(content.strip()) and not (
            is_indented_code or is_block_interrupt
        )

    return tuple(normalized)


def _structural_table_row_lines(
    lines: Sequence[tuple[int, str]],
) -> frozenset[int]:
    table_rows: set[int] = set()
    normalized_lines = _container_lines(lines)
    index = 0

    while index + 1 < len(normalized_lines):
        header_line = normalized_lines[index]
        delimiter_line = normalized_lines[index + 1]
        header = markdown_table_cells(header_line.content)
        delimiter = markdown_table_cells(delimiter_line.content)
        if (
            delimiter_line.line != header_line.line + 1
            or header_line.is_indented_code
            or delimiter_line.is_indented_code
            or header_line.signature != delimiter_line.signature
            or header is None
            or delimiter is None
            or len(header) != len(delimiter)
            or not is_table_separator(delimiter)
        ):
            index += 1
            continue

        table_rows.update((header_line.line, delimiter_line.line))
        previous_line = delimiter_line.line
        row_index = index + 2
        while row_index < len(normalized_lines):
            row = normalized_lines[row_index]
            if row.line != previous_line + 1:
                break
            if (
                row.is_indented_code
                or row.signature != header_line.signature
                or not row.content.strip()
                or _is_block_interrupt(row.content)
            ):
                break
            table_rows.add(row.line)
            previous_line = row.line
            row_index += 1
        index = row_index

    return frozenset(table_rows)


def _inline_markdown_blocks(
    lines: Sequence[tuple[int, str]],
) -> Iterator[tuple[int, str]]:
    block: list[str] = []
    block_start = 0
    block_signature: tuple[tuple[str, int], ...] = ()
    previous_line = 0
    atx_heading = re.compile(r"^[ ]{0,3}#{1,6}(?:[ \t]+|$)")
    thematic_break = re.compile(
        r"^[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
    )
    setext_underline = re.compile(r"^[ ]{0,3}(?:=+|-+)[ \t]*$")
    normalized_lines = _container_lines(lines)
    table_row_lines = _structural_table_row_lines(lines)

    def finish_block() -> tuple[int, str] | None:
        nonlocal block, block_signature
        if not block:
            return None
        finished = (block_start, "\n".join(block))
        block = []
        block_signature = ()
        return finished

    for normalized in normalized_lines:
        line_number = normalized.line
        line = normalized.raw
        if line_number != previous_line + 1 or not line.strip():
            if (finished := finish_block()) is not None:
                yield finished
            if not line.strip():
                previous_line = line_number
                continue

        content = normalized.content
        if not content.strip():
            if (finished := finish_block()) is not None:
                yield finished
            previous_line = line_number
            continue

        is_heading = atx_heading.match(content) is not None
        is_thematic_break = thematic_break.fullmatch(content) is not None
        is_setext_underline = setext_underline.fullmatch(content) is not None
        list_interrupts = normalized.starts_list_item
        is_edge_pipe_row = content.lstrip().startswith(
            "|"
        ) or content.rstrip().endswith("|")
        starts_block = (
            is_heading
            or is_thematic_break
            or is_setext_underline
            or list_interrupts
            or (is_edge_pipe_row and line_number not in table_row_lines)
        )
        continues_container = _is_container_subsequence(
            normalized.signature, block_signature
        )
        if block and continues_container and not starts_block:
            block.append(line)
            previous_line = line_number
            continue

        if (finished := finish_block()) is not None:
            yield finished

        if line_number in table_row_lines:
            yield line_number, line
            previous_line = line_number
            continue

        if normalized.is_indented_code:
            previous_line = line_number
            continue

        if is_edge_pipe_row:
            yield line_number, line
            previous_line = line_number
            continue

        if is_thematic_break or is_setext_underline:
            previous_line = line_number
            continue

        if is_heading:
            yield line_number, line
            previous_line = line_number
            continue

        block_start = line_number
        block_signature = normalized.signature
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
