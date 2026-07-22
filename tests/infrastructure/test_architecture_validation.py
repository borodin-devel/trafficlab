"""Tests for deterministic architecture-corpus validation."""

import os
from pathlib import Path, PurePosixPath

import pytest
import tools.validate_architecture as architecture_validation
from tools.validate_architecture import (
    ValidationIssue,
    corpus_from_mapping,
    load_corpus,
    main,
    validate,
    validate_identifiers,
    validate_links,
    validate_naming,
    validate_registries,
    validate_roadmap_links,
    validate_srs_structure,
)


@pytest.mark.unit
def test_broken_relative_link_is_rejected() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "# Architecture\n\n[Missing](MISSING.md)\n"}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_soft_wrapped_inline_link_is_validated_at_its_start_line() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "# Architecture\n\n"
                "For every method, [missing owner\n"
                "documentation](MISSING.md) must also be read.\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "Paragraph [open label\n# close label](MISSING.md)\n",
        "Paragraph [open label\n> close label](MISSING.md)\n",
        "Paragraph [open label\n***\nclose label](MISSING.md)\n",
        "    [indented code\n    is not a link](MISSING.md)\n",
        "> [open label\n> > close label](MISSING.md)\n",
        "10. [open label\n    - close label](MISSING.md)\n",
        "10. [open label\n    # close label](MISSING.md)\n",
        "10. [open label\n    > close label](MISSING.md)\n",
        "10. [open label\n    ***\n    close label](MISSING.md)\n",
        "1. [open owner\n2. close](MISSING.md)\n",
        "> 1. [open owner\n> 2. close](MISSING.md)\n",
        "- 1. [open owner\n  2. close](MISSING.md)\n",
        "[open label\n===\nclose](MISSING.md)\n",
        "> [open label\n>\n> close](MISSING.md)\n",
        "- > [open owner\n> close](MISSING.md)\n",
        "- - > [open owner\n> close](MISSING.md)\n",
    ],
)
def test_inline_links_do_not_cross_markdown_block_boundaries(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("# [Missing](MISSING.md)\n", 1),
        ("> [Missing](MISSING.md)\n", 1),
        ("| Owner | [Missing](MISSING.md) |\n", 1),
        ("> [Missing owner\n> document](MISSING.md)\n", 1),
        ("- [Missing owner\n  document](MISSING.md)\n", 1),
        ("10. [Missing owner\n    document](MISSING.md)\n", 1),
        ("> - [Missing owner\n>   document](MISSING.md)\n", 1),
        ("> 10. [Missing owner\n>     document](MISSING.md)\n", 1),
        ("> > [Missing owner\n> document](MISSING.md)\n", 1),
        ("[Missing owner\n2. document](MISSING.md)\n", 1),
        (
            "[Missing owner\n2. outer\n   2. inner\n   continuation](MISSING.md)\n",
            1,
        ),
        ("[Missing owner\n2. fake\n      # close](MISSING.md)\n", 1),
        ("> - [Missing owner\n  document](MISSING.md)\n", 1),
        ("Paragraph [open label\n| close label](MISSING.md) |\n", 1),
    ],
)
def test_inline_capable_markdown_blocks_are_parsed_separately(
    text: str, line: int
) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            line,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_lazy_blockquote_continuation_preserves_wrapped_link() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": ("> [missing owner\ncontinuation](MISSING.md)\n")}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            1,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_no_edge_pipe_table_rows_do_not_fuse_link_syntax() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "Name [open label | Owner\n--- | ---\nclose label](MISSING.md) | Demo\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_no_edge_pipe_table_link_is_validated_within_its_row() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "Name | Owner\n--- | ---\ndemo | [Missing](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_pipe_less_table_body_rows_do_not_fuse_link_syntax() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "Name | Owner\n--- | ---\n[open label\nclose](MISSING.md) | Demo\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_setext_shaped_table_body_rows_do_not_fuse_link_syntax() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "Name | Owner\n--- | ---\n===\n[open label\nclose](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_html_block_terminates_table_body() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "Name | Owner\n"
                "--- | ---\n"
                "cell\n"
                "<!-- boundary -->\n"
                "[Missing owner\n"
                "continuation](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            5,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_pipe_less_table_body_link_is_validated() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": ("Name | Owner\n--- | ---\n[Missing](MISSING.md)\n")}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "line"),
    [
        (
            (
                "Name | Owner\n"
                "--- | ---\n"
                "2. [Missing owner\n"
                "   continuation](MISSING.md)\n"
            ),
            3,
        ),
        (
            (
                "Name | Owner\n"
                "--- | ---\n"
                "cell\n"
                "2. [Missing owner\n"
                "   continuation](MISSING.md)\n"
            ),
            4,
        ),
        (
            (
                "Name | Owner\n"
                "--- | ---\n"
                "demo | Owner\n"
                "2. [Missing owner\n"
                "   continuation](MISSING.md)\n"
            ),
            4,
        ),
    ],
)
def test_non_one_ordered_list_after_table_is_validated(text: str, line: int) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            line,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize("container", ["> ", "> > "])
def test_blockquote_table_rows_do_not_fuse_link_syntax(container: str) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                f"{container}Name [open | Owner\n"
                f"{container}--- | ---\n"
                f"{container}close](MISSING.md) | Demo\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        (
            "- outer\n"
            "  - Name [open | Owner\n"
            "    --- | ---\n"
            "    close](MISSING.md) | Demo\n"
        ),
        ("10. Name [open | Owner\n    --- | ---\n    close](MISSING.md) | Demo\n"),
        ("-   Name [open | Owner\n    --- | ---\n    close](MISSING.md) | Demo\n"),
        (
            "- outer\n"
            "  - inner\n"
            "    - Name [open | Owner\n"
            "      --- | ---\n"
            "      close](MISSING.md) | Demo\n"
        ),
        (
            "- > Name | Owner\n"
            "  > --- | ---\n"
            "  > [open | Demo\n"
            "  > close](MISSING.md) | Demo\n"
        ),
    ],
)
def test_list_table_rows_do_not_fuse_link_syntax(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("container", "indentation"),
    [
        ("", "    "),
        ("", "\t"),
        ("", " \t"),
        ("> ", "    "),
        ("> > ", "    "),
        ("> > ", "\t"),
        ("> > ", " \t"),
    ],
)
def test_indented_code_shaped_like_table_is_not_parsed(
    container: str, indentation: str
) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                f"{container}{indentation}Name | Owner\n"
                f"{container}{indentation}--- | ---\n"
                f"{container}{indentation}demo | [Missing](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("container", "indentation"),
    [
        ("> ", "    "),
        ("> > ", "    "),
        ("> > ", "\t"),
        ("> > ", " \t"),
    ],
)
def test_quoted_indented_edge_pipe_table_is_not_parsed(
    container: str, indentation: str
) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                f"{container}{indentation}| Name | Owner |\n"
                f"{container}{indentation}| --- | --- |\n"
                f"{container}{indentation}| demo | [Missing](MISSING.md) |\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize("indentation", ["\t", " \t"])
