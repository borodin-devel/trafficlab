"""Full pipeline types ownership."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from trafficlab.capture.types import CaptureResult
from trafficlab.preflight.types import PreparedExperiment

if TYPE_CHECKING:
    from trafficlab.comparison.schema import ComparisonResult
    from trafficlab.fitting.stage import FitStageResult
    from trafficlab.generation.stage import GenerationStageResult


def run_preflight(path: Path, *, config_only: bool) -> PreparedExperiment:
    from trafficlab.preflight.stage import run_preflight as run_preflight_stage

    return run_preflight_stage(path, config_only=config_only)


def capture_prepared_experiment(path: Path, prepared: PreparedExperiment) -> CaptureResult:
    from trafficlab.capture.stage import capture_prepared_experiment as capture_prepared_stage

    return capture_prepared_stage(path, prepared)


def fit_experiment(path: Path) -> FitStageResult:
    from trafficlab.fitting.stage import fit_experiment as fit_stage

    return fit_stage(path)


def generate_experiment(path: Path) -> GenerationStageResult:
    from trafficlab.generation.stage import generate_experiment as generate_stage

    return generate_stage(path)


def compare_experiment(path: Path) -> ComparisonResult:
    from trafficlab.comparison.stage import compare_experiment as compare_stage

    return compare_stage(path)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Validated results returned by the five complete-experiment stages."""

    experiment_path: Path
    run_directory: Path
    capture: CaptureResult
    fit: FitStageResult
    generation: GenerationStageResult
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class RunDependencies:
    """The five concrete stage boundaries used by the explicit coordinator."""

    preflight: Callable[[Path], PreparedExperiment]
    capture: Callable[[Path, PreparedExperiment], CaptureResult]
    fit: Callable[[Path], FitStageResult]
    generate: Callable[[Path], GenerationStageResult]
    compare: Callable[[Path], ComparisonResult]

    @classmethod
    def production(cls) -> Self:
        """Return the ordinary in-process stage functions with one full preflight."""
        return cls(_full_preflight, _capture_prepared, fit_experiment, generate_experiment, compare_experiment)


def _full_preflight(path: Path) -> PreparedExperiment:
    return run_preflight(path, config_only=False)


def _capture_prepared(path: Path, prepared: PreparedExperiment) -> CaptureResult:
    return capture_prepared_experiment(path, prepared)
