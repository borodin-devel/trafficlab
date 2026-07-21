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
        if not isinstance(path, bytes) and Path(path) == source:
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