def test_absolute_tab_stops_allow_single_quoted_table(indentation: str) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                f"> {indentation}Name | Owner\n"
                f"> {indentation}--- | ---\n"
                f"> {indentation}demo | [Missing](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "line"),
    [
        (
            "- outer\n"
            "  - Name | Owner\n"
            "    --- | ---\n"
            "    demo | [Missing](MISSING.md)\n",
            4,
        ),
        (
            "10. Name | Owner\n    --- | ---\n    demo | [Missing](MISSING.md)\n",
            3,
        ),
        (
            "10. Item\n\n"
            "    Name | Owner\n"
            "    --- | ---\n"
            "    demo | [Missing](MISSING.md)\n",
            5,
        ),
        (
            "- outer\n"
            "  - inner\n"
            "    - Name | Owner\n"
            "      --- | ---\n"
            "      demo | [Missing](MISSING.md)\n",
            5,
        ),
        (
            "- > Name | Owner\n  > --- | ---\n  > demo | [Missing](MISSING.md)\n",
            3,
        ),
        (
            "-   intro\n"
            "lazy continuation\n\n"
            "    Name | Owner\n"
            "    --- | ---\n"
            "    demo | [Missing](MISSING.md)\n",
            6,
        ),
    ],
)
def test_list_table_links_are_validated(text: str, line: int) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            line,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ["-     ", "1.      ", "> -     "])
def test_list_marker_indented_code_is_not_parsed(prefix: str) -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": f"{prefix}| [Missing](MISSING.md) |\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_list_table_indented_code_row_is_not_parsed() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "> 10. Name | Owner\n"
                ">     --- | ---\n"
                ">         code | [Missing](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "- item\n# outside\n\n    [Missing](MISSING.md)\n",
        "- item\n***\n\n    [Missing](MISSING.md)\n",
        "paragraph\n2. fake\n\n    [Missing](MISSING.md)\n",
        (
            "- item\n"
            "```\ncode\n```\n"
            "    Name | Owner\n"
            "    --- | ---\n"
            "    demo | [Missing](MISSING.md)\n"
        ),
    ],
)
def test_closed_container_does_not_expose_indented_code(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "> [open label\nName | Owner\n--- | ---\nclose](MISSING.md) | Demo\n",
        "- [open label\nName | Owner\n--- | ---\nclose](MISSING.md) | Demo\n",
        (
            "> [open label\n"
            "| Name | Owner |\n"
            "| --- | --- |\n"
            "| close](MISSING.md) | Demo |\n"
        ),
        ("> [Missing owner\nName | Owner\n--- | ---\n2. continuation](MISSING.md)\n"),
    ],
)
def test_lazy_paragraph_takes_precedence_over_table_shape(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            1,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        (
            "[Missing owner\n"
            "2. Name | Owner\n"
            "   --- | ---\n"
            "   close](MISSING.md) | Demo\n"
        ),
        "[Missing owner\nName | Owner\n--- | ---\n2. continuation](MISSING.md)\n",
    ],
)
def test_table_can_interrupt_an_uncontained_paragraph(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_missing_heading_fragment_is_rejected() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "# Architecture\n\n[Rule](RULES.md#missing)\n",
            "architecture/RULES.md": "# Rules\n\n## Present\n",
        }
    )

    assert validate_links(corpus)[0].code == "LNK002"


