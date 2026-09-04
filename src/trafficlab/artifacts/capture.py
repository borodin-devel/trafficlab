"""Capture-pair recovery, publication, diagnostics, and rollback."""

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from trafficlab.artifacts.io import FileIdentity, file_identity, fsync_published_artifact, is_file_identity
from trafficlab.capture.validation import CaptureInspection, validate_capture_pair
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError

_CAPTURE_COPY_CHUNK_SIZE = 64 * 1024


def _capture_copy_deadline(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() >= deadline:
        raise DeadlineExceededError(
            "capture publication copy exceeded its absolute deadline",
            corrective_action="increase the total run timeout and retry capture",
        )


def _copy_capture_temporary(
    source: Path,
    run_directory: Path,
    *,
    label: str,
    deadline: float | None,
    clock: Callable[[], float],
) -> Path:
    temporary_path: Path | None = None
    try:
        with (
            source.open("rb") as input_stream,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=run_directory,
                prefix=f".capture-pair.{label}.",
                suffix=".tmp",
                delete=False,
            ) as output_stream,
        ):
            temporary_path = Path(output_stream.name)
            while True:
                _capture_copy_deadline(deadline, clock)
                chunk = input_stream.read(_CAPTURE_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        _capture_copy_deadline(deadline, clock)
    except KeyboardInterrupt as error:
        cleanup_detail = _unlink_capture_temporary(temporary_path)
        if cleanup_detail is not None:
            raise TrafficlabError(
                f"capture publication was interrupted; cleanup incomplete: {cleanup_detail}",
                corrective_action="remove the owned temporary capture file and retry when ready",
            ) from error
        raise
    except (OSError, TrafficlabError) as error:
        cleanup_detail = _unlink_capture_temporary(temporary_path)
        if isinstance(error, TrafficlabError):
            if cleanup_detail is None:
                raise
            raise TrafficlabError(
                f"{error}; cleanup incomplete: {cleanup_detail}",
                corrective_action=error.corrective_action,
            ) from error
        detail = f"could not prepare capture artifact from {source}: {error}"
        if cleanup_detail is not None:
            detail = f"{detail}; cleanup incomplete: {cleanup_detail}"
        raise TrafficlabError(
            detail,
            corrective_action="verify the capture files and run directory are readable and writable",
        ) from error
    return temporary_path


def _unlink_capture_temporary(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        os.unlink(path)
    except OSError as error:
        return f"could not remove owned temporary file {path}: {error}"
    return None


type CapturePairIdentity = tuple[FileIdentity | None, FileIdentity | None]


@dataclass(frozen=True, slots=True)
class CapturePublication:
    """Inspection plus exact ownership evidence for one capture publication call."""

    inspection: CaptureInspection
    created_by_call: bool
    owned_identity: CapturePairIdentity | None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.inspection) is not CaptureInspection:
            raise TypeError("inspection must be a CaptureInspection")
        if type(self.created_by_call) is not bool:
            raise TypeError("created_by_call must be a boolean")
        if type(self.warnings) is not tuple:
            raise TypeError("warnings must be a tuple")
        if not all(type(warning) is str and warning.strip() for warning in self.warnings):
            raise ValueError("warnings must contain nonempty strings")
        if self.created_by_call:
            identity = self.owned_identity
            if (
                type(identity) is not tuple
                or len(identity) != 2
                or not all(is_file_identity(item) for item in identity)
            ):
                raise ValueError("a created publication requires an exact owned_identity pair")
        elif self.owned_identity is not None:
            raise ValueError("a reused publication cannot carry an owned_identity")


def _capture_pair_identity(metadata_path: Path, pcapng_path: Path) -> CapturePairIdentity:
    return (file_identity(metadata_path), file_identity(pcapng_path))


def _recovery_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        detail,
        corrective_action="preserve the reported recovery artifacts, validate them, and retry capture if needed",
    )


def _restore_quarantined_artifact(canonical_path: Path, quarantine_path: Path, *, reason: str) -> None:
    try:
        os.link(quarantine_path, canonical_path)
    except FileExistsError as error:
        raise _recovery_error(
            f"{reason}; canonical path {canonical_path} is occupied; moved artifact preserved at {quarantine_path}"
        ) from error
    except OSError as error:
        raise _recovery_error(
            f"{reason}; could not restore {canonical_path}: {error}; moved artifact preserved at {quarantine_path}"
        ) from error

    try:
        os.unlink(quarantine_path)
    except OSError as error:
        raise _recovery_error(
            f"{reason}; restored {canonical_path}, but could not remove recovery link {quarantine_path}: {error}"
        ) from error

    try:
        quarantine_path.parent.rmdir()
    except OSError as error:
        raise _recovery_error(
            f"{reason}; restored {canonical_path}, but could not remove recovery directory "
            f"{quarantine_path.parent}: {error}"
        ) from error
    raise _recovery_error(reason)


