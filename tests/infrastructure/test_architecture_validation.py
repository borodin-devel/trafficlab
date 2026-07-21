"""Tests for deterministic architecture-corpus validation."""

from pathlib import Path, PurePosixPath

import pytest
from tools.validate_architecture import (
    ValidationIssue,
    corpus_from_mapping,
    load_corpus,
    main,
    validate,
    validate_identifiers,
    validate_links,
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
def test_setext_heading_anchor_resolves() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "[Rules](RULES.md#setext-rules)\n",
            "architecture/RULES.md": "Setext **Rules**\n----------------\n",
        }
    )

    assert validate_links(corpus) == ()


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