@pytest.mark.unit
def test_duplicate_requirement_identifier_is_rejected() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/one/SRS.md": "- **ONE-FR-001:** First.\n",
            "architecture/two/SRS.md": "- **ONE-FR-001:** Duplicate.\n",
        }
    )

    issues = validate_identifiers(corpus)

    assert len(issues) == 2
    assert {issue.code for issue in issues} == {"SRS001"}


@pytest.mark.unit
def test_issue_order_is_path_line_code_order() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/Z.md": "[Missing](NO.md)\n",
            "architecture/A.md": "[Missing](NO.md)\n",
        }
    )

    assert tuple(issue.path for issue in validate(corpus)) == (
        PurePosixPath("architecture/A.md"),
        PurePosixPath("architecture/Z.md"),
    )


@pytest.mark.unit
def test_issue_render_is_stable() -> None:
    issue = ValidationIssue(
        PurePosixPath("architecture/README.md"),
        7,
        "LNK003",
        "local target is unsafe: ../../outside.md",
    )

    assert (
        issue.render()
        == "architecture/README.md:7: [LNK003] local target is unsafe: ../../outside.md"
    )


@pytest.mark.unit
def test_issue_render_escapes_controls_and_preserves_printable_unicode() -> None:
    issue = ValidationIssue(
        PurePosixPath("architecture/BAD\nNAME.md"),
        7,
        "LNK003",
        "unsafe\rmessage\twith\x1b controls and café",
    )

    rendered = issue.render()

    assert rendered == (
        r"architecture/BAD\nNAME.md:7: [LNK003] "
        r"unsafe\rmessage\twith\x1b controls and café"
    )
    assert rendered.splitlines() == [rendered]