def _recover_failed_capture_pair(
    metadata_path: Path,
    pcapng_path: Path,
    expected_identity: CapturePairIdentity,
) -> None:
    paths = (metadata_path, pcapng_path)
    current_identity = _capture_pair_identity(*paths)
    if current_identity != expected_identity:
        raise _recovery_error("capture pair changed during invalid-pair recovery")

    try:
        quarantine_directory = Path(tempfile.mkdtemp(dir=metadata_path.parent, prefix=".capture-recovery."))
    except OSError as error:
        raise TrafficlabError(
            f"could not create capture recovery quarantine in {metadata_path.parent}: {error}",
            corrective_action="verify the run directory is writable and retry capture",
        ) from error

    errors: list[str] = []
    quarantine_retained = False
    for index, (path, expected) in enumerate(zip(paths, expected_identity, strict=True)):
        if expected is None:
            continue
        quarantine_path = quarantine_directory / f"{index}-{path.name}"
        try:
            os.rename(path, quarantine_path)
        except OSError as error:
            errors.append(f"could not move capture artifact {path} into recovery quarantine: {error}")
            break
        moved_identity = file_identity(quarantine_path)
        if moved_identity != expected:
            _restore_quarantined_artifact(
                path,
                quarantine_path,
                reason="capture pair changed during invalid-pair recovery",
            )
        try:
            os.unlink(quarantine_path)
        except OSError as error:
            errors.append(f"could not remove creator-owned recovery artifact {quarantine_path}: {error}")
            quarantine_retained = True
            break

    if not quarantine_retained:
        try:
            quarantine_directory.rmdir()
        except OSError as error:
            errors.append(f"could not remove creator-owned recovery directory {quarantine_directory}: {error}")
    if errors:
        raise TrafficlabError(
            "; ".join(errors),
            corrective_action="repair the exact failed capture artifact paths and retry capture",
        )


def _existing_capture(
    metadata_path: Path,
    pcapng_path: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float],
) -> CaptureInspection | None:
    identity = _capture_pair_identity(metadata_path, pcapng_path)
    if identity == (None, None):
        return None
    if all(item is not None for item in identity):
        try:
            inspection = validate_capture_pair(metadata_path, pcapng_path, deadline=deadline, clock=clock)
        except DeadlineExceededError:
            raise
        except OSError as error:
            raise TrafficlabError(
                f"could not read capture pair for reuse: {error}",
                corrective_action="verify the exact capture artifact paths are readable and retry capture",
            ) from error
        except TrafficlabError:
            pass
        else:
            if _capture_pair_identity(metadata_path, pcapng_path) != identity:
                raise _recovery_error("capture pair changed during valid-pair validation")
            return inspection
    _recover_failed_capture_pair(metadata_path, pcapng_path, identity)
    return None


