from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPOSITORY / "scripts" / "check_fixture_layout.py"


class _ManifestEntry(Protocol):
    path: str
    size: int
    sha256: str
    mode: int


class _Checker(Protocol):
    class FixtureLayoutError(ValueError): ...

    def build_manifest(self, root: Path) -> tuple[_ManifestEntry, ...]: ...

    def check_manifest(self, root: Path, manifest_path: Path) -> None: ...

    def tracked_phase_paths(self, repository: Path) -> tuple[Path, ...]: ...

    def legacy_fixture_paths(self, repository: Path) -> tuple[Path, ...]: ...


def _load_checker() -> _Checker:
    assert CHECKER_PATH.is_file(), "repository fixture-layout checker is missing"
    spec = importlib.util.spec_from_file_location("check_fixture_layout", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_Checker, module)


def _write_manifest(path: Path, entries: tuple[_ManifestEntry, ...]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [
                    {
                        "mode": entry.mode,
                        "path": entry.path,
                        "sha256": entry.sha256,
                        "size": entry.size,
                    }
                    for entry in entries
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def test_manifest_is_sorted_and_binds_regular_fixture_bytes_and_modes(tmp_path: Path) -> None:
    checker = _load_checker()
    root = tmp_path / "fixtures"
    (root / "tests" / "zeta").mkdir(parents=True)
    (root / "examples").mkdir()
    executable = root / "tests" / "zeta" / "tree.py"
    executable.write_bytes(b"print('tree')\n")
    executable.chmod(0o755)
    (root / "examples" / "alpha.json").write_bytes(b'{"value":1}\n')
    (root / "README.md").write_text("fixture documentation\n", encoding="utf-8")

    entries = checker.build_manifest(root)

    assert tuple(entry.path for entry in entries) == (
        "examples/alpha.json",
        "tests/zeta/tree.py",
    )
    assert entries[0].size == 12
    assert entries[0].sha256 == "3a37782e8974c48eebf2a0517c866ad15641c53b3d31993188796b56aeb79624"
    assert entries[0].mode == 0o644
    assert entries[1].mode == 0o755


@pytest.mark.parametrize("mutation", ["changed", "extra", "missing", "mode"])
def test_manifest_rejects_every_fixture_identity_or_inventory_change(tmp_path: Path, mutation: str) -> None:
    checker = _load_checker()
    root = tmp_path / "fixtures"
    root.mkdir()
    fixture = root / "sample.bin"
    fixture.write_bytes(b"original")
    manifest = root / "manifest.json"
    _write_manifest(manifest, checker.build_manifest(root))

    if mutation == "changed":
        fixture.write_bytes(b"changed!")
    elif mutation == "extra":
        (root / "extra.bin").write_bytes(b"extra")
    elif mutation == "missing":
        fixture.unlink()
    else:
        fixture.chmod(0o755)

    with pytest.raises(checker.FixtureLayoutError, match="fixture manifest does not match"):
        checker.check_manifest(root, manifest)


def test_manifest_rejects_nonregular_fixture_entries(tmp_path: Path) -> None:
    checker = _load_checker()
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "target").write_bytes(b"target")
    (root / "alias").symlink_to("target")

    with pytest.raises(checker.FixtureLayoutError, match="regular file"):
        checker.build_manifest(root)


def test_tracked_phase_paths_reports_case_insensitive_basenames_only(tmp_path: Path) -> None:
    checker = _load_checker()
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=repository, check=True)
    for relative in (
        "docs/legacy-phase-1.md",
        "scripts/PHASE_builder.py",
        "docs/phase-parent/clean.md",
        "docs/mentions-phase-in-parent/also-clean.md",
        "docs/plain.md",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=repository, check=True)

    assert checker.tracked_phase_paths(repository) == (
        Path("docs/legacy-phase-1.md"),
        Path("scripts/PHASE_builder.py"),
    )