@pytest.mark.unit
def test_mapping_paths_are_normalized_and_sorted() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/z/../Z.md": "# Z\n",
            "architecture/./A.md": "# A\n",
        }
    )

    assert tuple(source.path for source in corpus.markdown_files) == (
        PurePosixPath("architecture/A.md"),
        PurePosixPath("architecture/Z.md"),
    )
    assert corpus.existing_paths == frozenset(
        {
            PurePosixPath("architecture/A.md"),
            PurePosixPath("architecture/Z.md"),
        }
    )
    assert {
        PurePosixPath("."),
        PurePosixPath("architecture"),
    } <= corpus.directories


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/architecture/README.md", "../README.md"])
def test_mapping_rejects_absolute_and_escaping_paths(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        corpus_from_mapping({path: "# Unsafe\n"})


@pytest.mark.unit
def test_loader_reads_only_regular_architecture_markdown_without_symlinks(
    tmp_path: Path,
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    (architecture_root / "README.md").write_text("# Architecture\n", encoding="utf-8")
    (architecture_root / "NOTES.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"asset")
    cached = tmp_path / ".pytest_cache"
    cached.mkdir()
    (cached / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (architecture_root / "LINK.md").symlink_to(outside)

    corpus = load_corpus(tmp_path, architecture_root)

    assert corpus.architecture_root == PurePosixPath("architecture")
    assert tuple(source.path for source in corpus.markdown_files) == (
        PurePosixPath("architecture/README.md"),
    )
    assert PurePosixPath("asset.bin") in corpus.existing_paths
    assert PurePosixPath("architecture/NOTES.txt") in corpus.existing_paths
    assert PurePosixPath("architecture/LINK.md") not in corpus.existing_paths
    assert PurePosixPath(".pytest_cache/ignored.md") not in corpus.existing_paths


@pytest.mark.unit
def test_loader_records_regular_file_named_like_pruned_directory(
    tmp_path: Path,
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    (architecture_root / "README.md").write_text("# Architecture\n", encoding="utf-8")
    (tmp_path / "build").write_text("regular file\n", encoding="utf-8")

    corpus = load_corpus(tmp_path, architecture_root)

    assert PurePosixPath("build") in corpus.existing_paths


@pytest.mark.unit
def test_loader_rejects_architecture_root_outside_repository(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()

    with pytest.raises(ValueError, match="inside repository root"):
        load_corpus(repository_root, architecture_root)


@pytest.mark.unit
def test_loader_rejects_symlinked_architecture_root(tmp_path: Path) -> None:
    real_architecture = tmp_path / "real_architecture"
    real_architecture.mkdir()
    architecture_root = tmp_path / "architecture"
    architecture_root.symlink_to(real_architecture, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_corpus(tmp_path, architecture_root)


@pytest.mark.unit
def test_loader_rejects_symlinked_repository_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    repository_root = real_parent / "repository"
    architecture_root = repository_root / "architecture"
    architecture_root.mkdir(parents=True)
    (architecture_root / "README.md").write_text("# Architecture\n", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        load_corpus(alias / "repository", alias / "repository" / "architecture")


@pytest.mark.unit
def test_loader_rejects_directory_identity_change_and_closes_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    architecture_root = tmp_path / "architecture"
    owners = architecture_root / "owners"
    owners.mkdir(parents=True)
    (owners / "README.md").write_text("# Owner\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "README.md").write_text("# Replacement\n", encoding="utf-8")
    real_open = os.open
    opened_descriptors: list[int] = []

    def swapped_directory_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == "owners":
            assert dir_fd is not None
            assert flags & os.O_DIRECTORY
            assert flags & os.O_NOFOLLOW
            descriptor = real_open(replacement, flags, mode)
            opened_descriptors.append(descriptor)
            return descriptor
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(architecture_validation.os, "open", swapped_directory_open)

    with pytest.raises(OSError, match="changed while being loaded"):
        load_corpus(tmp_path, architecture_root)

    assert len(opened_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(opened_descriptors[0])


@pytest.mark.unit
def test_loader_rejects_descriptor_identity_change_and_uses_no_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    source = architecture_root / "README.md"
    source.write_text("# Architecture\n", encoding="utf-8")
    replacement = tmp_path / "REPLACEMENT.md"
    replacement.write_text("# Replacement\n", encoding="utf-8")
    real_open = os.open
    opened_descriptors: list[int] = []

    def swapped_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == source.name:
            assert dir_fd is not None
            assert flags & os.O_NOFOLLOW
            descriptor = real_open(replacement, flags, mode)
            opened_descriptors.append(descriptor)
            return descriptor
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(architecture_validation.os, "open", swapped_open)

    with pytest.raises(OSError, match="changed while being loaded"):
        load_corpus(tmp_path, architecture_root)

    assert len(opened_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(opened_descriptors[0])


@pytest.mark.unit
def test_loader_fifo_swap_uses_nonblocking_no_follow_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    source = architecture_root / "README.md"
    source.write_text("# Architecture\n", encoding="utf-8")
    replacement_fifo = tmp_path / "REPLACEMENT_FIFO"
    os.mkfifo(replacement_fifo)
    real_open = os.open
    opened_descriptors: list[int] = []

    def fifo_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == source.name:
            assert dir_fd is not None
            assert flags & os.O_NOFOLLOW
            assert flags & os.O_NONBLOCK
            descriptor = real_open(replacement_fifo, flags, mode)
            opened_descriptors.append(descriptor)
            return descriptor
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(architecture_validation.os, "open", fifo_open)

    with pytest.raises(OSError, match="changed while being loaded"):
        load_corpus(tmp_path, architecture_root)

    assert len(opened_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(opened_descriptors[0])


@pytest.mark.unit
def test_fenced_links_are_ignored_and_reference_links_keep_use_line() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "```markdown\n"
                "[Ignored](IGNORED.md)\n"
                "```\n"
                "\n"
                "[Missing][rule]\n"
                "\n"
                "[rule]: MISSING.md\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            5,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_inline_code_span_link_is_ignored() -> None:
    corpus = corpus_from_mapping({"architecture/README.md": "`[demo](MISSING.md)`\n"})

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_odd_backslash_escaped_code_span_opening_does_not_hide_link() -> None:
    corpus = corpus_from_mapping({"architecture/README.md": "\\`[demo](MISSING.md)`\n"})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            1,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_escaped_first_backtick_leaves_remaining_run_as_code_span_opener() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "\\`` [demo](MISSING.md) `\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_even_backslashes_allow_code_span_opening() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "\\\\`[demo](MISSING.md)`\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_backslash_inside_code_span_does_not_escape_exact_closing_run() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "`[ignored](IGNORED.md)\\` [missing](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            1,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_unmatched_double_backticks_allow_later_single_code_span() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": ("`` unmatched ` [demo](MISSING.md) `\n")}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "``` unmatched `` [demo](MISSING.md) ``\n",
        "```` unmatched ``` noise ` [demo](MISSING.md) `\n",
    ],
)
def test_mismatched_code_runs_preserve_later_valid_span(source: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": source})

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_html_comment_block_link_is_ignored() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "<!-- [demo](MISSING.md) -->\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["<x-demo>", "</x-demo>"])
def test_complete_generic_type_seven_html_block_link_is_ignored(tag: str) -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": (f"{tag}\n[demo](MISSING.md)\n")}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_malformed_generic_html_tag_is_ordinary_prose() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "<x =>\n[demo](MISSING.md)\n"}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            2,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "tag",
    [
        "<x disabled>",
        "<x data-id=value>",
        "<x data-id = 'quoted value'>",
        '<x :scope.name="quoted value" />',
        "<x/>",
        "</x >",
    ],
)
def test_complete_commonmark_generic_tags_start_type_seven_blocks(tag: str) -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": f"{tag}\n[demo](MISSING.md)\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_type_seven_html_block_cannot_interrupt_paragraph() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": ("paragraph\n<x-demo>\n[demo](MISSING.md)\n")}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_type_seven_html_block_ends_at_container_boundary() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "> <x-demo>\n> [ignored](IGNORED.md)\n[missing](MISSING.md)\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_escaped_reference_link_use_is_ignored() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": ("[id]: MISSING.md\n\n\\[demo][id]\n")}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_container_reference_definition_is_document_global() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": ("> [owner]: MISSING.md\n\n[Owner][owner]\n")}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("paragraph\n> [id]: MISSING.md\n[Use][id]\n", 3),
        ("> paragraph\n> > [id]: MISSING.md\n> [Use][id]\n", 3),
    ],
)
def test_new_quote_container_starts_a_reference_definition(
    text: str, line: int
) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            line,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "[id]: <[open>\nclose](MISSING.md)\n",
        "> [id]: <[open>\n> close](MISSING.md)\n",
        "Name | Owner\n--- | ---\n[id]: <[open>\nclose](MISSING.md)\n",
    ],
)
def test_reference_definition_line_is_an_inline_block_boundary(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_reference_definition_rejects_trailing_garbage() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "[id]: MISSING.md garbage\n[Use][id]\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_reference_definition_destination_may_start_on_the_next_line() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "[id]:\n  MISSING.md\n\n[Use][id]\n"}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            4,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_reference_definition_destination_unescapes_punctuation() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "[id]: RULES\\.md\n[Use][id]\n",
            "architecture/RULES.md": "# Rules\n",
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "definition",
    [
        '[id]: MISSING.md "title"',
        "[id]: MISSING.md 'title'",
        "[id]: MISSING.md (title)",
        '[id]: <MISSING.md> "title"',
    ],
)
def test_reference_definition_accepts_same_line_title(definition: str) -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": f"{definition}\n[Use][id]\n"}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            2,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_reference_definition_title_lines_are_not_inline_blocks() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                '[id]: MISSING.md\n  "title [open\n  close](IGNORED.md)"\n\n[Use][id]\n'
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            5,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_multiline_reference_link_label_is_normalized() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "[multi label]: MISSING.md\n\n[Use][multi\nlabel]\n"
            )
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "[id]: MISSING.md | Owner\n--- | ---\n[Use][id] | Demo\n",
        "paragraph\n[id]: MISSING.md\n\n[Use][id]\n",
    ],
)
def test_reference_definition_is_not_parsed_inside_another_block(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "> ```markdown\n> [Ignored](IGNORED.md)\n> ```\n",
        "- ```markdown\n  [Ignored](IGNORED.md)\n  ```\n",
        "- > ```markdown\n  > [Ignored](IGNORED.md)\n  > ```\n",
        "```\n> ```\n> [Ignored](IGNORED.md)\n```\n",
        "~~~ bad`info\n[Ignored](IGNORED.md)\n~~~\n",
        "- outer\n  - ```\n\t[Ignored](IGNORED.md)\n\t```\n",
        "> - ```\n>\t[Ignored](IGNORED.md)\n>\t```\n",
    ],
)
def test_container_fenced_links_are_ignored(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "> ```markdown\n[Missing](MISSING.md)\n",
        "> > ```markdown\n> [Missing](MISSING.md)\n",
        "- ```markdown\n- [Missing](MISSING.md)\n",
        "-     ```markdown\n[Missing](MISSING.md)\n",
        "``` bad`info\n[Missing](MISSING.md)\n",
        ">  - ```\n> \t[Missing](MISSING.md)\n> \t```\n",
    ],
)
def test_link_after_container_fence_ends_is_validated(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            2,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "> ```markdown\n\n> [Missing](MISSING.md)\n",
        "> > ```markdown\n\n> > [Missing](MISSING.md)\n",
        "- > ```markdown\n\n  > [Missing](MISSING.md)\n",
    ],
)
def test_unmarked_blank_ends_quoted_fence(text: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": text})

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_external_links_are_ignored() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "[HTTPS](https://example.invalid/missing)\n"
                "[Mail](mailto:maintainer@example.invalid)\n"
                "[Network](//example.invalid/missing)\n"
            )
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_plain_text_that_resembles_a_destination_is_not_a_link() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "This is not a label](MISSING.md).\n"}
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "[closed] prose](MISSING.md)\n",
        "\\[escaped](MISSING.md)\n",
        "[escaped\\](MISSING.md)\n",
    ],
)
def test_unmatched_or_escaped_brackets_do_not_form_inline_links(line: str) -> None:
    corpus = corpus_from_mapping({"architecture/README.md": line})

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_absolute_and_escaping_link_paths_are_rejected() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/nested/README.md": (
                "[Absolute](/etc/passwd)\n[Escape](../../../outside.md)\n"
            )
        }
    )

    assert tuple((issue.line, issue.code) for issue in validate_links(corpus)) == (
        (1, "LNK003"),
        (2, "LNK003"),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "destination",
    [
        "BAD%0ANAME.md",
        "README.md#bad%0Afragment",
    ],
)
def test_percent_decoded_link_controls_are_rejected_as_unsafe(
    destination: str,
) -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": (f"# Architecture\n\n[Unsafe]({destination})\n")}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK003",
            f"local target is unsafe: {destination}",
        ),
    )


