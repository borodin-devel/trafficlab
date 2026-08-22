"""Full pipeline types ownership."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from trafficlab.capture.stage import CaptureResult, capture_prepared_experiment
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.stage import FitStageResult, fit_experiment
from trafficlab.generation.stage import GenerationStageResult, generate_experiment
from trafficlab.preflight.stage import run_preflight
from trafficlab.preflight.types import PreparedExperiment


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
