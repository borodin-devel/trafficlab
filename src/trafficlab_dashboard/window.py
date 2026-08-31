from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QShowEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from trafficlab.common.errors import TrafficlabError
from trafficlab_dashboard.aspects.base import Aspect, CalculationSettings, PlotData, TraceVisibility
from trafficlab_dashboard.aspects.registry import ASPECTS
from trafficlab_dashboard.cache import AspectCache
from trafficlab_dashboard.plotting.canvas import DashboardCanvas
from trafficlab_dashboard.plotting.export import ExportFormat, export_figure
from trafficlab_dashboard.run_data import DashboardRun
from trafficlab_dashboard.run_loader import load_dashboard_run
from trafficlab_dashboard.state import (
    DashboardState,
    accept_aspect,
    begin_aspect_request,
    begin_run_load,
    commit_pending_run,
    prepare_shutdown,
    reject_aspect,
    reject_pending_run,
    reject_run_load,
    set_visibility,
    stage_run_load,
)
from trafficlab_dashboard.workers import (
    CalculateAspectFailure,
    CalculateAspectResult,
    CalculateAspectWorker,
    LoadRunFailure,
    LoadRunResult,
    LoadRunWorker,
)

type LoadRunCallable = Callable[[Path], DashboardRun]
type ExportCallable = Callable[[Figure, Path, ExportFormat], None]