@pytest.mark.unit
def test_percent_decoded_paths_and_duplicate_heading_anchors_resolve() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": (
                "[First](R%C3%89SUM%C3%89.md#café-rule)\n"
                "[Second](R%C3%89SUM%C3%89.md#café-rule-1)\n"
            ),
            "architecture/RÉSUMÉ.md": "# **Café** Rule\n\n# Café Rule\n",
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "heading",
    [
        "# [Owner](README.md)",
        "# `Owner`",
        "> # Owner",
        "- # Owner",
    ],
)
def test_heading_anchor_uses_rendered_text_in_normalized_containers(
    heading: str,
) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/INDEX.md": "[Owner](README.md#owner)\n",
            "architecture/README.md": f"{heading}\n",
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_heading_anchor_rejects_inline_link_source_text_slug() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/INDEX.md": ("[Synthetic](README.md#ownerreadmemd)\n"),
            "architecture/README.md": "# [Owner](README.md)\n",
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/INDEX.md"),
            1,
            "LNK002",
            "heading fragment does not exist: architecture/README.md#ownerreadmemd",
        ),
    )


@pytest.mark.unit
def test_setext_heading_anchor_resolves() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "[Rules](RULES.md#setext-rules)\n",
            "architecture/RULES.md": "Setext **Rules**\n----------------\n",
        }
    )

    assert validate_links(corpus) == ()