@pytest.mark.parametrize(
    "relative",
    ("tests/fixtures", "tests/docker/compose.endpoint.json", "examples/data"),
)
def test_legacy_fixture_paths_reports_each_forbidden_source(tmp_path: Path, relative: str) -> None:
    checker = _load_checker()
    repository = tmp_path / "repository"
    path = repository / relative
    if path.suffix:
        path.parent.mkdir(parents=True)
        path.write_text("fixture", encoding="utf-8")
    else:
        path.mkdir(parents=True)

    assert checker.legacy_fixture_paths(repository) == (Path(relative),)


def test_agents_requires_precise_progress_and_generated_task_labels() -> None:
    document = (REPOSITORY / "AGENTS.md").read_text(encoding="utf-8")

    assert "[<integer>%]" in document
    assert "[+<integer>%]" in document
    assert "[-<integer>%]" in document
    assert "[ETA: <hours>h <minutes>m]" in document
    assert "[TASK-<ordinal>-<crc32>]" in document
    assert "[STEP-<ordinal>-<crc32>]" in document
    assert "eight lowercase hexadecimal digits" in document
    assert "regenerate the timestamp on collision" in document


@pytest.mark.parametrize(
    ("source_relative", "destination_relative"),
    (
        ("tests/fixtures/diagnostics", "fixtures/tests/diagnostics"),
        (
            "tests/fixtures/process_guard_tree.py",
            "fixtures/tests/process_guard/process_guard_tree.py",
        ),
        (
            "tests/fixtures/validation_study_pre_user_agent_r6",
            "fixtures/tests/validation_study/pre-user-agent-r6",
        ),
        (
            "tests/docker/compose.endpoint.json",
            "fixtures/tests/docker/compose.endpoint.json",
        ),
        ("examples/data", "fixtures/examples/pipeline"),
    ),
)
def test_root_fixture_copy_matches_legacy_source(
    source_relative: str,
    destination_relative: str,
) -> None:
    source = REPOSITORY / source_relative
    destination = REPOSITORY / destination_relative
    assert source.exists()
    assert destination.exists()
    if source.is_file():
        source_files = (source,)
        destination_files = (destination,)
    else:
        source_files = tuple(path for path in source.rglob("*") if path.is_file())
        destination_files = tuple(path for path in destination.rglob("*") if path.is_file())
        assert tuple(path.relative_to(source) for path in source_files) == tuple(
            path.relative_to(destination) for path in destination_files
        )
    for source_path, destination_path in zip(source_files, destination_files, strict=True):
        assert destination_path.read_bytes() == source_path.read_bytes()
        assert destination_path.stat().st_mode & 0o777 == source_path.stat().st_mode & 0o777


def test_fixture_path_catalog_owns_each_compartment() -> None:
    catalog_path = REPOSITORY / "tests" / "support" / "fixture_paths.py"
    assert catalog_path.is_file()
    catalog = runpy.run_path(str(catalog_path))

    assert catalog["FIXTURE_ROOT"] == REPOSITORY / "fixtures"
    assert catalog["PIPELINE_FIXTURE_ROOT"] == REPOSITORY / "fixtures" / "examples" / "pipeline"
    assert catalog["DIAGNOSTIC_FIXTURE_ROOT"] == REPOSITORY / "fixtures" / "tests" / "diagnostics"
    assert catalog["DOCKER_FIXTURE_ROOT"] == REPOSITORY / "fixtures" / "tests" / "docker"
    assert catalog["PROCESS_GUARD_FIXTURE_ROOT"] == REPOSITORY / "fixtures" / "tests" / "process_guard"
    assert catalog["VALIDATION_STUDY_FIXTURE_ROOT"] == (REPOSITORY / "fixtures" / "tests" / "validation_study")


def test_repository_has_no_phase_named_tracked_files() -> None:
    checker = _load_checker()

    assert checker.tracked_phase_paths(REPOSITORY) == ()
