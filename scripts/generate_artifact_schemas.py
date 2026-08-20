#!/usr/bin/env python3
"""Generate or verify every public scientific-artifact v3 JSON Schema."""

from __future__ import annotations

import argparse
import json
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.artifact_schemas import PUBLIC_ARTIFACT_MODELS

REPOSITORY = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = REPOSITORY / "examples" / "schemas" / "scientific-artifact-v4"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def canonical_schema_bytes(document: Mapping[str, object]) -> bytes:
    """Render one public schema with deterministic UTF-8 bytes."""
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def build_schema_documents() -> dict[str, bytes]:
    """Build the complete filename-to-canonical-schema mapping."""
    documents: dict[str, bytes] = {}
    for name in sorted(PUBLIC_ARTIFACT_MODELS):
        filename = f"{name}.schema.json"
        generated = PUBLIC_ARTIFACT_MODELS[name].model_json_schema(mode="validation")
        document = cast(dict[str, object], {"$schema": DRAFT_2020_12, "$id": filename, **generated})
        documents[filename] = canonical_schema_bytes(document)
    return documents


def _schema_entries(directory: Path) -> tuple[tuple[str, bool], ...]:
    if not directory.is_dir():
        return ()
    return tuple(
        sorted((path.name, stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)) for path in directory.iterdir())
    )


def schema_directory_mismatches(directory: Path = OUTPUT_DIRECTORY) -> tuple[str, ...]:
    """Return every changed, missing, or foreign schema path in stable order."""
    expected = build_schema_documents()
    entries = dict(_schema_entries(directory))
    actual_names = set(entries)
    mismatches: list[str] = []
    mismatches.extend(f"nonregular:{name}" for name, regular in entries.items() if not regular)
    for name, content in expected.items():
        path = directory / name
        if name not in actual_names:
            mismatches.append(f"missing:{name}")
            continue
        if not entries[name]:
            continue
        try:
            actual = path.read_bytes()
        except OSError:
            actual = b""
        if actual != content:
            mismatches.append(f"changed:{name}")
    mismatches.extend(f"foreign:{name}" for name in actual_names - set(expected) if entries[name])
    return tuple(sorted(mismatches))


def write_schema_directory(directory: Path = OUTPUT_DIRECTORY) -> None:
    """Replace only the owned flat schema set with deterministic documents."""
    directory.mkdir(parents=True, exist_ok=True)
    expected = build_schema_documents()
    entries = _schema_entries(directory)
    nonregular = tuple(name for name, regular in entries if not regular)
    if nonregular:
        raise ValueError("schema directory contains nonregular entries: " + ", ".join(nonregular))
    for name, _regular in entries:
        if name not in expected:
            path = directory / name
            path.unlink()
    for name, content in expected.items():
        (directory / name).write_bytes(content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail unless the complete checked schema set matches")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    output = cast(Path, arguments.output)
    if cast(bool, arguments.check):
        mismatches = schema_directory_mismatches(output)
        if mismatches:
            for mismatch in mismatches:
                print(f"artifact-schemas: {mismatch}", file=sys.stderr)
            return 1
        print(f"artifact-schemas: verified {len(build_schema_documents())} public roots at {output}")
        return 0
    write_schema_directory(output)
    total_bytes = sum(len(content) for content in build_schema_documents().values())
    print(f"artifact-schemas: wrote {len(build_schema_documents())} public roots ({total_bytes} bytes) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
