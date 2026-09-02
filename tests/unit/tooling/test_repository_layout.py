from __future__ import annotations

import ast
import importlib.util
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
CHECKER_PATH = REPOSITORY / "scripts" / "check_fixture_layout.py"

VALIDATION_STUDY_TOOLING = {
    "__init__.py",
    "audit/__init__.py",
    "audit/artifacts.py",
    "audit/common.py",
    "audit/environment.py",
    "audit/lifecycle.py",
    "audit/science.py",
    "candidate/__init__.py",
    "candidate/artifacts.py",
    "candidate/held_out.py",
    "candidate/reporting.py",
    "cli.py",
    "collection.py",
    "common.py",
    "evidence.py",
    "fixture.py",
    "prerequisites/__init__.py",
    "prerequisites/codec.py",
    "prerequisites/commands.py",
    "prerequisites/run.py",
    "records.py",
    "results/__init__.py",
    "results/codec.py",
    "results/reporting.py",
    "results/reproduction.py",
    "rotation/__init__.py",
    "rotation/run.py",
    "rotation/schema.py",
    "transfer.py",
    "workloads.py",
}

VALIDATION_STUDY_WRAPPERS = {
    "audit_validation_study.py": "scripts.validation_study.audit.lifecycle",
    "generate_validation_study_fixture.py": "scripts.validation_study.fixture",
    "run_validation_study.py": "scripts.validation_study.cli",
}

TOOLING_MODULE_LINE_LIMIT = 800
PRODUCTION_MODULE_LINE_LIMIT = 600
TEST_MODULE_LINE_LIMIT = 1_000
TOP_LEVEL_ARCHITECTURE_DOCUMENTS = {
    "CANDIDATES.md",
    "CAPTURE.md",
    "DEVELOPMENT.md",
    "README.md",
    "RESEARCH_FITNESS_CRITERIA.md",
    "SYSTEM.md",
    "TESTING.md",
    "VISUALIZATION.md",
}

FAILURE_MATRIX_SUPPORT = {"__init__.py", "cases.py", "doubles.py", "oracle.py", "runners.py"}

IMMUTABLE_VALIDATION_EVIDENCE_PREFIX = "examples/validation_study/evidence/"
HUMAN_AUTHORED_JSON_PREFIX = ".vscode/"
IMMUTABLE_LEGACY_JSON_PREFIX = "tests/fixtures/data/validation_study/pre-user-agent-r6/"
IMMUTABLE_LEGACY_JSON_PATHS = frozenset({"examples/validation_study/results.json"})


class _ManifestEntry(Protocol):
    path: str
    size: int
    sha256: str
    mode: int


class _Checker(Protocol):
    class FixtureLayoutError(ValueError): ...

    def build_manifest(self, root: Path) -> tuple[_ManifestEntry, ...]: ...

    def write_manifest(self, root: Path, manifest_path: Path) -> None: ...

    def check_manifest(self, root: Path, manifest_path: Path) -> None: ...

    def misplaced_fixture_paths(self, repository: Path) -> tuple[Path, ...]: ...

    def production_test_fixture_references(self, repository: Path) -> tuple[Path, ...]: ...


def _load_checker() -> _Checker:
    assert CHECKER_PATH.is_file(), "repository fixture-layout checker is missing"
    spec = importlib.util.spec_from_file_location("check_fixture_layout", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_Checker, module)


def test_regenerable_tracked_json_documents_are_readable() -> None:
    completed = subprocess.run(
        ("git", "ls-files", "*.json"),
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    mismatches: list[str] = []
    for relative in completed.stdout.splitlines():
        if relative in IMMUTABLE_LEGACY_JSON_PATHS or relative.startswith(
            (HUMAN_AUTHORED_JSON_PREFIX, IMMUTABLE_LEGACY_JSON_PREFIX, IMMUTABLE_VALIDATION_EVIDENCE_PREFIX)
        ):
            continue
        path = REPOSITORY / relative
        content = path.read_bytes()
        document = json.loads(content)
        expected = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )
        if content != expected:
            mismatches.append(relative)

    assert mismatches == []


