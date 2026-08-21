"""Reference capture stage ownership."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from trafficlab.artifacts.capture import (
    CapturePublication,
    publish_capture_pair,
    remove_stable_capture_diagnostics,
    rollback_capture_publication,
)
from trafficlab.capture.cleanup import cleanup_project
from trafficlab.capture.docker.compose import DockerCompose
from trafficlab.capture.docker.types import ServiceState
from trafficlab.capture.failures import append_event, capture_failure_logs, capture_failure_outcomes, outcome_error
from trafficlab.capture.lifecycle import (
    CaptureDocker,
    flush_capture,
    future_deadline,
    interrupt_lifecycle,
    observe_workload,
    remember,
    temporary_capture_directory,
    wait_readiness,
)
from trafficlab.capture.lineage import (
    CaptureResult,
    capture_lineage,
    identify_mounted_inputs,
    record_capture_input_failure,
    require_unchanged_capture_inputs,
    try_reuse_prepared_capture,
)
from trafficlab.capture.policy import (
    CaptureFailureOrigin,
    CaptureOutcome,
    FailureKind,
    record_cleanup_failure,
    record_induced_target_status,
    record_total_timeout,
    record_validation_failure,
)
from trafficlab.capture.topology import ComposePaths, write_production_compose
from trafficlab.common.errors import (
    DeadlineExceededError,
    TrafficlabError,
    append_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.preflight.stage import (
    PreparedExperiment,
    run_preflight,
)


def capture_prepared_experiment(
    path: Path,
    prepared: PreparedExperiment,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult:
    """Capture an already-preflighted experiment or reuse its stable valid pair."""
    reused, run_directory, experiment_identity = try_reuse_prepared_capture(path, prepared, clock=clock)
    if reused is not None:
        return reused

    environment_identity = prepared.report.environment_identity
    if environment_identity is None:
        raise TrafficlabError(
            "fresh capture requires resolved Docker image identities from full preflight",
            corrective_action="run full preflight without --config-only and retry capture",
        )

    if docker is None:
        docker = cast(CaptureDocker, DockerCompose(clock=clock))
    config = prepared.config
    environment_identity = replace(
        environment_identity,
        mounted_inputs=identify_mounted_inputs(config),
    )
    project_name = f"trafficlab-capture-{uuid.uuid4().hex}"
    states: dict[str, ServiceState] = {}
    outcome = CaptureOutcome()
    total_deadline: float | None = None
    result: CaptureResult | None = None
    publication: CapturePublication | None = None
    target_may_exist = False
    natural_target_succeeded = False

    def record_temporary_cleanup_failure(detail: str) -> None:
        nonlocal outcome
        outcome = record_validation_failure(outcome, detail)

    with temporary_capture_directory(
        run_directory,
        cleanup_failure=record_temporary_cleanup_failure,
    ) as temporary:
        capture_directory = Path(temporary).resolve()
        compose_path = (capture_directory / "compose.json").resolve()
        metadata_path = capture_directory / "capture.json"
        pcapng_path = capture_directory / "reference.pcapng.tmp"
        write_production_compose(
            compose_path,
            config,
            ComposePaths(project_name=project_name, output_directory=capture_directory),
            target_image=environment_identity.target_content_id,
            capture_image=environment_identity.capture_content_id,
        )
        creation_deadline = future_deadline(clock, config.capture.total_timeout_seconds, stage="project creation")
        operation = "record capture project creation"

        try:
            append_event(run_directory, "capture_project_created", project_name=project_name)
            operation = "create capture service"
            docker.create_capture(compose_path, project_name, deadline=creation_deadline)
            total_deadline = future_deadline(clock, config.capture.total_timeout_seconds, stage="total-run")
            operation = "start capture service"
            docker.start_capture(compose_path, project_name, deadline=total_deadline)
            readiness_deadline = future_deadline(clock, config.capture.readiness_timeout_seconds, stage="readiness")
            operation = "wait for capture readiness"
            outcome = wait_readiness(
                docker,
                compose_path,
                project_name,
                metadata_path,
                pcapng_path,
                states,
                deadline=readiness_deadline,
                total_deadline=total_deadline,
                clock=clock,
                interruption=interruption,
            )
            if outcome.primary_kind is not None:
                if outcome.primary_kind is FailureKind.USER_INTERRUPTION:
                    outcome = interrupt_lifecycle(
                        docker,
                        compose_path,
                        project_name,
                        states,
                        outcome,
                        target_may_exist=False,
                        total_deadline=total_deadline,
                        flush_timeout_seconds=config.capture.flush_timeout_seconds,
                        clock=clock,
                    )
                    closed_capture = states.get("capture")
                    if (
                        closed_capture is not None
                        and closed_capture.state == "exited"
                        and closed_capture.exit_code == 0
                    ):
                        try:
                            require_unchanged_capture_inputs(
                                run_directory,
                                experiment_identity,
                                config,
                                environment_identity.mounted_inputs,
                            )
                            publication = publish_capture_pair(
                                metadata_path,
                                pcapng_path,
                                run_directory,
                                target_success=False,
                                deadline=total_deadline,
                                clock=clock,
                            )
                        except DeadlineExceededError as error:
                            outcome = record_total_timeout(
                                outcome,
                                str(error),
                                origin=CaptureFailureOrigin.VALIDATION,
                            )
                        except TrafficlabError as error:
                            outcome = record_capture_input_failure(outcome, error)
                        else:
                            for warning in publication.warnings:
                                outcome = record_validation_failure(
                                    outcome,
                                    f"capture publication cleanup warning: {warning}",
                                )
                else:
                    outcome = capture_failure_logs(
                        docker,
                        compose_path,
                        project_name,
                        run_directory,
                        outcome,
                        deadline=total_deadline,
                        context="readiness failure",
                    )
            if outcome.primary_kind is None:
                operation = "record capture readiness"
                append_event(run_directory, "capture_ready", project_name=project_name)
                operation = "start target service"
                target_may_exist = True
                docker.start_target(compose_path, project_name, deadline=total_deadline)
                operation = "calculate target workload deadline"
                workload_deadline = future_deadline(clock, config.capture.workload_timeout_seconds, stage="workload")
                operation = "observe target workload"
                outcome, capture_state = observe_workload(
                    docker,
                    compose_path,
                    project_name,
                    states,
                    stage_deadline=workload_deadline,
                    total_deadline=total_deadline,
                    clock=clock,
                    interruption=interruption,
                )
                target = states.get("target")
                natural_target = target is not None and target.state == "exited"
                if not natural_target:
                    try:
                        docker.kill_target(compose_path, project_name, deadline=total_deadline)
                    except (TrafficlabError, OSError) as error:
                        outcome = record_validation_failure(
                            outcome,
                            f"could not kill target after capture stopped or workload ended: {error}",
                        )
                    try:
                        killed = docker.service_state(compose_path, project_name, "target", deadline=total_deadline)
                    except (TrafficlabError, OSError) as error:
                        outcome = record_validation_failure(
                            outcome,
                            f"could not inspect target after requested kill: {error}",
                        )
                    else:
                        remember(states, "target", killed)
                        if killed is not None and killed.state == "exited":
                            outcome = record_induced_target_status(outcome, killed.exit_code)
                if outcome.primary_kind is FailureKind.CAPTURE_STOPPED:
                    outcome = capture_failure_logs(
                        docker,
                        compose_path,
                        project_name,
                        run_directory,
                        outcome,
                        deadline=total_deadline,
                        context="capture stopped",
                    )
                if capture_state is not None and capture_state.state == "running":
                    operation = "flush capture output"
                    flush_deadline = future_deadline(clock, config.capture.flush_timeout_seconds, stage="flush")
                    outcome = flush_capture(
                        docker,
                        compose_path,
                        project_name,
                        states,
                        outcome,
                        stage_deadline=flush_deadline,
                        total_deadline=total_deadline,
                        clock=clock,
                    )
                target_status = target.exit_code if natural_target and target is not None else None
                natural_target_succeeded = target_status == 0
                closed_capture = states.get("capture")
                capture_closed_cleanly = (
                    closed_capture is not None and closed_capture.state == "exited" and closed_capture.exit_code == 0
                )
                if capture_closed_cleanly:
                    operation = "validate and publish capture output"
                    try:
                        require_unchanged_capture_inputs(
                            run_directory,
                            experiment_identity,
                            config,
                            environment_identity.mounted_inputs,
                        )
                        publication = publish_capture_pair(
                            metadata_path,
                            pcapng_path,
                            run_directory,
                            target_success=target_status == 0,
                            deadline=total_deadline,
                            clock=clock,
                        )
                    except DeadlineExceededError as error:
                        outcome = record_total_timeout(
                            outcome,
                            str(error),
                            origin=CaptureFailureOrigin.VALIDATION,
                        )
                    except TrafficlabError as error:
                        outcome = record_capture_input_failure(outcome, error)
                    else:
                        for warning in publication.warnings:
                            outcome = record_validation_failure(
                                outcome,
                                f"capture publication cleanup warning: {warning}",
                            )
                        if target_status == 0 and outcome.primary_kind is None:
                            result = CaptureResult(
                                run_directory=run_directory,
                                reference_path=run_directory / "reference.pcapng",
                                packet_count=publication.inspection.packet_count,
                                target_status=target_status,
                            )
        except KeyboardInterrupt:
            active_deadline = total_deadline if total_deadline is not None else creation_deadline
            outcome = interrupt_lifecycle(
                docker,
                compose_path,
                project_name,
                states,
                outcome,
                target_may_exist=target_may_exist,
                total_deadline=active_deadline,
                flush_timeout_seconds=config.capture.flush_timeout_seconds,
                clock=clock,
            )
            closed_capture = states.get("capture")
            if closed_capture is not None and closed_capture.state == "exited" and closed_capture.exit_code == 0:
                try:
                    require_unchanged_capture_inputs(
                        run_directory,
                        experiment_identity,
                        config,
                        environment_identity.mounted_inputs,
                    )
                    publication = publish_capture_pair(
                        metadata_path,
                        pcapng_path,
                        run_directory,
                        target_success=False,
                        deadline=active_deadline,
                        clock=clock,
                    )
                except DeadlineExceededError as error:
                    outcome = record_total_timeout(
                        outcome,
                        str(error),
                        origin=CaptureFailureOrigin.VALIDATION,
                    )
                except TrafficlabError as error:
                    outcome = record_capture_input_failure(outcome, error)
                else:
                    for warning in publication.warnings:
                        outcome = record_validation_failure(
                            outcome,
                            f"capture publication cleanup warning: {warning}",
                        )
        except (TrafficlabError, OSError) as error:
            outcome = record_validation_failure(outcome, f"could not {operation}: {error}")
        finally:
            cleanup_deadline = total_deadline if total_deadline is not None else creation_deadline
            cleanup = cleanup_project(
                docker,
                compose_path,
                project_name,
                deadline=cleanup_deadline,
                clock=clock,
            )
            if not cleanup.success:
                outcome = record_cleanup_failure(outcome, cleanup.detail)

    if publication is not None and publication.created_by_call and outcome.primary_kind is not None:
        try:
            rollback_capture_publication(run_directory, publication)
        except TrafficlabError as error:
            outcome = record_validation_failure(
                outcome,
                f"could not roll back owned capture publication after capture failure: {error}",
            )
        result = None

    if outcome.primary_kind is not None:
        capture_state = states.get("capture")
        capture_status = (
            capture_state.exit_code if capture_state is not None and capture_state.state == "exited" else None
        )
        primary_outcome, secondary_outcomes = capture_failure_outcomes(
            outcome,
            capture_status=capture_status,
            natural_target_succeeded=natural_target_succeeded,
        )
        error = outcome_error(outcome)
        error.failure_outcomes = (primary_outcome, *secondary_outcomes)
        error.failure_outcome = primary_outcome
        try:
            append_event(
                run_directory,
                "capture_failed",
                detail=outcome.primary_detail,
                failure_kind=outcome.primary_kind.value,
                failure_outcome=primary_outcome.as_dict(),
                primary_status=outcome.primary_status,
                secondary_details=[item.detail for item in outcome.secondary_details],
                secondary_failures=[
                    {"detail": item.detail, "kind": item.kind.value, "status": item.status}
                    for item in outcome.secondary_details
                ],
                secondary_outcomes=[item.as_dict() for item in secondary_outcomes],
            )
        except TrafficlabError as logging_error:
            append_failure_outcome(
                error,
                failure_outcome_from_error(
                    logging_error,
                    kind="publication_failed",
                    stage="capture",
                    affected_evidence="run.log",
                    evidence_state="not_published",
                    authority="secondary",
                ),
            )
            error.args = (f"{error}; additionally could not append capture failure to run.log: {logging_error}",)
        raise error
    if result is None:
        raise TrafficlabError(
            "capture completed without a reusable reference",
            corrective_action="inspect run.log and retry capture",
        )
    remove_stable_capture_diagnostics(run_directory)
    append_event(
        run_directory,
        "capture_published",
        **capture_lineage(run_directory, environment_identity, experiment_identity=experiment_identity),
        packet_count=result.packet_count,
        path=str(result.reference_path),
        project_name=project_name,
        reused=False,
    )
    return result


def capture_experiment(
    path: Path,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult:
    """Reuse after local preparation, otherwise run full preflight and capture."""
    locally_prepared = run_preflight(path, config_only=True, docker=docker, clock=clock)
    reused, _, _ = try_reuse_prepared_capture(path, locally_prepared, clock=clock)
    if reused is not None:
        return reused
    prepared = run_preflight(path, config_only=False, docker=docker, clock=clock)
    return capture_prepared_experiment(
        path,
        prepared,
        docker=docker,
        clock=clock,
        interruption=interruption,
    )
