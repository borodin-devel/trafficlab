#!/usr/bin/env python3
"""Derive bounded development references from one canonical PCAPNG capture."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.json import render_json_document
from trafficlab.common.trace import load_capture_metadata


@dataclass(frozen=True, slots=True)
class ToolPaths:
    """Resolved Wireshark tools used by one derivation."""

    editcap: str
    reordercap: str


@dataclass(frozen=True, slots=True)
class DerivationResult:
    """Published reference details and the staging paths used for provenance tests."""

    output: Path
    reference: Path
    metadata: Path
    manifest: Path
    packet_count: int
    observation_window_seconds: float
    staged_extracted: Path
    staged_ordered: Path


type CommandRunner = Callable[[tuple[str, ...]], None]
type VersionReader = Callable[[str], str]
type Validator = Callable[[Path, Path], object]
type ToolFinder = Callable[[], ToolPaths]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def find_tools() -> ToolPaths:
    """Find the two Wireshark command-line tools required for derivation."""
    editcap = shutil.which("editcap")
    reordercap = shutil.which("reordercap")
    if editcap is None or reordercap is None:
        missing = ", ".join(name for name, value in (("editcap", editcap), ("reordercap", reordercap)) if value is None)
        raise ValueError(f"required Wireshark program(s) not found on PATH: {missing}")
    return ToolPaths(editcap, reordercap)


def run_command(command: tuple[str, ...]) -> None:
    """Run one conversion command with bounded diagnostics."""
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostics"
        raise RuntimeError(f"{Path(command[0]).name} failed with status {completed.returncode}: {detail}")


def read_tool_version(tool: str) -> str:
    """Return the first deterministic version line emitted by a tool."""
    completed = subprocess.run((tool, "--version"), check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostics"
        raise RuntimeError(f"{Path(tool).name} --version failed with status {completed.returncode}: {detail}")
    output = completed.stdout.strip() or completed.stderr.strip()
    if not output:
        raise RuntimeError(f"{Path(tool).name} --version returned no output")
    return output.splitlines()[0]


def _default_validate(metadata: Path, pcapng: Path) -> object:
    return validate_capture_pair(metadata, pcapng, deadline=None)


def _packet_count(inspection: object) -> int:
    value = getattr(inspection, "packet_count", None)
    if type(value) is not int or value <= 0:
        raise ValueError("strict capture validation did not return a positive packet count")
    return value


def _window(inspection: object) -> float:
    direct = getattr(inspection, "observation_window_seconds", None)
    if type(direct) is float and direct > 0.0:
        return direct
    trace = getattr(inspection, "trace", None)
    timestamps = getattr(trace, "timestamps", None)
    if timestamps is not None and len(timestamps) >= 2:
        result = float(timestamps[-1] - timestamps[0])
    else:
        first = getattr(inspection, "first_timestamp", None)
        last = getattr(inspection, "last_timestamp", None)
        result = float(last - first) if type(first) is float and type(last) is float else 0.0
    if not result > 0.0:
        raise ValueError("strict capture validation did not return a positive observation window W")
    return result


def _publish_directory_no_replace(stage: Path, destination: Path) -> None:
    """Publish the staged directory without replacing an existing path entry."""
    from scripts.prepare_traffic_dumps import publish_directory_no_replace

    publish_directory_no_replace(stage, destination)


def _manifest_bytes(
    *,
    source: Path,
    source_bytes: bytes,
    capture: Path,
    capture_bytes: bytes,
    reference: Path,
    reference_bytes: bytes,
    tools: ToolPaths,
    versions: dict[str, str],
    packet_count: int,
    observation_window_seconds: float,
) -> bytes:
    return render_json_document(
        {
            "schema_version": 1,
            "source": {"path": str(source.resolve()), "sha256": _sha256(source_bytes), "size": len(source_bytes)},
            "capture": {"path": str(capture.resolve()), "sha256": _sha256(capture_bytes), "size": len(capture_bytes)},
            "output": {
                "path": reference.name,
                "sha256": _sha256(reference_bytes),
                "size": len(reference_bytes),
            },
            "tools": {"editcap": versions[tools.editcap], "reordercap": versions[tools.reordercap]},
            "packet_count": packet_count,
            "W": observation_window_seconds,
        }
    )


def derive_required_candidates(
    source: Path,
    capture_json: Path,
    *,
    packet_limit: int | None,
    output: Path,
    tools: ToolPaths | None = None,
    run: CommandRunner = run_command,
    versions: VersionReader = read_tool_version,
    validate: Validator = _default_validate,
    find: ToolFinder = find_tools,
) -> DerivationResult:
    """Derive and exclusively publish one bounded, strictly validated capture pair."""
    if not source.is_file():
        raise FileNotFoundError(source)
    if not capture_json.is_file():
        raise FileNotFoundError(capture_json)
    if packet_limit is not None and (type(packet_limit) is not int or packet_limit <= 0):
        raise ValueError("packet limit must be a positive integer")
    if os.path.lexists(output):
        raise FileExistsError(output)

    source_bytes = source.read_bytes()
    capture_bytes = capture_json.read_bytes()
    # Parse before invoking external tools, while preserving the original bytes verbatim.
    load_capture_metadata(capture_json)
    selected_tools = tools or find()
    versions_by_tool = {
        selected_tools.editcap: versions(selected_tools.editcap),
        selected_tools.reordercap: versions(selected_tools.reordercap),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    extracted = stage / "extracted.pcapng"
    ordered = stage / "ordered.pcapng"
    reference = stage / "reference.pcapng"
    metadata = stage / "capture.json"
    manifest = stage / "manifest.json"
    published = False
    try:
        selected_limit = packet_limit
        if selected_limit is None:
            selected_limit = _packet_count(validate(capture_json, source))
        run((selected_tools.editcap, "-r", str(source), str(extracted), f"1-{selected_limit}"))
        run((selected_tools.reordercap, str(extracted), str(ordered)))
        extracted.unlink()
        inspection = validate(capture_json, ordered)
        count = _packet_count(inspection)
        if count != selected_limit:
            raise ValueError(f"derived capture contains {count} packets, expected exactly {selected_limit}")
        window = _window(inspection)
        ordered.replace(reference)
        metadata.write_bytes(capture_bytes)
        with reference.open("rb") as stream:
            reference_bytes = stream.read()
        manifest.write_bytes(
            _manifest_bytes(
                source=source,
                source_bytes=source_bytes,
                capture=capture_json,
                capture_bytes=capture_bytes,
                reference=reference,
                reference_bytes=reference_bytes,
                tools=selected_tools,
                versions=versions_by_tool,
                packet_count=count,
                observation_window_seconds=window,
            )
        )
        _publish_directory_no_replace(stage, output)
        published = True
        return DerivationResult(
            output=output,
            reference=output / reference.name,
            metadata=output / metadata.name,
            manifest=output / manifest.name,
            packet_count=count,
            observation_window_seconds=window,
            staged_extracted=extracted,
            staged_ordered=ordered,
        )
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--capture-json", type=Path, required=True)
    parser.add_argument("--packet-limit", type=int)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        result = derive_required_candidates(
            args.source,
            args.capture_json,
            packet_limit=args.packet_limit,
            output=args.output,
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"derive_required_candidates_reference: {error}", file=sys.stderr)
        return 1
    print(f"derived {result.packet_count} packets W={result.observation_window_seconds:g} -> {result.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
