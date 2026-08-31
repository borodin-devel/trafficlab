from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace
from trafficlab_dashboard.aspects.base import TraceVisibility
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun
from trafficlab_dashboard.state import (
    DashboardState,
    accept_run_load,
    begin_aspect_request,
    begin_run_load,
    prepare_shutdown,
    reject_aspect,
    set_visibility,
)


def _trace(*timestamps: float) -> TrafficTrace:
    return TrafficTrace.from_events(
        tuple(
            TraceEvent(
                timestamp=float(timestamp),
                direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                frame_length=100 + index,
            )
            for index, timestamp in enumerate(timestamps)
        )
    )


def _run(*, unavailable: MappingProxyType[str, str] | None = None) -> DashboardRun:
    return DashboardRun(
        directory=Path.cwd() / "run",
        identities=ArtifactIdentities(
            reference_sha256="1" * 64,
            generated_sha256="2" * 64,
            capture_sha256="3" * 64,
            similarity_sha256=None,
            best_model_sha256=None,
            history_sha256=None,
        ),
        metadata=CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:10"),
        reference=_trace(0.0, 1.0, 3.0),
        generated=_trace(0.0, 1.0),
        window=3.0,
        similarity=None,
        best_model=None,
        history=None,
        experiment=None,
        unavailable=MappingProxyType({}) if unavailable is None else unavailable,
    )


def test_begin_run_load_preserves_accepted_run_and_plot_while_invalidating_inflight_work() -> None:
    current = DashboardState(
        generation=4,
        run=_run(),
        selected_aspect="throughput",
        requested_aspect="throughput",
        visibility=TraceVisibility(reference=False, generated=True),
        calculating=True,
    )

    updated = begin_run_load(current, Path("/tmp/next-run"))

    assert updated.generation == 5
    assert updated.run is current.run
    assert updated.selected_aspect == "throughput"
    assert updated.requested_aspect == "throughput"
    assert updated.visibility == TraceVisibility(reference=False, generated=True)
    assert updated.loading_run is True
    assert updated.calculating is False
    assert updated.last_directory == Path("/tmp/next-run")


def test_accept_run_load_keeps_requested_aspect_only_when_it_is_available() -> None:
    current = DashboardState(selected_aspect="throughput", requested_aspect="similarity_scores")

    accepted = accept_run_load(
        current,
        _run(unavailable=MappingProxyType({"similarity_scores": "similarity.json is missing"})),
        aspect_order=("throughput", "similarity_scores", "ga_fitness_history"),
    )

    assert accepted.run is not None
    assert accepted.selected_aspect == "throughput"
    assert accepted.requested_aspect == "throughput"
    assert accepted.loading_run is False
    assert accepted.calculating is False


def test_begin_aspect_request_tracks_the_requested_identifier_without_changing_current_plot() -> None:
    current = DashboardState(generation=3, selected_aspect="throughput", requested_aspect="throughput")

    updated = begin_aspect_request(current, "iat_ecdf")

    assert updated.generation == 4
    assert updated.selected_aspect == "throughput"
    assert updated.requested_aspect == "iat_ecdf"
    assert updated.calculating is True


def test_reject_aspect_restores_last_accepted_aspect_after_a_failed_request() -> None:
    current = DashboardState(selected_aspect="throughput", requested_aspect="iat_ecdf", calculating=True)

    updated = reject_aspect(current, "could not calculate aspect")

    assert updated.selected_aspect == "throughput"
    assert updated.requested_aspect == "throughput"
    assert updated.calculating is False
    assert updated.progress_text == "could not calculate aspect"


def test_prepare_shutdown_invalidates_late_results_without_clearing_visible_plot_state() -> None:
    current = DashboardState(
        generation=7,
        run=_run(),
        selected_aspect="throughput",
        requested_aspect="throughput",
        loading_run=True,
        calculating=True,
    )

    updated = prepare_shutdown(current)

    assert updated.generation == 8
    assert updated.run is current.run
    assert updated.selected_aspect == "throughput"
    assert updated.requested_aspect == "throughput"
    assert updated.loading_run is False
    assert updated.calculating is False


def test_set_visibility_updates_the_stored_trace_selection() -> None:
    current = DashboardState(visibility=TraceVisibility(reference=True, generated=True))

    updated = set_visibility(current, TraceVisibility(reference=False, generated=True))

    assert updated.visibility == TraceVisibility(reference=False, generated=True)