def load_or_recover_capture_pair(
    run_directory: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> CaptureInspection | None:
    """Reuse a stable valid capture pair or remove only a stable invalid pair."""
    return _existing_capture(
        run_directory / "capture.json",
        run_directory / "reference.pcapng",
        deadline=deadline,
        clock=clock,
    )


def remove_stable_capture_diagnostics(run_directory: Path) -> None:
    """Remove only the unchanged stable diagnostic capture identities."""
    metadata_path = run_directory / "diagnostic-capture.json"
    pcapng_path = run_directory / "diagnostic-reference.pcapng"
    identity = _capture_pair_identity(metadata_path, pcapng_path)
    if identity == (None, None):
        return
    _recover_failed_capture_pair(metadata_path, pcapng_path, identity)


def _capture_publication_error(error: Exception, destination: Path, cleanup_details: list[str]) -> TrafficlabError:
    if isinstance(error, FileExistsError):
        detail = f"capture artifact already exists: {destination}"
        action = "preserve the existing artifact and retry capture in a new run directory"
    elif isinstance(error, TrafficlabError):
        detail = str(error)
        action = error.corrective_action
    else:
        detail = f"could not publish capture artifact {destination}: {error}"
        action = "verify the run directory is writable and has available space"
    if cleanup_details:
        detail = f"{detail}; cleanup incomplete: {'; '.join(cleanup_details)}"
    error_type = DeadlineExceededError if isinstance(error, DeadlineExceededError) else TrafficlabError
    outcomes = error.failure_outcomes if isinstance(error, TrafficlabError) and error.failure_outcomes else None
    return error_type(detail, corrective_action=action, failure_outcomes=outcomes)


def publish_capture_pair(
    source_metadata_path: Path,
    source_pcapng_path: Path,
    run_directory: Path,
    *,
    target_success: bool,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
    recover_invalid: bool = True,
) -> CapturePublication:
    """Validate and exclusively publish a reusable or diagnostic capture pair."""
    if type(target_success) is not bool:
        raise TrafficlabError(
            "target_success must be a boolean",
            corrective_action="report whether the target exited successfully",
        )

    final_metadata = run_directory / "capture.json"
    final_pcapng = run_directory / "reference.pcapng"
    if target_success:
        if recover_invalid:
            existing = _existing_capture(final_metadata, final_pcapng, deadline=deadline, clock=clock)
            if existing is not None:
                return CapturePublication(inspection=existing, created_by_call=False, owned_identity=None)
        elif _capture_pair_identity(final_metadata, final_pcapng) != (None, None):
            raise TrafficlabError(
                "capture artifact already exists during exclusive publication",
                corrective_action="preserve the existing artifact and retry in a new run directory",
            )
        destinations = (final_metadata, final_pcapng)
    else:
        existing_identity = _capture_pair_identity(final_metadata, final_pcapng)
        _recover_failed_capture_pair(final_metadata, final_pcapng, existing_identity)
        destinations = (
            run_directory / "diagnostic-capture.json",
            run_directory / "diagnostic-reference.pcapng",
        )

    temporary_paths: list[Path] = []
    current_destination = destinations[0]
    try:
        temporary_metadata = _copy_capture_temporary(
            source_metadata_path,
            run_directory,
            label="metadata",
            deadline=deadline,
            clock=clock,
        )
        temporary_paths.append(temporary_metadata)
        temporary_pcapng = _copy_capture_temporary(
            source_pcapng_path,
            run_directory,
            label="pcapng",
            deadline=deadline,
            clock=clock,
        )
        temporary_paths.append(temporary_pcapng)
        inspection = validate_capture_pair(
            temporary_metadata,
            temporary_pcapng,
            deadline=deadline,
            clock=clock,
        )
        owned_identity = _capture_pair_identity(*temporary_paths)
        for temporary_path, destination in zip(temporary_paths, destinations, strict=True):
            current_destination = destination
            os.link(temporary_path, destination)
        if _capture_pair_identity(*destinations) != owned_identity:
            raise _recovery_error("capture pair changed during publication")
        fsync_published_artifact(
            destinations[-1],
            stage="capture",
            affected_evidence="capture pair",
        )
    except KeyboardInterrupt as error:
        cleanup_details = [
            detail
            for temporary_path in temporary_paths
            if (detail := _unlink_capture_temporary(temporary_path)) is not None
        ]
        if cleanup_details:
            raise TrafficlabError(
                f"capture publication was interrupted; cleanup incomplete: {'; '.join(cleanup_details)}",
                corrective_action="remove the owned temporary capture files and retry when ready",
            ) from error
        raise
    except Exception as error:
        cleanup_details = [
            detail
            for temporary_path in temporary_paths
            if (detail := _unlink_capture_temporary(temporary_path)) is not None
        ]
        raise _capture_publication_error(error, current_destination, cleanup_details) from error

    cleanup_details = [
        detail
        for temporary_path in temporary_paths
        if (detail := _unlink_capture_temporary(temporary_path)) is not None
    ]
    return CapturePublication(
        inspection=inspection,
        created_by_call=target_success,
        owned_identity=owned_identity if target_success else None,
        warnings=tuple(cleanup_details),
    )


def rollback_capture_publication(run_directory: Path, publication: CapturePublication) -> None:
    """Withdraw only the unchanged reusable pair proven to be owned by this publication call."""
    if type(publication) is not CapturePublication:
        raise TypeError("publication must be a CapturePublication")
    if not publication.created_by_call:
        return
    assert publication.owned_identity is not None
    _recover_failed_capture_pair(
        run_directory / "capture.json",
        run_directory / "reference.pcapng",
        publication.owned_identity,
    )
