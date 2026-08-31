#!/usr/bin/env python3
"""Create TrafficLab-compatible copies of external traffic dumps."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.capture.validation import CaptureInspection, validate_capture_pair
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng, read_pcapng_packets
from trafficlab.common.trace import CaptureMetadata, Direction, normalize_reference, render_capture_metadata

_CAPTURE_SUFFIXES = frozenset({".pcap", ".pcapng"})
_DEFAULT_PREFIX = "trafficlab-ready-"
# External prepared captures are only structural stand-ins for TrafficLab's
# production parser boundary. This fixed metadata must not be treated as
# authoritative provenance for the original capture interface or endpoint.
_STRUCTURAL_METADATA = CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:02")


@dataclass(frozen=True, slots=True)
class Conversion:
    """One immutable source-to-destination sibling conversion plan."""

    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class OrganizedConversion:
    """One immutable source-to-directory organized conversion plan."""

    source: Path
    directory: Path
    capture_path: Path
    metadata_path: Path


@dataclass(frozen=True, slots=True)
class PreparedCapture:
    """Validated structural facts for one prepared sibling capture."""

    packet_count: int
    observation_window_seconds: float


@dataclass(frozen=True, slots=True)
class InferredTargetMac:
    """Deterministic Ethernet endpoint inference for one prepared capture."""

    target_mac: str
    transmitted_packet_count: int
    source_count: int
    destination_count: int
    total_appearances: int


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


def organized_output_path(source: Path, *, organized_root: Path, prefix: str) -> Path:
    """Return the prepared PCAPNG path within one organized output directory."""
    return organized_root / source.stem / f"{prefix}{source.stem}.pcapng"


def _organized_directory(source: Path, *, organized_root: Path) -> Path:
    return organized_root / source.stem


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


def _is_capture_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _CAPTURE_SUFFIXES


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
            if _is_capture_file(candidate) and not candidate.name.startswith(prefix)
        )
    return tuple(sorted(discovered, key=str))


def find_prior_generated_outputs(paths: tuple[Path, ...], *, prefix: str) -> tuple[Path, ...]:
    """Find recursive prepared outputs before organized publication starts."""
    generated: set[Path] = set()
    for path in paths:
        if _is_capture_file(path) and path.name.startswith(prefix):
            generated.add(path)
            continue
        if not path.is_dir():
            continue
        generated.update(
            candidate
            for candidate in path.rglob("*")
            if _is_capture_file(candidate) and candidate.name.startswith(prefix)
        )
    return tuple(sorted(generated, key=str))


def _detect_path_aliases(paths: tuple[Path, ...]) -> None:
    resolved_paths: dict[Path, Path] = {}
    for path in paths:
        resolved = path.resolve(strict=True)
        prior = resolved_paths.get(resolved)
        if prior is not None and prior != path:
            raise ValueError(f"input path alias detected: {prior} and {path}")
        resolved_paths[resolved] = path


def plan_conversions(inputs: tuple[Path, ...], *, prefix: str) -> tuple[Conversion, ...]:
    """Create a non-overwriting sibling conversion plan."""
    plan = tuple(Conversion(source, output_path(source, prefix=prefix)) for source in inputs)
    destinations = {conversion.destination.resolve(strict=False) for conversion in plan}
    if len(destinations) != len(plan):
        raise ValueError("multiple inputs map to the same output; rename an input or choose separate invocations")
    for conversion in plan:
        if conversion.destination.exists():
            raise ValueError(f"output already exists: {conversion.destination}")
    return plan


def plan_organized_conversions(
    inputs: tuple[Path, ...],
    *,
    organized_root: Path,
    prefix: str,
) -> tuple[OrganizedConversion, ...]:
    """Create a non-overwriting organized conversion plan."""
    stems: dict[str, Path] = {}
    plan: list[OrganizedConversion] = []
    for source in inputs:
        prior = stems.get(source.stem)
        if prior is not None:
            raise ValueError(f"duplicate source stem for organized output: {prior} and {source}")
        stems[source.stem] = source
        directory = _organized_directory(source, organized_root=organized_root)
        if directory.exists():
            raise ValueError(f"output already exists: {directory}")
        plan.append(
            OrganizedConversion(
                source=source,
                directory=directory,
                capture_path=organized_output_path(source, organized_root=organized_root, prefix=prefix),
                metadata_path=directory / "capture.json",
            )
        )
    return tuple(plan)


def preflight_organized_conversions(
    paths: tuple[Path, ...],
    *,
    organized_root: Path,
    prefix: str,
) -> tuple[OrganizedConversion, ...]:
    """Validate organized-mode inputs before any conversion or publication."""
    if prefix != _DEFAULT_PREFIX:
        raise ValueError(f"organized output requires the default prefix {_DEFAULT_PREFIX!r}")
    if organized_root.exists() and not organized_root.is_dir():
        raise ValueError(f"organized output root must be a directory path: {organized_root}")
    missing = tuple(path for path in paths if not path.exists())
    if missing:
        raise ValueError(f"input path does not exist: {missing[0]}")
    _detect_path_aliases(paths)
    generated = find_prior_generated_outputs(paths, prefix=prefix)
    if generated:
        raise ValueError(f"generated output already present in selected inputs: {generated[0]}")
    inputs = discover_inputs(paths, prefix=prefix)
    if not inputs:
        raise ValueError("no unprepared .pcap or .pcapng input files were found")
    _detect_path_aliases(inputs)
    return plan_organized_conversions(inputs, organized_root=organized_root, prefix=prefix)


def validate_capture(path: Path) -> PreparedCapture:
    """Validate one prepared file through TrafficLab's production PCAPNG boundary."""
    trace, window = normalize_reference(read_pcapng(path, _STRUCTURAL_METADATA))
    return PreparedCapture(packet_count=len(trace), observation_window_seconds=window)