@pytest.mark.unit
def test_setext_heading_requires_physically_adjacent_underline() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "[Rules](RULES.md#separated)\n",
            "architecture/RULES.md": (
                "Separated\n```markdown\n# Fenced\n```\n----------------\n"
            ),
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            1,
            "LNK002",
            "heading fragment does not exist: architecture/RULES.md#separated",
        ),
    )


@pytest.mark.unit
def test_missing_fragment_reports_target_and_decoded_fragment() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "[Missing](RULES.md#caf%C3%A9)\n",
            "architecture/RULES.md": "# Present\n",
        }
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            1,
            "LNK002",
            "heading fragment does not exist: architecture/RULES.md#café",
        ),
    )


@pytest.mark.unit
def test_every_duplicate_identifier_declaration_is_reported() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/one/SRS.md": (
                "- **ONE-FR-001:** First.\n"
                "```markdown\n"
                "- **ONE-FR-001:** Example only.\n"
                "```\n"
                "- **TWO-001:** Too short.\n"
            ),
            "architecture/two/SRS.md": (
                "- **ONE-FR-001:** Second.\n- **ONE-FR-001:** Third.\n"
            ),
            "architecture/two/README.md": "**ONE-FR-001:** Not an SRS.\n",
        }
    )

    issues = validate_identifiers(corpus)

    assert tuple((issue.path, issue.line, issue.message) for issue in issues) == (
        (
            PurePosixPath("architecture/one/SRS.md"),
            1,
            "duplicate requirement identifier: ONE-FR-001",
        ),
        (
            PurePosixPath("architecture/two/SRS.md"),
            1,
            "duplicate requirement identifier: ONE-FR-001",
        ),
        (
            PurePosixPath("architecture/two/SRS.md"),
            2,
            "duplicate requirement identifier: ONE-FR-001",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("files", "expected_code"),
    [
        ({"architecture/bad-name.md": "# Bad\n"}, "NAM001"),
        ({"architecture/apps/demo/README.md": "# Demo\n"}, "DOC001"),
        (
            {
                "architecture/demo/SRS.md": (
                    "# SRS\n\n## Requirements\n\n- **DEM-FR-001:** Rule.\n"
                )
            },
            "SRS002",
        ),
        (
            {
                "architecture/demo/ROADMAP.md": (
                    "# Roadmap\n\n## [100%] STAGE 1 — Invalid\n"
                )
            },
            "RDM001",
        ),
        ({"architecture/demo/NOTE.md": "# Note \n"}, "HYG001"),
    ],
)
def test_structural_defect_is_rejected(
    files: dict[str, str], expected_code: str
) -> None:
    assert expected_code in {
        issue.code for issue in validate(corpus_from_mapping(files))
    }


@pytest.mark.unit
def test_naming_checks_every_regular_architecture_file_except_gitkeep() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/bad-name.txt": "invalid name\n",
            "architecture/empty/.gitkeep": "",
        }
    )

    assert validate_naming(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/bad-name.txt"),
            1,
            "NAM001",
            "architecture filename must use uppercase snake case with a lowercase "
            "extension",
        ),
    )


@pytest.mark.unit
def test_srs_requires_a_non_acceptance_requirement_identifier() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/demo/SRS.md": (
                "# Demo SRS\n\n"
                "## Acceptance Criteria\n\n"
                "- **DEM-AC-001:** The demonstration passes.\n\n"
                "## Traceability\n\n"
                "[Roadmap](ROADMAP.md)\n"
            )
        }
    )

    assert validate_srs_structure(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/demo/SRS.md"),
            1,
            "SRS002",
            "SRS does not declare a non-acceptance requirement identifier",
        ),
    )


@pytest.mark.unit
def test_malformed_srs_traceability_link_returns_issues_instead_of_raising() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/demo/SRS.md": (
                "# SRS\n\n"
                "## Acceptance Criteria\n\n"
                "- **DEM-AC-001:** Rule.\n\n"
                "## Traceability\n\n"
                "[Malformed](https://[invalid)\n"
            )
        }
    )

    assert {issue.code for issue in validate(corpus)} >= {"LNK003", "SRS002"}


