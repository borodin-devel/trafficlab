from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from trafficlab.common.errors import TrafficlabError
from trafficlab_dashboard.aspects.base import Aspect, CalculationSettings, PlotData
from trafficlab_dashboard.run_data import DashboardRun
from trafficlab_dashboard.run_loader import load_dashboard_run


@dataclass(frozen=True, slots=True)
class LoadRunSuccess:
    token: int
    directory: Path
    run: DashboardRun


@dataclass(frozen=True, slots=True)
class LoadRunFailure:
    token: int
    directory: Path
    error: TrafficlabError | Exception


@dataclass(frozen=True, slots=True)
class CalculateAspectSuccess:
    token: int
    aspect_id: str
    data: PlotData


@dataclass(frozen=True, slots=True)
class CalculateAspectFailure:
    token: int
    aspect_id: str
    error: Exception


type LoadRunResult = LoadRunSuccess | LoadRunFailure
type CalculateAspectResult = CalculateAspectSuccess | CalculateAspectFailure


class _WorkerSignals(QObject):
    result = Signal(object)


class LoadRunWorker(QRunnable):
    def __init__(
        self,
        *,
        token: int,
        directory: Path,
        loader: Callable[[Path], DashboardRun] = load_dashboard_run,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.directory = directory
        self.loader = loader
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            run = self.loader(self.directory)
        except (TrafficlabError, Exception) as error:
            self.signals.result.emit(LoadRunFailure(token=self.token, directory=self.directory, error=error))
            return
        self.signals.result.emit(LoadRunSuccess(token=self.token, directory=self.directory, run=run))


class CalculateAspectWorker(QRunnable):
    def __init__(
        self,
        *,
        token: int,
        aspect: Aspect,
        run: DashboardRun,
        settings: CalculationSettings,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.aspect = aspect
        self.run_data = run
        self.settings = settings
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            data = self.aspect.calculate(self.run_data, self.settings)
        except Exception as error:
            self.signals.result.emit(
                CalculateAspectFailure(token=self.token, aspect_id=self.aspect.identifier, error=error)
            )
            return
        self.signals.result.emit(CalculateAspectSuccess(token=self.token, aspect_id=self.aspect.identifier, data=data))