def test_vscode_uses_the_project_virtual_environment_for_python_analysis() -> None:
    settings = json.loads((REPOSITORY / ".vscode" / "settings.json").read_bytes())

    assert settings["python.defaultInterpreterPath"] == "${workspaceFolder}/.venv/bin/python"


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
            indent=2,
            allow_nan=False,
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

    manifest = tmp_path / "manifest.json"
    checker.write_manifest(root, manifest)
    document = json.loads(manifest.read_bytes())
    assert manifest.read_bytes() == (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def test_manifest_ignores_generated_python_cache_files(tmp_path: Path) -> None:
    checker = _load_checker()
    root = tmp_path / "fixtures"
    cache = root / "tests" / "client" / "__pycache__"
    cache.mkdir(parents=True)
    (root / "tests" / "client" / "client.py").write_bytes(b"print('fixture')\n")
    (cache / "client.cpython-312.pyc").write_bytes(b"generated bytecode")

    assert tuple(entry.path for entry in checker.build_manifest(root)) == ("tests/client/client.py",)


def test_manifest_rejects_a_non_directory_python_cache_path(tmp_path: Path) -> None:
    checker = _load_checker()
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "__pycache__").write_bytes(b"not a directory")

    with pytest.raises(checker.FixtureLayoutError, match="Python cache path must be a directory"):
        checker.build_manifest(root)


def test_manifest_rejects_a_missing_fixture_root(tmp_path: Path) -> None:
    checker = _load_checker()

    with pytest.raises(checker.FixtureLayoutError, match="fixture root is not a directory"):
        checker.build_manifest(tmp_path / "missing")


def test_manifest_rejects_nonregular_fixture_documentation(tmp_path: Path) -> None:
    checker = _load_checker()
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "target").write_bytes(b"target")
    (root / "README.md").symlink_to("target")

    with pytest.raises(checker.FixtureLayoutError, match="documentation must be a regular file"):
        checker.build_manifest(root)


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


@pytest.mark.parametrize("relative", ("fixtures", "tests/docker/images", "tests/docker/compose.endpoint.json"))
def test_misplaced_fixture_paths_reports_each_forbidden_source(tmp_path: Path, relative: str) -> None:
    checker = _load_checker()
    repository = tmp_path / "repository"
    path = repository / relative
    if path.suffix:
        path.parent.mkdir(parents=True)
        path.write_text("fixture", encoding="utf-8")
    else:
        path.mkdir(parents=True)

    assert checker.misplaced_fixture_paths(repository) == (Path(relative),)


def test_fixture_path_catalog_owns_each_compartment() -> None:
    catalog_path = REPOSITORY / "tests" / "fixtures" / "paths.py"
    assert catalog_path.is_file()
    catalog = runpy.run_path(str(catalog_path))

    assert catalog["FIXTURES_ROOT"] == REPOSITORY / "tests" / "fixtures"
    assert catalog["STATIC_FIXTURE_DATA"] == REPOSITORY / "tests" / "fixtures" / "data"
    assert catalog["EXAMPLE_FIXTURES"] == REPOSITORY / "examples" / "data"
    assert catalog["TEST_FIXTURES"] == catalog["STATIC_FIXTURE_DATA"]
    assert catalog["PIPELINE_FIXTURE_ROOT"] == catalog["EXAMPLE_FIXTURES"]
    assert catalog["DIAGNOSTIC_FIXTURE_ROOT"] == REPOSITORY / "tests" / "fixtures" / "data" / "diagnostics"
    assert catalog["DOCKER_FIXTURE_ROOT"] == REPOSITORY / "tests" / "fixtures" / "data" / "docker"
    assert catalog["PROCESS_GUARD_FIXTURE_ROOT"] == REPOSITORY / "tests" / "fixtures" / "data" / "process_guard"
    assert catalog["VALIDATION_STUDY_FIXTURE_ROOT"] == (REPOSITORY / "tests" / "fixtures" / "data" / "validation_study")


def test_repository_has_no_misplaced_fixture_paths() -> None:
    checker = _load_checker()

    assert checker.misplaced_fixture_paths(REPOSITORY) == ()