def _mac_text(value: bytes) -> str:
    return ":".join(f"{octet:02x}" for octet in value)


def _is_nonzero_unicast_mac(value: bytes) -> bool:
    return value != b"\x00" * 6 and not (value[0] & 1)


def infer_target_mac(path: Path) -> InferredTargetMac:
    """Infer the most likely structural target MAC from Ethernet headers only."""
    source_counts: defaultdict[str, int] = defaultdict(int)
    destination_counts: defaultdict[str, int] = defaultdict(int)
    packets = read_pcapng_packets(path, _STRUCTURAL_METADATA, source=path)
    for packet in packets:
        frame = packet.ethernet_frame
        if len(frame) < 14:
            raise ValueError(f"invalid Ethernet frame in prepared capture: {path}")
        destination = frame[:6]
        source = frame[6:12]
        if _is_nonzero_unicast_mac(source):
            source_counts[_mac_text(source)] += 1
        if _is_nonzero_unicast_mac(destination):
            destination_counts[_mac_text(destination)] += 1
    candidates = [
        InferredTargetMac(
            target_mac=mac,
            transmitted_packet_count=source_count,
            source_count=source_count,
            destination_count=destination_counts[mac],
            total_appearances=source_count + destination_counts[mac],
        )
        for mac, source_count in source_counts.items()
        if destination_counts[mac] > 0
    ]
    if not candidates:
        raise ValueError(f"no eligible target MAC could be inferred from prepared capture: {path}")
    return min(
        candidates,
        key=lambda candidate: (
            -candidate.total_appearances,
            -candidate.transmitted_packet_count,
            candidate.target_mac,
        ),
    )


def render_inferred_capture_metadata(target_mac: str) -> bytes:
    """Render canonical capture metadata for one inferred structural target MAC."""
    return render_capture_metadata(CaptureMetadata(interface="eth0", target_mac=target_mac))


def convert_capture(
    conversion: Conversion,
    *,
    tools: ToolPaths,
    run: CommandRunner,
    validate: CaptureValidator,
) -> None:
    """Convert, order, validate, and exclusively publish one new sibling capture."""
    with tempfile.TemporaryDirectory(dir=conversion.destination.parent, prefix=".trafficlab-dump-") as temporary:
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


def _cleanup_staged_directory(path: Path | None) -> None:
    if path is None:
        return
    shutil.rmtree(path, ignore_errors=True)


def convert_capture_to_organized_directory(
    conversion: OrganizedConversion,
    *,
    tools: ToolPaths,
    run: CommandRunner,
) -> CaptureInspection:
    """Convert, infer metadata, validate the pair, and atomically publish one directory."""
    stage_parent = conversion.directory.parent.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_directory: Path | None = Path(
        tempfile.mkdtemp(dir=stage_parent, prefix=f".trafficlab-dump-{conversion.source.stem}-")
    )
    published = False
    try:
        converted = stage_directory / "converted.pcapng"
        ordered = stage_directory / conversion.capture_path.name
        metadata_path = stage_directory / conversion.metadata_path.name
        run((tools.editcap, "-F", "pcapng", str(conversion.source), str(converted)))
        run((tools.reordercap, str(converted), str(ordered)))
        converted.unlink()
        inferred = infer_target_mac(ordered)
        metadata_path.write_bytes(render_inferred_capture_metadata(inferred.target_mac))
        inspection = validate_capture_pair(metadata_path, ordered, deadline=None)
        conversion.directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(stage_directory, conversion.directory)
        except FileExistsError as error:
            raise ValueError(f"output already exists: {conversion.directory}") from error
        published = True
        return inspection
    finally:
        if not published:
            _cleanup_staged_directory(stage_directory)


def _parse_arguments(argv: Sequence[str] | None) -> tuple[tuple[Path, ...], str, Path | None]:
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
    parser.add_argument(
        "--organized-root",
        type=Path,
        help="write one validated pair per source under ORGANIZED_ROOT/<source-stem>/",
    )
    arguments = parser.parse_args(argv)
    paths = tuple(arguments.paths) if arguments.paths else (Path("dumps"),)
    return paths, str(arguments.prefix), arguments.organized_root


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare every selected capture, returning a conventional process status."""
    try:
        paths, prefix, organized_root = _parse_arguments(argv)
        validate_prefix(prefix)
        tools = find_tools()
        if organized_root is None:
            missing = tuple(path for path in paths if not path.exists())
            if missing:
                raise ValueError(f"input path does not exist: {missing[0]}")
            inputs = discover_inputs(paths, prefix=prefix)
            if not inputs:
                raise ValueError("no unprepared .pcap or .pcapng input files were found")
            plan = plan_conversions(inputs, prefix=prefix)
            for conversion in plan:
                convert_capture(conversion, tools=tools, run=run_command, validate=validate_capture)
                print(f"prepared {conversion.source} -> {conversion.destination}")
            print(f"prepared {len(plan)} capture(s)")
            return 0

        plan = preflight_organized_conversions(paths, organized_root=organized_root, prefix=prefix)
        for conversion in plan:
            inspection = convert_capture_to_organized_directory(conversion, tools=tools, run=run_command)
            print(
                "prepared "
                f"{conversion.source} -> {conversion.capture_path} "
                f"metadata={conversion.metadata_path} "
                f"target_mac={inspection.metadata.target_mac} "
                f"packets={inspection.packet_count} "
                f"outbound={inspection.direction_counts[Direction.OUTBOUND]} "
                f"inbound={inspection.direction_counts[Direction.INBOUND]}"
            )
        print(f"prepared {len(plan)} capture pair(s)")
    except (OSError, RuntimeError, TrafficlabError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"prepare_traffic_dumps: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
