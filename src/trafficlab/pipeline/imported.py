# pyright: reportPrivateUsage=false
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
from typing import TYPE_CHECKING, cast

from trafficlab.artifacts.capture import CapturePublication, publish_capture_pair
from trafficlab.artifacts.io import FileIdentity, append_run_log, file_identity
from trafficlab.capture.types import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError
from trafficlab.common.scapy_io import normalize_raw_capture
from trafficlab.common.trace import parse_capture_metadata
from trafficlab.pipeline.imported_io import (
    _check_deadline,
    _identify_file_deadline,
    _path_identity,
    _path_state,
    _read_bytes_deadline,
)
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.types import PreparedExperiment

if TYPE_CHECKING:
    from trafficlab.comparison.schema import ComparisonResult
    from trafficlab.fitting.stage import FitStageResult
    from trafficlab.generation.stage import GenerationStageResult

_NORMALIZATION_VERSION = "scapy-raw-v1"
_COPY_CHUNK_SIZE = 1024 * 1024
type _PathState = tuple[int, int, int, int, int, int]


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
    initial_states: dict[str, _PathState] = {}
    for entry in entries:
        try:
            entry_status = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise _source_error(f"could not inspect direct entry {entry}: {error}") from error
        initial_states[entry.name] = _path_state(entry_status)
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
    try:
        resolved_capture = captures[0].resolve(strict=True)
        resolved_metadata = metadata[0].resolve(strict=True)
        final_directory_status = resolved.stat(follow_symlinks=False)
        final_entries = sorted(resolved.iterdir(), key=lambda path: path.name.encode("utf-8"))
        final_states = {entry.name: _path_state(entry.stat(follow_symlinks=False)) for entry in final_entries}
    except (OSError, UnicodeEncodeError) as error:
        raise _source_error(f"directory changed during discovery: {error}") from error
    if (
        resolved_capture.parent != resolved
        or resolved_metadata.parent != resolved
        or _path_state(directory_status) != _path_state(final_directory_status)
        or [entry.name for entry in final_entries] != [entry.name for entry in entries]
        or final_states != initial_states
    ):
        raise _source_error("directory or direct entry changed during discovery")
    return ImportSource(directory=resolved, capture_path=resolved_capture, metadata_path=resolved_metadata)


def _import_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"imported reference acquisition failed: {detail}",
        corrective_action="restore the original source and run, or select a fresh run.directory",
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


def _source_identities(
    source: ImportSource, *, deadline: float, clock: Callable[[], float]
) -> tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity]:
    before = (_path_identity(source.capture_path), _path_identity(source.metadata_path))
    content = (
        _identify_file_deadline(source.capture_path, deadline=deadline, clock=clock, kind="source capture"),
        _identify_file_deadline(source.metadata_path, deadline=deadline, clock=clock, kind="source metadata"),
    )
    if (_path_identity(source.capture_path), _path_identity(source.metadata_path)) != before:
        raise _import_error("supplied source changed during content identity")
    return (*content, *before)


def _require_unchanged_source(
    source: ImportSource,
    expected: tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity],
    *,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    if (
        discover_import_source(source.directory) != source
        or _source_identities(source, deadline=deadline, clock=clock) != expected
    ):
        raise _import_error("supplied source changed during import")