@pytest.mark.unit
@pytest.mark.parametrize("central_link_count", [0, 2])
def test_central_roadmap_must_link_each_component_once(
    central_link_count: int,
) -> None:
    component_link = "[Demo](../apps/demo/ROADMAP.md)\n"
    corpus = corpus_from_mapping(
        {
            "architecture/project/ROADMAP.md": (
                "# Central Roadmap\n\n" + component_link * central_link_count
            ),
            "architecture/apps/demo/ROADMAP.md": (
                "# Demo Roadmap\n\n"
                "Part of the [central roadmap](../../project/ROADMAP.md).\n"
            ),
        }
    )

    assert "RDM005" in {issue.code for issue in validate(corpus)}


@pytest.mark.unit
def test_component_roadmap_must_link_back_to_central_once() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/project/ROADMAP.md": (
                "# Central Roadmap\n\n[Demo](../apps/demo/ROADMAP.md)\n"
            ),
            "architecture/apps/demo/ROADMAP.md": "# Demo Roadmap\n",
        }
    )

    assert "RDM005" in {issue.code for issue in validate(corpus)}


@pytest.mark.unit
def test_central_roadmap_link_requires_a_brief_scope_description() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/project/ROADMAP.md": (
                "# Central Roadmap\n\n[Demo](../apps/demo/ROADMAP.md)\n"
            ),
            "architecture/apps/demo/ROADMAP.md": (
                "# Demo Roadmap\n\n"
                "Part of the [central roadmap](../../project/ROADMAP.md).\n"
            ),
        }
    )

    assert validate_roadmap_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/project/ROADMAP.md"),
            3,
            "RDM005",
            "central roadmap link must include a brief scope description",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "tail",
    [
        " — <!-- scope description -->\n",
        " — [Details](DETAILS.md)\n",
        " —\n# Scope description\n",
    ],
)
def test_central_roadmap_scope_rejects_nonprose_tail(tail: str) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/project/ROADMAP.md": (
                "# Central Roadmap\n\n[Demo](../apps/demo/ROADMAP.md)" + tail
            ),
            "architecture/apps/demo/ROADMAP.md": (
                "# Demo Roadmap\n\n"
                "Part of the [central roadmap](../../project/ROADMAP.md).\n"
            ),
        }
    )

    assert validate_roadmap_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/project/ROADMAP.md"),
            3,
            "RDM005",
            "central roadmap link must include a brief scope description",
        ),
    )


@pytest.mark.unit
def test_registry_must_cover_every_immediate_component() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n| Selectable name | Owner |\n| --- | --- |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate(corpus)}


@pytest.mark.unit
def test_registry_rejects_duplicate_component_rows_with_one_owner_link() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "| --- | --- |\n"
                "| `demo` | [Demo](demo/README.md) |\n"
                "| `demo` | Missing |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
def test_registry_requires_owner_link_in_owner_column() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner | Alternate |\n"
                "| --- | --- | --- |\n"
                "| `demo` | Missing | [Demo](demo/README.md) |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "owner_label"),
    [("Planned", "unselectable demo"), ("Not selectable", "Demo")],
)
def test_genetic_registry_rejects_invalid_status_cell(
    status: str, owner_label: str
) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/genetic_models/README.md": (
                "# Genetic Models\n\n"
                "## Strategy Status\n\n"
                "| Name | Status | Owner |\n"
                "| --- | --- | --- |\n"
                f"| `demo` | {status} | "
                f"[{owner_label}](demo/README.md) |\n"
            ),
            "architecture/genetic_models/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
def test_registry_rejects_unknown_component_name() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "| --- | --- |\n"
                "| `demo` | [Demo](demo/README.md) |\n"
                "| `ghost` | [Ghost](ghost/README.md) |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
def test_present_registry_with_no_components_rejects_unknown_row() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "| --- | --- |\n"
                "| `ghost` | [Ghost](ghost/README.md) |\n"
            )
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
def test_absent_registry_root_with_no_components_is_skipped() -> None:
    corpus = corpus_from_mapping({"architecture/README.md": "# Architecture\n"})

    assert validate_registries(corpus) == ()


@pytest.mark.unit
def test_registry_rejects_owner_link_with_trailing_junk() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "| --- | --- |\n"
                "| `demo` | [Demo](demo/README.md) junk) |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
@pytest.mark.parametrize(
    "owner_cell",
    ["[Demo](demo/README.md)", '[Demo](demo/README.md "Owner")'],
)
def test_registry_accepts_exact_owner_link(owner_cell: str) -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "| --- | --- |\n"
                f"| `demo` | {owner_cell} |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert validate_registries(corpus) == ()


