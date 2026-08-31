#!/usr/bin/env python3
"""Create TrafficLab-compatible PCAPNG copies of external traffic dumps."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng
from trafficlab.common.trace import CaptureMetadata, normalize_reference

_CAPTURE_SUFFIXES = frozenset({".pcap", ".pcapng"})
_DEFAULT_PREFIX = "trafficlab-ready-"
# Direction classification does not affect structural acceptance.  A fixed
# unicast MAC lets the production parser validate frames without claiming an
# authoritative target identity for an external capture.
_STRUCTURAL_METADATA = CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:02")


@dataclass(frozen=True, slots=True)
class Conversion:
    """One immutable source-to-destination conversion plan."""

    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class PreparedCapture:
    """Validated structural facts for one prepared capture."""

    packet_count: int
    observation_window_seconds: float


@dataclass(frozen=True, slots=True)
class ToolPaths:
    """Resolved Wireshark command paths used by one invocation."""

    editcap: str
    reordercap: str


type CommandRunner = Callable[[tuple[str, ...]], None]
type CaptureValidator = Callable[[Path], object]
type ExecutableFinder = Callable[[str], str | None]


def output_path(source: Path, *, prefix: str) -> Path:
    """Return the prefixed sibling PCAPNG destination for one capture."""
    return source.with_name(f"{prefix}{source.stem}.pcapng")


def validate_prefix(prefix: str) -> None:
    """Reject prefixes that are empty or contain path traversal syntax."""
    if prefix in {"", ".", ".."} or "/" in prefix or "\\" in prefix:
        raise ValueError("output prefix must be a nonempty filename prefix without path separators")


def find_tools(which: ExecutableFinder = shutil.which) -> ToolPaths:
    """Resolve the required Wireshark programs or report all missing names."""
    resolved = {name: which(name) for name in ("editcap", "reordercap")}
    missing = tuple(name for name, path in resolved.items() if path is None)
    if missing:
        raise ValueError(f"required Wireshark program(s) not found on PATH: {', '.join(missing)}")
    return ToolPaths(editcap=resolved["editcap"] or "", reordercap=resolved["reordercap"] or "")


def run_command(command: tuple[str, ...]) -> None:
    """Run one bounded external conversion command with captured diagnostics."""
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostics"
        raise RuntimeError(f"{Path(command[0]).name} failed with status {completed.returncode}: {detail}")


def discover_inputs(paths: tuple[Path, ...], *, prefix: str) -> tuple[Path, ...]:
    """Discover sorted capture inputs while excluding this tool's outputs."""
    discovered: set[Path] = set()
    for path in paths:
        if path.is_file():
            if path.suffix.lower() not in _CAPTURE_SUFFIXES:
                raise ValueError(f"unsupported explicit input; expected .pcap or .pcapng: {path}")
            if path.name.startswith(prefix):
                raise ValueError(f"explicit input already has output prefix {prefix!r}: {path}")
            candidates = (path,)
        else:
            candidates = path.rglob("*") if path.is_dir() else ()
        discovered.update(
            candidate
            for candidate in candidates
            if candidate.is_file()
            and candidate.suffix.lower() in _CAPTURE_SUFFIXES
            and not candidate.name.startswith(prefix)
        )
    return tuple(sorted(discovered, key=str))


def plan_conversions(inputs: tuple[Path, ...], *, prefix: str) -> tuple[Conversion, ...]:
    """Create a non-overwriting conversion plan."""
    plan = tuple(Conversion(source, output_path(source, prefix=prefix)) for source in inputs)
    destinations = {conversion.destination.resolve(strict=False) for conversion in plan}
    if len(destinations) != len(plan):
        raise ValueError("multiple inputs map to the same output; rename an input or choose separate invocations")
    for conversion in plan:
        if conversion.destination.exists():
            raise ValueError(f"output already exists: {conversion.destination}")
    return plan


def validate_capture(path: Path) -> PreparedCapture:
    """Validate one prepared file through TrafficLab's production PCAPNG boundary."""
    trace, window = normalize_reference(read_pcapng(path, _STRUCTURAL_METADATA))
    return PreparedCapture(packet_count=len(trace), observation_window_seconds=window)


def convert_capture(
    conversion: Conversion,
    *,
    tools: ToolPaths,
    run: CommandRunner,
    validate: CaptureValidator,
) -> None:
    """Convert, order, validate, and exclusively publish one new capture."""
    with TemporaryDirectory(dir=conversion.destination.parent, prefix=".trafficlab-dump-") as temporary:
        temporary_directory = Path(temporary)
        converted = temporary_directory / "converted.pcapng"
        ordered = temporary_directory / "ordered.pcapng"
        run((tools.editcap, "-F", "pcapng", str(conversion.source), str(converted)))
        run((tools.reordercap, str(converted), str(ordered)))
        validate(ordered)
        try:
            os.link(ordered, conversion.destination)
        except FileExistsError as error:
            raise ValueError(f"output already exists: {conversion.destination}") from error


def _parse_arguments(argv: Sequence[str] | None) -> tuple[tuple[Path, ...], str]:
    parser = argparse.ArgumentParser(
        description="Create ordered, validated PCAPNG copies without modifying the source traffic dumps."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="capture files or directories to scan recursively (default: dumps)",
    )
    parser.add_argument(
        "--prefix",
        default=_DEFAULT_PREFIX,
        help=f"output filename prefix (default: {_DEFAULT_PREFIX})",
    )
    arguments = parser.parse_args(argv)
    paths = tuple(arguments.paths) if arguments.paths else (Path("dumps"),)
    return paths, str(arguments.prefix)


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare every selected capture, returning a conventional process status."""
    try:
        paths, prefix = _parse_arguments(argv)
        validate_prefix(prefix)
        missing = tuple(path for path in paths if not path.exists())
        if missing:
            raise ValueError(f"input path does not exist: {missing[0]}")
        inputs = discover_inputs(paths, prefix=prefix)
        if not inputs:
            raise ValueError("no unprepared .pcap or .pcapng input files were found")
        plan = plan_conversions(inputs, prefix=prefix)
        tools = find_tools()
        for conversion in plan:
            convert_capture(conversion, tools=tools, run=run_command, validate=validate_capture)
            print(f"prepared {conversion.source} -> {conversion.destination}")
        print(f"prepared {len(plan)} capture(s)")
    except (OSError, RuntimeError, TrafficlabError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"prepare_traffic_dumps: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