class DashboardWindow(QMainWindow):
    def __init__(
        self,
        *,
        initial_run_directory: Path | None,
        aspects: Sequence[Aspect] = ASPECTS,
        loader: LoadRunCallable = load_dashboard_run,
        exporter: ExportCallable = export_figure,
        calculation_settings: CalculationSettings | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        if not aspects:
            raise ValueError("DashboardWindow requires at least one aspect")
        self.initial_run_directory = initial_run_directory
        self.loader = loader
        self.exporter = exporter
        self.settings = CalculationSettings.default() if calculation_settings is None else calculation_settings
        self.aspects = tuple(aspects)
        self._aspects_by_id = {aspect.identifier: aspect for aspect in self.aspects}
        self.cache = AspectCache()
        self.state = DashboardState(requested_aspect=self.aspects[0].identifier)
        self.canvas = DashboardCanvas()
        self._current_plot: PlotData | None = None
        self._thread_pool = QThreadPool(self) if thread_pool is None else thread_pool
        self._active_workers: dict[int, object] = {}
        self._closing = False
        self._initial_action_pending = True
        self._syncing_controls = False
        self._build_window()
        self._populate_aspects()
        self._sync_aspect_selection()
        self._update_controls()
        self._show_status()

    def open_run(self, path: Path) -> None:
        self.state = begin_run_load(self.state, path)
        self._sync_aspect_selection()
        self._update_controls()
        self._show_status()
        worker = LoadRunWorker(token=self.state.generation, directory=path, loader=self.loader)
        worker.signals.result.connect(self.accept_load)
        self._active_workers[self.state.generation] = worker
        self._thread_pool.start(worker)

    def browse_run(self) -> None:
        start_directory = self._default_browse_directory()
        selected = QFileDialog.getExistingDirectory(self, "Open TrafficLab Run", str(start_directory))
        if not selected:
            if self.state.run is None:
                self.state = replace(self.state, progress_text="No run loaded")
                self._show_status()
            return
        self.open_run(Path(selected))

    def export_current_plot(self) -> None:
        if self._current_plot is None or self.state.selected_aspect is None:
            return
        default_name = self._default_export_name()
        destination, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Plot",
            str(default_name),
            "PNG (*.png);;SVG (*.svg)",
        )
        if not destination:
            return
        target, format = self._resolve_export_destination(Path(destination), selected_filter)
        try:
            self.exporter(self.canvas.figure, target, format)
        except TrafficlabError as error:
            self._show_error_dialog("Export Failed", self._format_error(error))

    @Slot(object)
    def accept_load(self, result: object) -> None:
        load_result = cast(LoadRunResult, result)
        self._active_workers.pop(load_result.token, None)
        if self._closing or load_result.token != self.state.generation:
            return
        if isinstance(load_result, LoadRunFailure):
            message = self._format_error(load_result.error)
            self.state = reject_run_load(self.state, message)
            self._sync_aspect_selection()
            self._update_controls()
            self._show_status()
            self._show_error_dialog("Open Run Failed", message)
            return
        self.state = stage_run_load(
            self.state,
            token=load_result.token,
            run=load_result.run,
            aspect_order=tuple(aspect.identifier for aspect in self.aspects),
        )
        self._sync_aspect_selection()
        self._update_controls()
        self._show_status()
        if self.state.pending_aspect is not None:
            self._request_plot_for_run(load_result.run, self.state.pending_aspect, load_result.token)

    @Slot(object)
    def accept_calculation(self, result: object) -> None:
        calculation_result = cast(CalculateAspectResult, result)
        self._active_workers.pop(calculation_result.token, None)
        if self._closing or calculation_result.token != self.state.generation:
            return
        if isinstance(calculation_result, CalculateAspectFailure):
            message = self._format_error(calculation_result.error)
            if self.state.pending_run is not None and self.state.pending_run_token == calculation_result.token:
                self.state = reject_pending_run(self.state, message)
            else:
                self.state = reject_aspect(self.state, message)
            self._sync_aspect_selection()
            self._update_controls()
            self._show_status()
            self._show_error_dialog("Aspect Calculation Failed", message)
            return
        if self.state.pending_run is not None and self.state.pending_run_token == calculation_result.token:
            pending_run = self.state.pending_run
            key = self.cache.key(pending_run, calculation_result.aspect_id, self.settings)
            self.cache.clear()
            self.cache.put(key, calculation_result.data)
            self._current_plot = calculation_result.data
            self.state = commit_pending_run(self.state, calculation_result.aspect_id)
            committed_run = self.state.run
            assert committed_run is not None
            self._update_aspect_availability(committed_run)
            self.canvas.render(calculation_result.data, self.state.visibility, preserve_viewport=False)
            self._sync_aspect_selection()
            self._update_controls()
            self._show_status()
            return
        run = self.state.run
        if run is None:
            return
        key = self.cache.key(run, calculation_result.aspect_id, self.settings)
        self.cache.put(key, calculation_result.data)
        self._current_plot = calculation_result.data
        self.state = accept_aspect(self.state, calculation_result.aspect_id)
        self.canvas.render(calculation_result.data, self.state.visibility, preserve_viewport=False)
        self._sync_aspect_selection()
        self._update_controls()
        self._show_status()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._initial_action_pending:
            self._initial_action_pending = False
            QTimer.singleShot(0, self._run_initial_action)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.state = prepare_shutdown(self.state)
        self._thread_pool.clear()
        super().closeEvent(event)

    def request_aspect(self, aspect_id: str) -> None:
        run = self.state.run
        if run is None:
            return
        if aspect_id not in self._aspects_by_id:
            return
        unavailable_reason = run.unavailable.get(aspect_id)
        if unavailable_reason is not None:
            self.state = replace(self.state, progress_text=unavailable_reason)
            self._show_status()
            return
        self.state = begin_aspect_request(self.state, aspect_id)
        self._sync_aspect_selection()
        self._update_controls()
        self._show_status()
        self._request_plot_for_run(run, aspect_id, self.state.generation)

    def _build_window(self) -> None:
        self.setWindowTitle("TrafficLab Dashboard")
        self.resize(1200, 760)
        self.open_button = QPushButton("Open Run")
        self.open_button.setObjectName("open_run_button")
        self.aspect_combo = QComboBox()
        self.aspect_combo.setObjectName("aspect_combo")
        self.reference_button = QPushButton("Reference")
        self.reference_button.setObjectName("reference_button")
        self.reference_button.setCheckable(True)
        self.reference_button.setChecked(True)
        self.generated_button = QPushButton("Generated")
        self.generated_button.setObjectName("generated_button")
        self.generated_button.setCheckable(True)
        self.generated_button.setChecked(True)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("reset_button")
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("export_button")

        controls = QHBoxLayout()
        controls.addWidget(self.open_button)
        controls.addWidget(self.aspect_combo)
        controls.addWidget(self.reference_button)
        controls.addWidget(self.generated_button)
        controls.addWidget(self.reset_button)
        controls.addWidget(self.export_button)
        self.controls_layout = controls

        plot_container = QWidget()
        plot_layout = QVBoxLayout(plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.canvas)
        self.progress_overlay = QLabel("Working…", plot_container)
        self.progress_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_overlay.setStyleSheet("background: rgba(255, 255, 255, 180); padding: 12px;")
        self.progress_overlay.hide()

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(controls)
        layout.addWidget(plot_container)
        self.setCentralWidget(root)
        self.statusBar()

        self.open_button.clicked.connect(self.browse_run)
        self.aspect_combo.currentIndexChanged.connect(self._on_aspect_selected)
        self.reference_button.toggled.connect(self._on_reference_toggled)
        self.generated_button.toggled.connect(self._on_generated_toggled)
        self.reset_button.clicked.connect(self.canvas.reset_view)
        self.export_button.clicked.connect(self.export_current_plot)

    def _populate_aspects(self) -> None:
        model = cast(QStandardItemModel, self.aspect_combo.model())
        model.clear()
        for aspect in self.aspects:
            item = QStandardItem(aspect.label)
            item.setData(aspect.identifier, Qt.ItemDataRole.UserRole)
            item.setToolTip("")
            item.setStatusTip("")
            model.appendRow(item)

    def _update_aspect_availability(self, run: DashboardRun | None) -> None:
        model = cast(QStandardItemModel, self.aspect_combo.model())
        unavailable = {} if run is None else dict(run.unavailable)
        for index, aspect in enumerate(self.aspects):
            item = model.item(index)
            reason = unavailable.get(aspect.identifier)
            item.setEnabled(reason is None)
            item.setToolTip("" if reason is None else reason)
            item.setStatusTip("" if reason is None else reason)

    def _sync_aspect_selection(self) -> None:
        target = self.state.requested_aspect or self.state.selected_aspect or self.aspects[0].identifier
        index = self.aspect_combo.findData(target)
        if index < 0:
            return
        self._syncing_controls = True
        try:
            self.aspect_combo.setCurrentIndex(index)
        finally:
            self._syncing_controls = False

    def _update_controls(self) -> None:
        active_aspect = self._active_aspect()
        trace_controls_enabled = self.state.run is not None and self._current_plot is not None and active_aspect.trace_controls
        self._syncing_controls = True
        try:
            self.reference_button.setChecked(self.state.visibility.reference)
            self.generated_button.setChecked(self.state.visibility.generated)
            self.reference_button.setEnabled(trace_controls_enabled)
            self.generated_button.setEnabled(trace_controls_enabled)
        finally:
            self._syncing_controls = False
        plot_available = self._current_plot is not None
        conflicting_work = self.state.loading_run or self.state.calculating
        self.open_button.setEnabled(not conflicting_work)
        self.aspect_combo.setEnabled(self.state.run is not None and not conflicting_work)
        self.reset_button.setEnabled(plot_available)
        self.export_button.setEnabled(plot_available)
        self.progress_overlay.setText(self.state.progress_text or "Working…")
        if conflicting_work:
            self.progress_overlay.resize(self.canvas.size())
            self.progress_overlay.show()
            self.progress_overlay.raise_()
        else:
            self.progress_overlay.hide()

    def _show_status(self) -> None:
        message = self._status_message()
        self.statusBar().showMessage(message)

    def _status_message(self) -> str:
        if self.state.run is None:
            return self.state.progress_text or "No run loaded"
        parts = [
            self.state.run.directory.name,
            f"Reference packets {self.state.run.reference_packet_count}",
            f"Generated packets {self.state.run.generated_packet_count}",
            f"W={self.state.run.window:g} s",
        ]
        if self.state.progress_text:
            parts.append(self.state.progress_text)
        return " · ".join(parts)

    def _run_initial_action(self) -> None:
        if self._closing:
            return
        if self.initial_run_directory is not None:
            self.open_run(self.initial_run_directory)
            return
        self.browse_run()

    def _default_browse_directory(self) -> Path:
        if self.state.run is not None:
            return self.state.run.directory
        if self.state.last_directory is not None:
            return self.state.last_directory.parent
        return Path.cwd()

    def _default_export_name(self) -> Path:
        base_directory = self.state.run.directory if self.state.run is not None else Path.cwd()
        aspect_id = self.state.selected_aspect or self.aspects[0].identifier
        return base_directory.parent / f"{base_directory.name}-{aspect_id}.png"

    def _resolve_export_destination(self, destination: Path, selected_filter: str) -> tuple[Path, ExportFormat]:
        if destination.suffix.lower() == ".svg":
            return destination, "svg"
        if destination.suffix.lower() == ".png":
            return destination, "png"
        if "SVG" in selected_filter:
            return destination.with_suffix(".svg"), "svg"
        return destination.with_suffix(".png"), "png"

    def _active_aspect(self) -> Aspect:
        aspect_id = self.state.selected_aspect or self.state.requested_aspect or self.aspects[0].identifier
        return self._aspects_by_id.get(aspect_id, self.aspects[0])

    def _on_aspect_selected(self, index: int) -> None:
        if self._syncing_controls or index < 0:
            return
        aspect_id = self.aspect_combo.itemData(index)
        if type(aspect_id) is not str:
            return
        self.request_aspect(aspect_id)

    def _on_reference_toggled(self, checked: bool) -> None:
        self._handle_visibility_change(reference=checked, generated=self.state.visibility.generated)

    def _on_generated_toggled(self, checked: bool) -> None:
        self._handle_visibility_change(reference=self.state.visibility.reference, generated=checked)

    def _handle_visibility_change(self, *, reference: bool, generated: bool) -> None:
        if self._syncing_controls or self.state.run is None:
            return
        aspect = self._active_aspect()
        visibility = TraceVisibility(reference=reference, generated=generated)
        if aspect.trace_controls and not visibility.reference and not visibility.generated:
            self._syncing_controls = True
            try:
                self.reference_button.setChecked(self.state.visibility.reference)
                self.generated_button.setChecked(self.state.visibility.generated)
            finally:
                self._syncing_controls = False
            self.state = replace(self.state, progress_text="At least one trace must remain visible.")
            self._show_status()
            return
        self.state = set_visibility(self.state, visibility)
        if self._current_plot is not None and aspect.trace_controls:
            self.canvas.render(self._current_plot, visibility, preserve_viewport=True)
        self._update_controls()
        self._show_status()

    def _format_error(self, error: TrafficlabError | Exception) -> str:
        if isinstance(error, TrafficlabError):
            return f"{error}\n\nCorrective action: {error.corrective_action}"
        return str(error)

    def _show_error_dialog(self, title: str, message: str) -> None:
        if self._closing:
            return
        QMessageBox.critical(self, title, message)

    def _request_plot_for_run(self, run: DashboardRun, aspect_id: str, token: int) -> None:
        key = self.cache.key(run, aspect_id, self.settings)
        cached = self.cache.get(key)
        if cached is not None:
            if self.state.pending_run is not None and self.state.pending_run_token == token:
                self.cache.clear()
                self.cache.put(key, cached)
                self._current_plot = cached
                self.state = commit_pending_run(self.state, aspect_id)
                committed_run = self.state.run
                assert committed_run is not None
                self._update_aspect_availability(committed_run)
            else:
                self._current_plot = cached
                self.state = accept_aspect(self.state, aspect_id)
            self.canvas.render(cached, self.state.visibility, preserve_viewport=False)
            self._sync_aspect_selection()
            self._update_controls()
            self._show_status()
            return
        worker = CalculateAspectWorker(
            token=token,
            aspect=self._aspects_by_id[aspect_id],
            run=run,
            settings=self.settings,
        )
        worker.signals.result.connect(self.accept_calculation)
        self._active_workers[token] = worker
        self._thread_pool.start(worker)
