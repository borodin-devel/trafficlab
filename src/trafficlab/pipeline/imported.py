"""Imported reference acquisition and full-pipeline composition."""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trafficlab.artifacts.capture import publish_capture_pair
from trafficlab.artifacts.io import FileIdentity, append_run_log, file_identity
from trafficlab.capture.stage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import ContentIdentity, identify_bytes, identify_file
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError
from trafficlab.common.scapy_io import normalize_raw_capture
from trafficlab.common.trace import load_capture_metadata
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.stage import generate_experiment
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.stage import run_preflight
from trafficlab.preflight.types import PreparedExperiment

_NORMALIZATION_VERSION = "scapy-raw-v1"
_COPY_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImportSource:
    """The exact regular-file pair accepted from one supplied directory."""

    directory: Path
    capture_path: Path
    metadata_path: Path


def _source_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"invalid imported reference directory: {detail}",
        corrective_action=(
            "provide a real directory containing exactly one PCAP or PCAPNG file and one regular capture.json"
        ),
    )


def discover_import_source(directory: Path) -> ImportSource:
    """Resolve and validate the complete direct inventory of an import source."""
    if not isinstance(directory, Path):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("directory must be a Path")
    try:
        directory_status = directory.stat(follow_symlinks=False)
    except OSError as error:
        raise _source_error(f"path is not a readable real directory: {directory}: {error}") from error
    if not stat.S_ISDIR(directory_status.st_mode):
        raise _source_error(f"path is not a real directory: {directory}")

    try:
        resolved = directory.resolve(strict=True)
        entries = sorted(resolved.iterdir(), key=lambda path: path.name.encode("utf-8"))
    except (OSError, UnicodeEncodeError) as error:
        raise _source_error(f"could not inspect directory {directory}: {error}") from error

    captures: list[Path] = []
    metadata: list[Path] = []
    unexpected: list[Path] = []
    for entry in entries:
        try:
            entry_status = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise _source_error(f"could not inspect direct entry {entry}: {error}") from error
        if not stat.S_ISREG(entry_status.st_mode):
            unexpected.append(entry)
        elif entry.name == "capture.json":
            metadata.append(entry)
        elif entry.suffix.lower() in {".pcap", ".pcapng"}:
            captures.append(entry)
        else:
            unexpected.append(entry)

    if len(captures) != 1 or len(metadata) != 1 or unexpected:
        raise _source_error(
            "expected one capture, one capture.json, and no other direct entries; "
            f"found captures={len(captures)}, metadata={len(metadata)}, unexpected={len(unexpected)}"
        )
    return ImportSource(
        directory=resolved,
        capture_path=captures[0].resolve(strict=True),
        metadata_path=metadata[0].resolve(strict=True),
    )


def _import_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"imported reference acquisition failed: {detail}",
        corrective_action="restore the original source and run, or select a fresh run.directory",
    )


def _path_identity(path: Path) -> FileIdentity:
    try:
        status = path.stat(follow_symlinks=False)
    except OSError as error:
        raise _import_error(f"could not inspect source path {path}: {error}") from error
    if not stat.S_ISREG(status.st_mode):
        raise _import_error(f"source path is no longer a regular file: {path}")
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _check_deadline(deadline: float, clock: Callable[[], float]) -> None:
    if clock() >= deadline:
        raise DeadlineExceededError(
            "imported reference acquisition failed: capture.total_timeout_seconds expired",
            corrective_action="increase capture.total_timeout_seconds and retry import-run",
        )


def _deadline(prepared: PreparedExperiment, clock: Callable[[], float]) -> float:
    started = clock()
    try:
        deadline = started + prepared.config.capture.total_timeout_seconds
    except ArithmeticError as error:
        raise _import_error("could not calculate the import deadline") from error
    if not math.isfinite(started) or not math.isfinite(deadline) or deadline <= started:
        raise _import_error("could not calculate a finite future import deadline")
    return deadline


def _copy_snapshot(source: Path, destination: Path, *, deadline: float, clock: Callable[[], float]) -> None:
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            while True:
                _check_deadline(deadline, clock)
                chunk = input_stream.read(_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        _check_deadline(deadline, clock)
    except TrafficlabError:
        raise
    except OSError as error:
        raise _import_error(f"could not snapshot source file {source}: {error}") from error


def _source_identities(source: ImportSource) -> tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity]:
    return (
        identify_file(source.capture_path),
        identify_file(source.metadata_path),
        _path_identity(source.capture_path),
        _path_identity(source.metadata_path),
    )


def _require_unchanged_source(
    source: ImportSource,
    expected: tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity],
) -> None:
    if discover_import_source(source.directory) != source or _source_identities(source) != expected:
        raise _import_error("supplied source changed during import")