def _lineage_record(
    source: ImportSource,
    prepared: PreparedExperiment,
    *,
    source_identities: tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity],
    output_identities: tuple[ContentIdentity, ContentIdentity],
    packet_count: int,
    reused: bool,
) -> dict[str, object]:
    source_capture, source_metadata, capture_file, metadata_file = source_identities
    capture_identity, reference_identity = output_identities
    run_directory = prepared.run_directory
    return {
        "capture_identity": capture_identity.as_dict(),
        "event": "reference_imported",
        "experiment_identity": identify_bytes(render_effective_config(prepared.config)).as_dict(),
        "normalization_version": _NORMALIZATION_VERSION,
        "packet_count": packet_count,
        "path": str(run_directory / "reference.pcapng"),
        "reference_identity": reference_identity.as_dict(),
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
    presence: list[bool] = []
    for path in (run_directory / "capture.json", run_directory / "reference.pcapng"):
        try:
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            presence.append(False)
        except OSError as error:
            raise _reuse_error(f"could not inspect canonical capture entry {path}: {error}") from error
        else:
            presence.append(True)
    return (presence[0], presence[1])


def _reuse_error(detail: str) -> TrafficlabError:
    return _import_error(f"existing artifacts are not an exact imported-reference reuse: {detail}")


def _canonical_identities(
    run_directory: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[ContentIdentity, ContentIdentity, FileIdentity, FileIdentity]:
    metadata_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    for path in (metadata_path, reference_path):
        try:
            status = path.stat(follow_symlinks=False)
        except OSError as error:
            raise _reuse_error(f"could not inspect canonical capture entry {path}: {error}") from error
        if not stat.S_ISREG(status.st_mode):
            raise _reuse_error(f"canonical capture entry is not a regular file: {path}")
    metadata_file = file_identity(metadata_path)
    reference_file = file_identity(reference_path)
    if metadata_file is None or reference_file is None:
        raise _reuse_error("the canonical capture pair is incomplete")
    content_identities = (
        _identify_file_deadline(metadata_path, deadline=deadline, clock=clock, kind="published metadata"),
        _identify_file_deadline(reference_path, deadline=deadline, clock=clock, kind="published reference"),
    )
    if (file_identity(metadata_path), file_identity(reference_path)) != (metadata_file, reference_file):
        raise _reuse_error("the canonical capture pair changed during content identity")
    return (*content_identities, metadata_file, reference_file)


def _require_owned_publication(
    run_directory: Path,
    publication: CapturePublication,
    expected_content: tuple[ContentIdentity, ContentIdentity],
    *,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    owned = publication.owned_identity
    if owned is None:
        raise _import_error("published capture pair has no creator-owned identity")
    current = _canonical_identities(run_directory, deadline=deadline, clock=clock)
    if current[:2] != expected_content or current[2:] != owned:
        raise _import_error("canonical capture pair changed after publication")


def _read_import_lineage(
    run_directory: Path, *, deadline: float, clock: Callable[[], float]
) -> list[dict[str, object]]:
    try:
        content = _read_bytes_deadline(
            run_directory / "run.log", deadline=deadline, clock=clock, kind="import lineage"
        ).decode("utf-8", errors="strict")
        if not content.endswith("\n"):
            raise ValueError("run.log is not newline terminated")
        values: list[object] = []
        for line in content.splitlines():
            _check_deadline(deadline, clock)
            values.append(json.loads(line))
        _check_deadline(deadline, clock)
        if any(type(value) is not dict for value in values):
            raise TypeError("run.log record is not an object")
        documents = cast(list[dict[str, object]], values)
        return [value for value in documents if value.get("event") == "reference_imported"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _reuse_error(f"could not read canonical import lineage: {error}") from error


def _reuse_import(
    source: ImportSource,
    prepared: PreparedExperiment,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CaptureResult:
    source_identities = _source_identities(source, deadline=deadline, clock=clock)
    output_identities = _canonical_identities(prepared.run_directory, deadline=deadline, clock=clock)
    _check_deadline(deadline, clock)
    try:
        metadata_content = _read_bytes_deadline(
            prepared.run_directory / "capture.json",
            deadline=deadline,
            clock=clock,
            kind="published metadata",
        )
        _check_deadline(deadline, clock)
        parse_capture_metadata(metadata_content, source=prepared.run_directory / "capture.json")
        _check_deadline(deadline, clock)
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
    _require_unchanged_source(source, source_identities, deadline=deadline, clock=clock)
    if _canonical_identities(prepared.run_directory, deadline=deadline, clock=clock) != output_identities:
        raise _reuse_error("the canonical capture pair changed during validation")

    current_publication = _lineage_record(
        source,
        prepared,
        source_identities=source_identities,
        output_identities=output_identities[:2],
        packet_count=inspection.packet_count,
        reused=False,
    )
    records = _read_import_lineage(prepared.run_directory, deadline=deadline, clock=clock)
    publications = [record for record in records if record.get("reused") is False]
    reuses = [record for record in records if record.get("reused") is True]
    if len(publications) != 1 or len(publications) + len(reuses) != len(records):
        raise _reuse_error("lineage must contain exactly one authoritative publication")
    if publications[0] != current_publication:
        raise _reuse_error("authoritative publication lineage does not match current identities")
    current_reuse = {**current_publication, "reused": True}
    if any(record != current_reuse for record in reuses):
        raise _reuse_error("a retained reuse record contradicts the authoritative publication")

    _require_unchanged_source(source, source_identities, deadline=deadline, clock=clock)
    if _canonical_identities(prepared.run_directory, deadline=deadline, clock=clock) != output_identities:
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
    if _read_import_lineage(run_directory, deadline=deadline, clock=clock):
        raise _reuse_error("retained import lineage forbids a second authoritative publication")
    expected = _source_identities(source, deadline=deadline, clock=clock)
    _check_deadline(deadline, clock)
    temporary_root = Path(tempfile.mkdtemp(dir=run_directory, prefix=".import-reference."))
    try:
        capture_snapshot = temporary_root / "source.capture"
        metadata_snapshot = temporary_root / "capture.json"
        normalized = temporary_root / "reference.pcapng"
        _copy_snapshot(source.capture_path, capture_snapshot, deadline=deadline, clock=clock)
        _copy_snapshot(source.metadata_path, metadata_snapshot, deadline=deadline, clock=clock)
        _require_unchanged_source(source, expected, deadline=deadline, clock=clock)
        if (
            _identify_file_deadline(capture_snapshot, deadline=deadline, clock=clock, kind="capture snapshot"),
            _identify_file_deadline(metadata_snapshot, deadline=deadline, clock=clock, kind="metadata snapshot"),
        ) != expected[:2]:
            raise _import_error("owned source snapshot does not match the supplied files")

        metadata_content = _read_bytes_deadline(
            metadata_snapshot,
            deadline=deadline,
            clock=clock,
            kind="metadata snapshot",
        )
        _check_deadline(deadline, clock)
        parse_capture_metadata(metadata_content, source=metadata_snapshot)
        _check_deadline(deadline, clock)
        normalization = normalize_raw_capture(capture_snapshot, normalized, deadline=deadline, clock=clock)
        inspection = validate_capture_pair(metadata_snapshot, normalized, deadline=deadline, clock=clock)
        if inspection.packet_count != normalization.packet_count:
            raise _import_error("normalized packet count changed during capture-pair validation")
        _require_unchanged_source(source, expected, deadline=deadline, clock=clock)

        if _read_import_lineage(run_directory, deadline=deadline, clock=clock):
            raise _reuse_error("retained import lineage forbids a second authoritative publication")
        output_content = (
            _identify_file_deadline(metadata_snapshot, deadline=deadline, clock=clock, kind="metadata snapshot"),
            _identify_file_deadline(normalized, deadline=deadline, clock=clock, kind="normalized reference"),
        )
        publication = publish_capture_pair(
            metadata_snapshot,
            normalized,
            run_directory,
            target_success=True,
            deadline=deadline,
            clock=clock,
            recover_invalid=False,
        )
        if not publication.created_by_call:
            raise _import_error("canonical capture pair appeared during imported publication")
        if publication.inspection.packet_count != normalization.packet_count:
            raise _import_error("published packet count differs from normalized output")
        _require_owned_publication(
            run_directory,
            publication,
            output_content,
            deadline=deadline,
            clock=clock,
        )
        if _read_import_lineage(run_directory, deadline=deadline, clock=clock):
            raise _reuse_error("retained import lineage forbids a second authoritative publication")
        _require_unchanged_source(source, expected, deadline=deadline, clock=clock)
        record = _lineage_record(
            source,
            prepared,
            source_identities=expected,
            output_identities=output_content,
            packet_count=normalization.packet_count,
            reused=False,
        )
        _require_owned_publication(
            run_directory,
            publication,
            output_content,
            deadline=deadline,
            clock=clock,
        )
        append_run_log(run_directory, record)
        _require_owned_publication(
            run_directory,
            publication,
            output_content,
            deadline=deadline,
            clock=clock,
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
    from trafficlab.preflight.stage import run_preflight

    return run_preflight(path, config_only=True)


def _fit_experiment(path: Path) -> FitStageResult:
    from trafficlab.fitting.stage import fit_experiment

    return fit_experiment(path)


def _generate_experiment(path: Path) -> GenerationStageResult:
    from trafficlab.generation.stage import generate_experiment

    return generate_experiment(path)


def _compare_experiment(path: Path) -> ComparisonResult:
    from trafficlab.comparison.stage import compare_experiment

    return compare_experiment(path)


def _run_experiment(experiment_path: Path, *, dependencies: RunDependencies) -> RunResult:
    from trafficlab.pipeline.stage import run_experiment

    return run_experiment(experiment_path, dependencies=dependencies)


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
        return _config_only_preflight(path)

    def import_capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
        if prepared.config != preliminary.realized or prepared.portable_config != preliminary.portable:
            raise _import_error("experiment configuration changed during imported preflight")
        return import_reference(source, prepared)

    dependencies = RunDependencies(
        preflight=checked_preflight,
        capture=import_capture,
        fit=_fit_experiment,
        generate=_generate_experiment,
        compare=_compare_experiment,
    )
    return _run_experiment(experiment_path, dependencies=dependencies)
