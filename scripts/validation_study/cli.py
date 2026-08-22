"""Cli owner for Validation Study tooling."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.collection import (
    PhaseCaptureImage,
    collect_validation_candidate,
    collection_inputs_from_prerequisites,
    remove_owned_phase_capture_image,
)
from scripts.validation_study.common import (
    PRIMARY_ORDER,
    REPOSITORY_ROOT,
    path_entry_exists,
    phase_capture_tag,
    repository_relative_path,
    require,
    validate_endpoint_url,
    validate_study_id,
)
from scripts.validation_study.evidence import extract_primary_record, repository_path_record
from scripts.validation_study.prerequisites.commands import timestamp_now
from scripts.validation_study.prerequisites.run import run_prerequisites
from scripts.validation_study.records import CommandRunner, StudyResults
from scripts.validation_study.results.codec import publish_results, validate_run_document, validate_study_document
from scripts.validation_study.results.reporting import (
    natural_variation,
    study_document,
    study_run_document,
    workload_summaries,
)
from scripts.validation_study.results.reproduction import (
    environment_record,
    load_reference_trace,
    primary_run_specs,
    protocol_record,
    publish_audited_bundle,
    run_cli_reproduction,
    validate_primary_derived_records,
    validated_study_inputs,
)
from scripts.validation_study.rotation.run import begin_phase_attempt
from scripts.validation_study.transfer import archive_transfer_evidence, best_effort_archive, prepare_transfer_scratch
from scripts.validation_study.workloads import config_with_run_directory, render_realized_config, workload_specs
from trafficlab.capture.stage import capture_experiment
from trafficlab.common.config import SimilarityConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import (
    TrafficTrace,
)
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunResult

if TYPE_CHECKING:
    from scripts.validation_study.common import HeldOutCaptureRunner, WorkloadName
    from scripts.validation_study.records import StudyRunRecord

DEFAULT_COMMAND_RUNNER: CommandRunner = cast(CommandRunner, subprocess.run)


def run_study(
    url: str,
    study_id: str,
    prerequisite_path: Path,
    *,
    repository_root: Path,
    run: Callable[[Path], RunResult],
    runner: CommandRunner,
    perf_counter: Callable[[], float],
    utc_now: Callable[[], datetime],
) -> StudyResults:
    root = repository_root.resolve()
    owned_capture_image = PhaseCaptureImage(tag="")
    primary: BaseException | None = None
    try:
        url = validate_endpoint_url(url)
        study_id = validate_study_id(study_id)
        owned_capture_image.tag = phase_capture_tag(study_id, "study")
        results_path = root / "examples" / "validation_study" / "results.json"
        require(not path_entry_exists(results_path), f"study result target already exists: {results_path}")
        with tempfile.TemporaryDirectory(prefix=f"trafficlab-validation-{study_id}-capture-") as temporary_directory:
            prerequisites, configs, identity, prerequisite_content = validated_study_inputs(
                url,
                study_id,
                prerequisite_path,
                repository_root=root,
                runner=runner,
                owned_capture_image=owned_capture_image,
                capture_iidfile=Path(temporary_directory) / "capture.iid",
            )
        specifications = primary_run_specs(root, study_id, configs)
        workloads = {spec.name: spec for spec in workload_specs(url)}
        object_size = cast(int, prerequisites.capability["object_size_bytes"])
        records: list[StudyRunRecord] = []
        traces: dict[tuple[WorkloadName, int], TrafficTrace] = {}
        settings: dict[WorkloadName, SimilarityConfig] = {}
        for spec in specifications:
            workload = workloads[spec.workload]
            prepared: Mapping[str, tuple[Path, int]] = {}
            try:
                config = config_with_run_directory(configs[spec.workload], spec.run_directory)
                render_realized_config(config, spec.config_path)
                prepared = prepare_transfer_scratch(root, study_id, spec.run_id, workload)
                started = perf_counter()
                result = run(spec.config_path)
                elapsed = perf_counter() - started
                responses = archive_transfer_evidence(
                    root, study_id, spec.run_id, workload, prepared, object_size_bytes=object_size
                )
                record = extract_primary_record(root, spec, workload, result, elapsed, responses)
                document = study_run_document(record)
                validate_run_document(
                    document,
                    expected=PRIMARY_ORDER[spec.execution_order - 1],
                    repository_root=root,
                    study_id=study_id,
                    object_size=object_size,
                )
                records.append(record)
                traces[spec.workload, spec.repeat] = load_reference_trace(spec.run_directory)
                settings[spec.workload] = config.similarity
            except Exception as error:
                archive_diagnostic = best_effort_archive(spec.transfer_evidence_directory, prepared)
                secondary = f"; secondary evidence archive failure: {archive_diagnostic}" if archive_diagnostic else ""
                raise TrafficlabError(
                    f"Validation Study primary failed for workload {spec.workload}, repeat {spec.repeat}, position {spec.execution_order}, raw run path {repository_path_record(spec.run_directory, repository_root=root, name='failed run path')}; restart with a new study ID: {error}{secondary}",
                    corrective_action="preserve the failed evidence and restart the balanced protocol with a new study ID",
                ) from error
        variation_values = natural_variation(records, traces, settings)
        summary_values = workload_summaries(records)
        validated_variation, validated_summaries = validate_primary_derived_records(
            records, variation_values, summary_values
        )
        reproduction = run_cli_reproduction(
            root,
            study_id,
            configs["streaming"],
            records[3],
            workloads["streaming"],
            object_size_bytes=object_size,
            runner=runner,
            perf_counter=perf_counter,
        )
        created = timestamp_now(utc_now)
        result = StudyResults(
            schema_version=1,
            environment=environment_record(prerequisites, identity, created),
            protocol=protocol_record(prerequisites, prerequisite_content),
            runs=tuple(records),
            natural_variation=validated_variation,
            workload_summaries=validated_summaries,
            reproduction=reproduction,
        )
        validated = validate_study_document(study_document(result), repository_root=root)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        publish_results(results_path, validated, repository_root=root)
        return validated
    except TrafficlabError as error:
        primary = error
        raise
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        primary = TrafficlabError(
            f"Validation Study failed validation: {error}",
            corrective_action="preserve the ignored evidence, correct the failure, and restart with a new study ID",
        )
        raise primary from error
    except BaseException as error:
        primary = error
        raise
    finally:
        if owned_capture_image.build_attempted:
            try:
                remove_owned_phase_capture_image(
                    owned_capture_image, phase="study", repository_root=root, runner=runner
                )
            except BaseException as cleanup_error:
                if primary is None:
                    raise TrafficlabError(
                        f"Validation Study study capture image cleanup failed: {cleanup_error}",
                        corrective_action="preserve the study evidence, remove the exact owned capture image tag, and restart with a new study ID",
                    ) from cleanup_error
                primary.add_note(f"study capture image cleanup failed: {cleanup_error}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_validation_study.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prerequisites = commands.add_parser("prerequisites")
    prerequisites.add_argument("--url", required=True)
    prerequisites.add_argument("--study-id", required=True)
    study_parser = commands.add_parser("study")
    study_parser.add_argument("--url", required=True)
    study_parser.add_argument("--study-id", required=True)
    study_parser.add_argument("--prerequisites", required=True, type=Path)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--url", required=True)
    collect_parser.add_argument("--study-id", required=True)
    collect_parser.add_argument("--prerequisites", required=True, type=Path)
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--candidate", required=True, type=Path)
    publish_parser.add_argument("--study-id", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    run: Callable[[Path], RunResult] = run_experiment,
    capture: HeldOutCaptureRunner = capture_experiment,
    runner: CommandRunner = DEFAULT_COMMAND_RUNNER,
    perf_counter: Callable[[], float] = time.perf_counter,
    utc_now: Callable[[], datetime] = _utc_now,
) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        parser.print_usage(sys.stderr)
        return 2
    try:
        parsed = parser.parse_args(arguments)
    except SystemExit as error:
        return int(error.code) if error.code is not None else 0
    try:
        if parsed.command == "publish":
            candidate = parsed.candidate
            if not candidate.is_absolute():
                candidate = repository_root.resolve() / candidate
            destination = publish_audited_bundle(
                candidate, validate_study_id(parsed.study_id), repository_root=repository_root
            )
            print(f"validation-study: accepted evidence published at {destination}")
            return 0
        try:
            url = validate_endpoint_url(parsed.url)
            study_id = validate_study_id(parsed.study_id)
        except ValueError as error:
            raise TrafficlabError(
                f"invalid Validation Study command arguments: {error}",
                corrective_action="supply the exact credential-free HTTPS URL and lowercase study ID",
            ) from error
        if parsed.command == "prerequisites":
            result = run_prerequisites(url, study_id, repository_root=repository_root, runner=runner, utc_now=utc_now)
            output_path = repository_root.resolve() / "examples" / "validation_study" / "prerequisites.json"
            print(f"validation-study: prerequisites validated for {result.study_id} at {output_path}")
            return 0
        try:
            prerequisite_record = repository_relative_path(
                parsed.prerequisites.as_posix(), repository_root=repository_root, name="study prerequisite path"
            )
        except ValueError as error:
            raise TrafficlabError(
                f"invalid Validation Study command arguments: {error}",
                corrective_action="supply the exact repository-relative checked prerequisite path",
            ) from error
        prerequisite_path = repository_root.resolve() / Path(*prerequisite_record.split("/"))
        canonical_prerequisite_path = repository_root.resolve() / "examples" / "validation_study" / "prerequisites.json"
        try:
            require(
                prerequisite_path == canonical_prerequisite_path,
                "collection prerequisites must use examples/validation_study/prerequisites.json before candidate creation",
            )
        except ValueError as error:
            raise TrafficlabError(
                f"invalid Validation Study command arguments: {error}",
                corrective_action="supply the exact repository-relative checked prerequisite path",
            ) from error
        if parsed.command == "collect":
            attempt = begin_phase_attempt(repository_root.resolve(), study_id=study_id, url=url, phase="collection")
            owned_capture_image = PhaseCaptureImage(tag=phase_capture_tag(study_id, "collection"))
            primary: BaseException | None = None
            try:
                environment, retained_prerequisites, prerequisite_files, configs, object_size_bytes = (
                    collection_inputs_from_prerequisites(
                        repository_root,
                        prerequisite_path,
                        study_id=study_id,
                        url=url,
                        runner=runner,
                        require_successful_prerequisite=True,
                        owned_capture_image=owned_capture_image,
                    )
                )
                candidate = collect_validation_candidate(
                    repository_root=repository_root,
                    study_id=study_id,
                    url=url,
                    attempt=attempt,
                    environment=environment,
                    retained_prerequisites=retained_prerequisites,
                    prerequisite_files=prerequisite_files,
                    configs=configs,
                    run=run,
                    capture=capture,
                    object_size_bytes=object_size_bytes,
                    perf_counter=perf_counter,
                    owned_capture_image=owned_capture_image,
                    runner=runner,
                )
            except BaseException as error:
                primary = error
                raise
            finally:
                if owned_capture_image.build_attempted and (not owned_capture_image.cleanup_verified):
                    try:
                        remove_owned_phase_capture_image(
                            owned_capture_image,
                            phase="collection",
                            repository_root=repository_root.resolve(),
                            runner=runner,
                        )
                    except BaseException as cleanup_error:
                        if primary is None:
                            raise TrafficlabError(
                                f"Validation Study collection capture image cleanup failed: {cleanup_error}",
                                corrective_action="preserve the collection attempt, remove the exact owned capture image tag, and restart with a new study ID",
                            ) from cleanup_error
                        primary.add_note(f"collection capture image cleanup failed: {cleanup_error}")
            print(f"validation-study: candidate collected at {candidate}")
            return 0
        result = run_study(
            url,
            study_id,
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=runner,
            perf_counter=perf_counter,
            utc_now=utc_now,
        )
        output_path = repository_root.resolve() / "examples" / "validation_study" / "results.json"
        print(f"validation-study: study completed with {len(result.runs)} primary runs at {output_path}")
        return 0
    except TrafficlabError as error:
        print(f"validation-study: {error}; {error.corrective_action}", file=sys.stderr)
        return error.exit_code