def _lineage_record(
    source: ImportSource,
    prepared: PreparedExperiment,
    *,
    source_identities: tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity],
    packet_count: int,
    reused: bool,
) -> dict[str, object]:
    source_capture, source_metadata, capture_file, metadata_file = source_identities
    run_directory = prepared.run_directory
    return {
        "capture_identity": identify_file(run_directory / "capture.json").as_dict(),
        "event": "reference_imported",
        "experiment_identity": identify_bytes(render_effective_config(prepared.config)).as_dict(),
        "normalization_version": _NORMALIZATION_VERSION,
        "packet_count": packet_count,
        "path": str(run_directory / "reference.pcapng"),
        "reference_identity": identify_file(run_directory / "reference.pcapng").as_dict(),
        "reused": reused,
        "source_capture_file_identity": list(capture_file),
        "source_capture_identity": source_capture.as_dict(),
        "source_capture_path": str(source.capture_path),
        "source_metadata_file_identity": list(metadata_file),
        "source_metadata_identity": source_metadata.as_dict(),
        "source_metadata_path": str(source.metadata_path),
        "stage": "capture",
    }


def _validate_prepared_import(prepared: PreparedExperiment) -> None:
    if type(prepared) is not PreparedExperiment:
        raise TypeError("prepared must be a PreparedExperiment")
    if prepared.run_directory != prepared.config.run.directory or not prepared.run_directory.is_absolute():
        raise _import_error("prepared run directory does not match the effective configuration")
    try:
        snapshot = (prepared.run_directory / "experiment.toml").read_bytes()
    except OSError as error:
        raise _import_error(f"could not read the prepared experiment snapshot: {error}") from error
    if snapshot != render_effective_config(prepared.config):
        raise _import_error("prepared effective configuration changed")


def _canonical_pair_presence(run_directory: Path) -> tuple[bool, bool]:
    return ((run_directory / "capture.json").exists(), (run_directory / "reference.pcapng").exists())


def _reuse_error(detail: str) -> TrafficlabError:
    return _import_error(f"existing artifacts are not an exact imported-reference reuse: {detail}")


def _canonical_identities(
    run_directory: Path,
) -> tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity]:
    metadata_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    metadata_file = file_identity(metadata_path)
    reference_file = file_identity(reference_path)
    if metadata_file is None or reference_file is None:
        raise _reuse_error("the canonical capture pair is incomplete")
    return (identify_file(metadata_path), identify_file(reference_path), metadata_file, reference_file)


