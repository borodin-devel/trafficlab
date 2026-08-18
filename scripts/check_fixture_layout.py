from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast

_MANIFEST_EXCLUSIONS = frozenset({"README.md", "manifest.json"})
_GENERATED_CACHE_DIRECTORY = "__pycache__"
_MISPLACED_FIXTURE_PATHS = (
    Path("fixtures"),
    Path("tests/docker/compose.endpoint.json"),
    Path("tests/docker/images"),
)
_PRODUCTION_TEST_REFERENCE_BYTES = (b"tests/fixtures", b"tests.fixtures")
_PRODUCTION_SOURCE_PREFIX = "src/"


class FixtureLayoutError(ValueError):
    """The checked fixture tree does not satisfy its repository contract."""


@dataclass(frozen=True, slots=True, order=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str
    mode: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(root: Path) -> tuple[ManifestEntry, ...]:
    if not root.is_dir():
        raise FixtureLayoutError(f"fixture root is not a directory: {root}")
    entries: list[ManifestEntry] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if _GENERATED_CACHE_DIRECTORY in PurePosixPath(relative).parts:
            if path.name == _GENERATED_CACHE_DIRECTORY and not stat.S_ISDIR(metadata.st_mode):
                raise FixtureLayoutError(f"fixture Python cache path must be a directory: {relative}")
            continue
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if relative in _MANIFEST_EXCLUSIONS:
            if not stat.S_ISREG(metadata.st_mode):
                raise FixtureLayoutError(f"fixture documentation must be a regular file: {relative}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise FixtureLayoutError(f"fixture entry must be a regular file: {relative}")
        entries.append(
            ManifestEntry(
                path=relative,
                size=metadata.st_size,
                sha256=_sha256(path),
                mode=stat.S_IMODE(metadata.st_mode),
            )
        )
    return tuple(entries)


def _manifest_document(entries: tuple[ManifestEntry, ...]) -> dict[str, object]:
    return {"schema_version": 1, "files": [asdict(entry) for entry in entries]}


def _manifest_bytes(entries: tuple[ManifestEntry, ...]) -> bytes:
    return (
        json.dumps(_manifest_document(entries), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _load_manifest(path: Path) -> tuple[ManifestEntry, ...]:
    try:
        raw_document = cast(object, json.loads(path.read_bytes()))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FixtureLayoutError(f"fixture manifest is unreadable: {path}") from error
    if not isinstance(raw_document, dict):
        raise FixtureLayoutError("fixture manifest must be a JSON object")
    object_document = cast(dict[object, object], raw_document)
    if not all(isinstance(key, str) for key in object_document):
        raise FixtureLayoutError("fixture manifest keys must be strings")
    document = cast(dict[str, object], raw_document)
    if document.get("schema_version") != 1:
        raise FixtureLayoutError("fixture manifest has an unsupported schema")
    raw_files = document.get("files")
    if not isinstance(raw_files, list):
        raise FixtureLayoutError("fixture manifest files must be an array")
    files = cast(list[object], raw_files)
    entries: list[ManifestEntry] = []
    for raw_value in files:
        if not isinstance(raw_value, dict):
            raise FixtureLayoutError("fixture manifest entry must be an object")
        object_value = cast(dict[object, object], raw_value)
        if not all(isinstance(key, str) for key in object_value):
            raise FixtureLayoutError("fixture manifest entry keys must be strings")
        value = cast(dict[str, object], raw_value)
        if tuple(sorted(value)) != ("mode", "path", "sha256", "size"):
            raise FixtureLayoutError("fixture manifest entry has invalid fields")
        path_value = value["path"]
        size = value["size"]
        sha256 = value["sha256"]
        mode = value["mode"]
        if (
            not isinstance(path_value, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(sha256, str)
            or not isinstance(mode, int)
            or isinstance(mode, bool)
        ):
            raise FixtureLayoutError("fixture manifest entry has invalid value types")
        entries.append(ManifestEntry(path=path_value, size=size, sha256=sha256, mode=mode))
    result = tuple(entries)
    if result != tuple(sorted(result, key=lambda entry: entry.path.encode("utf-8"))):
        raise FixtureLayoutError("fixture manifest entries are not in canonical order")
    return result


def write_manifest(root: Path, manifest_path: Path) -> None:
    manifest_path.write_bytes(_manifest_bytes(build_manifest(root)))


def check_manifest(root: Path, manifest_path: Path) -> None:
    actual = build_manifest(root)
    expected = _load_manifest(manifest_path)
    if actual != expected or manifest_path.read_bytes() != _manifest_bytes(expected):
        raise FixtureLayoutError("fixture manifest does not match the root fixture tree")


def tracked_phase_paths(repository: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    paths = tuple(Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value)
    return tuple(path for path in paths if "phase" in PurePosixPath(path.as_posix()).name.lower())


def misplaced_fixture_paths(repository: Path) -> tuple[Path, ...]:
    present: list[Path] = []
    for relative in _MISPLACED_FIXTURE_PATHS:
        try:
            (repository / relative).lstat()
        except FileNotFoundError:
            continue
        present.append(relative)
    return tuple(present)


def production_test_fixture_references(repository: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    references: list[Path] = []
    for value in result.stdout.split(b"\0"):
        if not value:
            continue
        relative = Path(value.decode("utf-8"))
        name = relative.as_posix()
        if relative.suffix != ".py" or not name.startswith(_PRODUCTION_SOURCE_PREFIX):
            continue
        content = (repository / relative).read_bytes()
        if any(reference in content for reference in _PRODUCTION_TEST_REFERENCE_BYTES):
            references.append(relative)
    return tuple(references)


def _repository() -> Path:
    return Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the repository fixture layout")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write-manifest", action="store_true")
    action.add_argument("--check-manifest", action="store_true")
    action.add_argument("--check", action="store_true")
    return parser


def _fail(message: str) -> NoReturn:
    raise FixtureLayoutError(message)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    repository = _repository()
    roots = (repository / "examples" / "data", repository / "tests" / "fixtures" / "data")
    if cast(bool, arguments.write_manifest):
        for root in roots:
            write_manifest(root, root / "manifest.json")
        return 0
    for root in roots:
        check_manifest(root, root / "manifest.json")
    if cast(bool, arguments.check):
        phase_paths = tracked_phase_paths(repository)
        if phase_paths:
            _fail("tracked filenames contain phase: " + ", ".join(path.as_posix() for path in phase_paths))
        misplaced_paths = misplaced_fixture_paths(repository)
        if misplaced_paths:
            _fail("misplaced fixture paths remain: " + ", ".join(path.as_posix() for path in misplaced_paths))
        production_references = production_test_fixture_references(repository)
        if production_references:
            _fail(
                "production sources reference test fixtures: "
                + ", ".join(path.as_posix() for path in production_references)
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FixtureLayoutError as error:
        print(f"fixture-layout: {error}")
        raise SystemExit(2) from error