@pytest.mark.unit
def test_registry_header_and_separator_must_be_physically_adjacent() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "```markdown\n"
                "fenced example\n"
                "```\n"
                "| --- | --- |\n"
                "| `demo` | [Demo](demo/README.md) |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert ValidationIssue(
        PurePosixPath("architecture/traffic_models/README.md"),
        5,
        "REG001",
        "registry table header must be followed by a physically adjacent separator row",
    ) in validate_registries(corpus)


@pytest.mark.unit
def test_registry_data_rows_must_be_physically_adjacent() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/traffic_models/README.md": (
                "# Traffic Models\n\n"
                "## Models\n\n"
                "| Selectable name | Owner |\n"
                "| --- | --- |\n"
                "```markdown\n"
                "fenced example\n"
                "```\n"
                "| `demo` | [Demo](demo/README.md) |\n"
            ),
            "architecture/traffic_models/demo/README.md": "# Demo\n",
        }
    )

    assert ValidationIssue(
        PurePosixPath("architecture/traffic_models/README.md"),
        10,
        "REG001",
        "registry table data row must be physically adjacent to the separator or "
        "previous row",
    ) in validate_registries(corpus)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("root", "section", "headers", "cells"),
    [
        (
            "traffic_models",
            "Models",
            ("Selectable name", "Planned name", "Owner"),
            ("`demo`", "`demo`", "[Demo](demo/README.md)"),
        ),
        (
            "traffic_models",
            "Models",
            ("Method", "Owner"),
            ("`demo`", "[Demo](demo/README.md)"),
        ),
        (
            "similarity_methods",
            "Registered Methods",
            ("Selectable name", "Owner"),
            ("`demo`", "[Demo](demo/README.md)"),
        ),
        (
            "genetic_models",
            "Strategy Status",
            ("Selectable name", "Status", "Owner"),
            ("`demo`", "Selectable", "[Demo](demo/README.md)"),
        ),
    ],
)
def test_registry_rejects_conflicting_or_root_inappropriate_identity_headers(
    root: str,
    section: str,
    headers: tuple[str, ...],
    cells: tuple[str, ...],
) -> None:
    header = "| " + " | ".join(headers) + " |\n"
    separator = "| " + " | ".join("---" for _ in headers) + " |\n"
    row = "| " + " | ".join(cells) + " |\n"
    corpus = corpus_from_mapping(
        {
            f"architecture/{root}/README.md": (
                f"# Registry\n\n## {section}\n\n{header}{separator}{row}"
            ),
            f"architecture/{root}/demo/README.md": "# Demo\n",
        }
    )

    assert "REG001" in {issue.code for issue in validate_registries(corpus)}


@pytest.mark.unit
def test_non_plan_roadmap_entry_requires_evidence() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/demo/ROADMAP.md": (
                "# Roadmap\n\n"
                "## [DONE] STAGE 1 — Implemented\n\n"
                "- **Task:** Complete the stage.\n"
                "- **Deliverable:** An increment.\n"
                "- **Applicable test types:** Unit.\n"
                "- **Completion criteria:** The unit test passes.\n"
            )
        }
    )

    assert "RDM003" in {issue.code for issue in validate(corpus)}


@pytest.mark.unit
def test_parent_roadmap_status_reflects_immediate_children() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/demo/ROADMAP.md": (
                "# Roadmap\n\n"
                "## [PLAN] STAGE 1 — Parent\n\n"
                "- **Task:** Complete the stage.\n"
                "- **Deliverable:** An increment.\n"
                "- **Applicable test types:** Unit.\n"
                "- **Completion criteria:** The step is done.\n\n"
                "### [DONE] STEP 1.1 — Child\n\n"
                "- **Task:** Complete the step.\n"
                "- **Deliverable:** An increment.\n"
                "- **Applicable test types:** Unit.\n"
                "- **Completion criteria:** The unit test passes.\n"
                "- **Evidence:** Unit test passed.\n"
            )
        }
    )

    assert "RDM004" in {issue.code for issue in validate(corpus)}


@pytest.mark.integration
def test_repository_architecture_corpus_is_valid() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    corpus = load_corpus(repository_root, repository_root / "architecture")

    assert validate(corpus) == ()


@pytest.mark.unit
def test_main_prints_issues_and_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    (architecture_root / "README.md").write_text(
        "[Missing](MISSING.md)\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert main(("architecture",)) == 1
    assert capsys.readouterr().out == (
        "architecture/README.md:1: [LNK001] "
        "local target does not exist: architecture/MISSING.md\n"
    )


@pytest.mark.unit
def test_main_defaults_to_architecture_and_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    (architecture_root / "README.md").write_text("# Architecture\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(()) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_main_keeps_control_bearing_corpus_filename_on_one_output_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    architecture_root = tmp_path / "architecture"
    architecture_root.mkdir()
    (architecture_root / "BAD\nNAME.md").write_text("# Bad\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(("architecture",)) == 1
    output = capsys.readouterr().out
    assert output == (
        r"architecture/BAD\nNAME.md:1: [NAM001] architecture filename must use "
        "uppercase snake case with a lowercase extension\n"
    )
    assert output.splitlines() == [output.rstrip("\n")]


@pytest.mark.unit
def test_main_reports_boundary_errors_with_exit_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(("missing",)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("architecture validation error:")