def _read_import_lineage(run_directory: Path) -> list[dict[str, object]]:
    try:
        content = (run_directory / "run.log").read_bytes().decode("utf-8", errors="strict")
        if not content.endswith("\n"):
            raise ValueError("run.log is not newline terminated")
        values = [json.loads(line) for line in content.splitlines()]
        if any(type(value) is not dict for value in values):
            raise TypeError("run.log record is not an object")
        return [value for value in values if value.get("event") == "reference_imported"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _reuse_error(f"could not read canonical import lineage: {error}") from error


def _reuse_import(
    source: ImportSource,
    prepared: PreparedExperiment,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CaptureResult:
    source_identities = _source_identities(source)
    output_identities = _canonical_identities(prepared.run_directory)
    _check_deadline(deadline, clock)
    try:
        inspection = validate_capture_pair(
            prepared.run_directory / "capture.json",
            prepared.run_directory / "reference.pcapng",
            deadline=deadline,
            clock=clock,
        )
    except DeadlineExceededError:
        raise
    except (OSError, TrafficlabError) as error:
        raise _reuse_error(f"the canonical capture pair is invalid: {error}") from error
    _require_unchanged_source(source, source_identities)
    if _canonical_identities(prepared.run_directory) != output_identities:
        raise _reuse_error("the canonical capture pair changed during validation")

    current_publication = _lineage_record(
        source,
        prepared,
        source_identities=source_identities,
        packet_count=inspection.packet_count,
        reused=False,
    )
    records = _read_import_lineage(prepared.run_directory)
    publications = [record for record in records if record.get("reused") is False]
    reuses = [record for record in records if record.get("reused") is True]
    if len(publications) != 1 or len(publications) + len(reuses) != len(records):
        raise _reuse_error("lineage must contain exactly one authoritative publication")
    if publications[0] != current_publication:
        raise _reuse_error("authoritative publication lineage does not match current identities")
    current_reuse = {**current_publication, "reused": True}
    if any(record != current_reuse for record in reuses):
        raise _reuse_error("a retained reuse record contradicts the authoritative publication")

    _require_unchanged_source(source, source_identities)
    if _canonical_identities(prepared.run_directory) != output_identities:
        raise _reuse_error("the canonical capture pair changed before reuse logging")
    append_run_log(prepared.run_directory, current_reuse)
    return CaptureResult(
        run_directory=prepared.run_directory,
        reference_path=prepared.run_directory / "reference.pcapng",
        packet_count=inspection.packet_count,
        target_status=0,
        reused=True,
    )


def _fresh_import(
    source: ImportSource,
    prepared: PreparedExperiment,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CaptureResult:
    run_directory = prepared.run_directory
    expected = _source_identities(source)
    _check_deadline(deadline, clock)
    temporary_root = Path(tempfile.mkdtemp(dir=run_directory, prefix=".import-reference."))
    try:
        capture_snapshot = temporary_root / "source.capture"
        metadata_snapshot = temporary_root / "capture.json"
        normalized = temporary_root / "reference.pcapng"
        _copy_snapshot(source.capture_path, capture_snapshot, deadline=deadline, clock=clock)
        _copy_snapshot(source.metadata_path, metadata_snapshot, deadline=deadline, clock=clock)
        _require_unchanged_source(source, expected)
        if (identify_file(capture_snapshot), identify_file(metadata_snapshot)) != expected[:2]:
            raise _import_error("owned source snapshot does not match the supplied files")

        load_capture_metadata(metadata_snapshot)
        _check_deadline(deadline, clock)
        normalization = normalize_raw_capture(capture_snapshot, normalized, deadline=deadline, clock=clock)
        inspection = validate_capture_pair(metadata_snapshot, normalized, deadline=deadline, clock=clock)
        if inspection.packet_count != normalization.packet_count:
            raise _import_error("normalized packet count changed during capture-pair validation")
        _require_unchanged_source(source, expected)

        publication = publish_capture_pair(
            metadata_snapshot,
            normalized,
            run_directory,
            target_success=True,
            deadline=deadline,
            clock=clock,
        )
        if not publication.created_by_call:
            raise _import_error("canonical capture pair appeared during imported publication")
        if publication.inspection.packet_count != normalization.packet_count:
            raise _import_error("published packet count differs from normalized output")
        _require_unchanged_source(source, expected)
        append_run_log(
            run_directory,
            _lineage_record(
                source,
                prepared,
                source_identities=expected,
                packet_count=normalization.packet_count,
                reused=False,
            ),
        )
        return CaptureResult(
            run_directory=run_directory,
            reference_path=run_directory / "reference.pcapng",
            packet_count=normalization.packet_count,
            target_status=0,
            reused=False,
        )
    finally:
        try:
            shutil.rmtree(temporary_root)
        except OSError as error:
            raise _import_error(f"could not remove owned temporary directory {temporary_root}: {error}") from error


def import_reference(
    source: ImportSource,
    prepared: PreparedExperiment,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> CaptureResult:
    """Publish or exactly reuse a supplied reference capture pair."""
    if type(source) is not ImportSource:
        raise TypeError("source must be an ImportSource")
    _validate_prepared_import(prepared)
    source = discover_import_source(source.directory)
    presence = _canonical_pair_presence(prepared.run_directory)
    deadline = _deadline(prepared, clock)
    if presence == (True, True):
        return _reuse_import(source, prepared, deadline=deadline, clock=clock)
    if presence != (False, False):
        raise _reuse_error("the canonical capture pair is incomplete")
    return _fresh_import(source, prepared, deadline=deadline, clock=clock)


def _require_separate_directories(source_directory: Path, run_directory: Path) -> None:
    source_resolved = source_directory.resolve()
    run_resolved = run_directory.resolve()
    if (
        source_resolved == run_resolved
        or source_resolved.is_relative_to(run_resolved)
        or run_resolved.is_relative_to(source_resolved)
    ):
        raise _source_error(f"supplied directory {source_resolved} and run.directory {run_resolved} overlap")


def _config_only_preflight(path: Path) -> PreparedExperiment:
    return run_preflight(path, config_only=True)


def run_imported_experiment(experiment_path: Path, dump_directory: Path) -> RunResult:
    """Run the ordinary pipeline with strict imported-reference acquisition."""
    source = discover_import_source(dump_directory)
    preliminary = load_configuration_pair(experiment_path)
    _require_separate_directories(source.directory, preliminary.realized.run.directory)

    def checked_preflight(path: Path) -> PreparedExperiment:
        current = load_configuration_pair(path)
        _require_separate_directories(source.directory, current.realized.run.directory)
        if current != preliminary:
            raise _import_error("experiment configuration changed during imported preflight")
        prepared = _config_only_preflight(path)
        if prepared.config != preliminary.realized or prepared.portable_config != preliminary.portable:
            raise _import_error("experiment configuration changed during imported preflight")
        return prepared

    def import_capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
        return import_reference(source, prepared)

    dependencies = RunDependencies(
        preflight=checked_preflight,
        capture=import_capture,
        fit=fit_experiment,
        generate=generate_experiment,
        compare=compare_experiment,
    )
    return run_experiment(experiment_path, dependencies=dependencies)