@pytest.mark.parametrize("reference", ("tests/fixtures/data/sample.json", "tests.fixtures.paths"))
def test_production_test_fixture_references_reports_package_dependencies(tmp_path: Path, reference: str) -> None:
    checker = _load_checker()
    repository = tmp_path / "repository"
    source = repository / "src" / "trafficlab" / "consumer.py"
    source.parent.mkdir(parents=True)
    source.write_text(f'DEPENDENCY = "{reference}"\n', encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=repository, check=True)
    subprocess.run(("git", "add", "."), cwd=repository, check=True)

    assert checker.production_test_fixture_references(repository) == (Path("src/trafficlab/consumer.py"),)


def test_repository_production_has_no_test_fixture_dependencies() -> None:
    checker = _load_checker()

    assert checker.production_test_fixture_references(REPOSITORY) == ()


def test_repository_owned_fixture_manifests_match() -> None:
    checker = _load_checker()

    checker.check_manifest(REPOSITORY / "examples" / "data", REPOSITORY / "examples" / "data" / "manifest.json")
    checker.check_manifest(
        REPOSITORY / "tests" / "fixtures" / "data",
        REPOSITORY / "tests" / "fixtures" / "data" / "manifest.json",
    )


def test_validation_study_tooling_has_the_declared_functional_owners() -> None:
    root = REPOSITORY / "scripts" / "validation_study"

    assert {
        path.relative_to(root).as_posix() for path in root.rglob("*.py") if "__pycache__" not in path.parts
    } == VALIDATION_STUDY_TOOLING
    records = ast.parse((root / "records.py").read_text(encoding="utf-8"))
    assert "HeldOutEvaluation" in {
        node.name for node in records.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    for path in (root / "audit").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("scripts.validation_study.candidate")
            for node in tree.body
        )


@pytest.mark.parametrize(("wrapper_name", "owner"), tuple(VALIDATION_STUDY_WRAPPERS.items()))
def test_validation_study_executables_are_thin_main_only_wrappers(wrapper_name: str, owner: str) -> None:
    path = REPOSITORY / "scripts" / wrapper_name
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner_imports = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == owner]

    assert len(source.splitlines()) <= 40
    assert len(owner_imports) == 1
    assert tuple(alias.name for alias in owner_imports[0].names) == ("main",)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("scripts.validation_study")
        and node is not owner_imports[0]
        for node in tree.body
    )


def test_python_tooling_stays_within_the_cohesion_backstop() -> None:
    scripts = REPOSITORY / "scripts"
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in scripts.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > TOOLING_MODULE_LINE_LIMIT
    }

    assert offenders == {}


def test_dashboard_production_modules_stay_within_the_cohesion_backstop() -> None:
    dashboard = REPOSITORY / "src" / "trafficlab_dashboard"
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in dashboard.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > PRODUCTION_MODULE_LINE_LIMIT
    }

    assert offenders == {}


def test_dashboard_test_modules_stay_within_the_cohesion_backstop() -> None:
    dashboard_tests = REPOSITORY / "tests" / "trafficlab_dashboard"
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in dashboard_tests.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > TEST_MODULE_LINE_LIMIT
    }

    assert offenders == {}


def test_architecture_inventory_includes_top_level_contracts() -> None:
    architecture = REPOSITORY / "architecture"

    assert {path.name for path in architecture.glob("*.md")} == TOP_LEVEL_ARCHITECTURE_DOCUMENTS
    assert "- [Traffic model candidates](CANDIDATES.md)" in (architecture / "README.md").read_text(encoding="utf-8")
    assert "- [Visualization](VISUALIZATION.md)" in (architecture / "README.md").read_text(encoding="utf-8")
    assert "- [Visualization companion](architecture/VISUALIZATION.md)" in (REPOSITORY / "README.md").read_text(
        encoding="utf-8"
    )


def test_visualization_contract_describes_stale_result_invalidation_and_atomic_replacement_cache_commit() -> None:
    content = (REPOSITORY / "architecture" / "VISUALIZATION.md").read_text(encoding="utf-8")

    assert "Stale worker results are discarded immediately." in content
    assert (
        "A replacement run keeps the previously accepted cache entries until its first matching plot calculation succeeds."
        in content
    )
    assert "That successful first-plot commit invalidates the previous cache atomically." in content


def test_failure_matrix_support_has_only_focused_typed_owners() -> None:
    root = REPOSITORY / "tests" / "support" / "failure_matrix"

    assert {path.name for path in root.glob("*.py")} == FAILURE_MATRIX_SUPPORT
