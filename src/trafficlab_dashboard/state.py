from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from trafficlab_dashboard.aspects.base import TraceVisibility
from trafficlab_dashboard.run_data import DashboardRun

_DEFAULT_ASPECT = "throughput"


@dataclass(frozen=True, slots=True)
class DashboardState:
    generation: int = 0
    run: DashboardRun | None = None
    selected_aspect: str | None = None
    requested_aspect: str | None = _DEFAULT_ASPECT
    visibility: TraceVisibility = TraceVisibility(True, True)
    loading_run: bool = False
    calculating: bool = False
    last_directory: Path | None = None
    progress_text: str = ""


def begin_run_load(state: DashboardState, directory: Path) -> DashboardState:
    return replace(
        state,
        generation=state.generation + 1,
        loading_run=True,
        calculating=False,
        last_directory=directory,
        progress_text=f"Loading {directory.name}",
    )


def accept_run_load(
    state: DashboardState,
    run: DashboardRun,
    *,
    aspect_order: Sequence[str],
) -> DashboardState:
    chosen_aspect = _choose_aspect(
        requested=state.requested_aspect or state.selected_aspect,
        aspect_order=aspect_order,
        unavailable=run.unavailable,
    )
    return replace(
        state,
        run=run,
        selected_aspect=chosen_aspect,
        requested_aspect=chosen_aspect,
        loading_run=False,
        calculating=False,
        progress_text=f"Loaded {run.directory.name}",
    )


def reject_run_load(state: DashboardState, message: str) -> DashboardState:
    return replace(
        state,
        loading_run=False,
        calculating=False,
        requested_aspect=state.selected_aspect or state.requested_aspect,
        progress_text=message,
    )


def begin_aspect_request(state: DashboardState, aspect_id: str) -> DashboardState:
    return replace(
        state,
        generation=state.generation + 1,
        requested_aspect=aspect_id,
        calculating=True,
        progress_text=f"Calculating {aspect_id}",
    )


def accept_aspect(state: DashboardState, aspect_id: str) -> DashboardState:
    return replace(
        state,
        selected_aspect=aspect_id,
        requested_aspect=aspect_id,
        calculating=False,
        progress_text="",
    )


def reject_aspect(state: DashboardState, message: str) -> DashboardState:
    return replace(
        state,
        requested_aspect=state.selected_aspect or state.requested_aspect,
        calculating=False,
        progress_text=message,
    )


def set_visibility(state: DashboardState, visibility: TraceVisibility) -> DashboardState:
    return replace(state, visibility=visibility, progress_text="")


def prepare_shutdown(state: DashboardState) -> DashboardState:
    return replace(
        state,
        generation=state.generation + 1,
        loading_run=False,
        calculating=False,
        progress_text="",
    )


def _choose_aspect(
    *,
    requested: str | None,
    aspect_order: Sequence[str],
    unavailable: Mapping[str, str],
) -> str:
    if not aspect_order:
        raise ValueError("aspect_order must not be empty")
    unavailable_map = dict(unavailable)
    if requested is not None and requested in aspect_order and requested not in unavailable_map:
        return requested
    for aspect_id in aspect_order:
        if aspect_id not in unavailable_map:
            return aspect_id
    raise ValueError("run does not expose any available aspect")
