"""PySide6 desktop UI for the search simulation dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from math import ceil
import sys
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from config import SUPPORTED_ALGORITHMS, SUPPORTED_NETWORK_TYPES, SimulationConfig, normalize_algorithm_name
from visualization import (
    GOAL_COLOR,
    FRONTIER_COLOR,
    PATH_COLOR,
    START_COLOR,
    TEXT_MAIN,
    TEXT_MUTED,
    TRAIL_COLOR,
    VISITED_COLOR,
    SearchMapWidget,
    SimulationBundle,
    format_distance,
    format_optimal,
    format_seconds,
    parse_coordinate_text,
    sample_nodes,
)

BundleLoader = Callable[[SimulationConfig], SimulationBundle]

APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #f1ece5;
    color: #1f2933;
    font-size: 12px;
}
QFrame#HeaderFrame {
    background: #fffdf9;
    border: 1px solid #d9cfc4;
    border-radius: 12px;
}
QGroupBox {
    background: #fffdf9;
    border: 1px solid #d9cfc4;
    border-radius: 12px;
    margin-top: 12px;
    font-weight: 600;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}
QLineEdit, QComboBox, QSpinBox {
    background: #ffffff;
    border: 1px solid #cdd5df;
    border-radius: 8px;
    padding: 6px 8px;
    min-height: 28px;
}
QPushButton {
    background: #eef2f7;
    border: 1px solid #cdd5df;
    border-radius: 9px;
    padding: 8px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background: #e3e9f0;
}
QPushButton:disabled {
    background: #f6f7f9;
    color: #94a3b8;
}
QTableWidget {
    background: #ffffff;
    border: 1px solid #d9cfc4;
    border-radius: 10px;
    gridline-color: #e5e7eb;
}
QHeaderView::section {
    background: #f4f6f8;
    border: none;
    border-bottom: 1px solid #d9cfc4;
    padding: 6px;
    font-weight: 600;
}
QSplitter::handle {
    background: #d9cfc4;
}
QSlider::groove:horizontal {
    border: 0px;
    height: 6px;
    background: #d7dfe8;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #4169e1;
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
"""


class ScenarioLoadSignals(QtCore.QObject):
    """Signals for a background scenario load task."""

    finished = QtCore.Signal(object)
    error = QtCore.Signal(str)


class ScenarioLoadTask(QtCore.QRunnable):
    """Background job that resolves the route and precomputes traces."""

    def __init__(self, loader: BundleLoader, config: SimulationConfig) -> None:
        super().__init__()
        self.loader = loader
        self.config = config
        self.signals = ScenarioLoadSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            bundle = self.loader(self.config)
            self.signals.finished.emit(bundle)
        except Exception as error:
            self.signals.error.emit(str(error))


class SearchSimulationWindow(QtWidgets.QMainWindow):
    """Main desktop dashboard window."""

    def __init__(self, bundle: SimulationBundle, loader: BundleLoader) -> None:
        super().__init__()
        self.loader = loader
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self.bundle = bundle
        self.config = bundle.config
        self.traces = bundle.traces
        self.current_algorithm = normalize_algorithm_name(self.config.selected_algorithm)
        self.trace = self.traces[self.current_algorithm]
        self.event_index = 0
        self.is_running = False
        self.is_loading = False
        self.status_message = "Ready"
        self.compare_queue: list[str] = []
        self.last_completed_algorithm: str | None = None
        self.frontier_nodes: set[int] = set()
        self.history_nodes: list[int] = []
        self.recent_nodes: deque[int] = deque()
        self.recent_edges: deque[tuple[int, int]] = deque()
        self.recent_frontier_nodes: deque[int] = deque()
        self._suspend_widget_events = False

        self.setWindowTitle("Pathfinder Search Dashboard")
        self.resize(1640, 980)
        self.setMinimumSize(1240, 760)
        self.setStyleSheet(APP_STYLESHEET)

        self.playback_timer = QtCore.QTimer(self)
        self.playback_timer.setInterval(16)
        self.playback_timer.timeout.connect(self._on_playback_tick)

        self._build_ui()
        self._set_bundle(bundle, initial=True)
        self.statusBar().showMessage("Ready")

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        header = QtWidgets.QFrame(objectName="HeaderFrame")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(10)
        title = QtWidgets.QLabel("Pathfinder Search Dashboard")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #1f2933;")
        subtitle = QtWidgets.QLabel("Real-map graph search simulation with fast local playback")
        subtitle.setStyleSheet("font-size: 12px; color: #52606d;")
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header_layout.addLayout(title_stack)
        header_layout.addStretch(1)
        self.scenario_chip = self._make_chip("#eef2ff")
        self.algorithm_chip = self._make_chip("#eefbf2")
        self.status_chip = self._make_chip("#fff5e6")
        header_layout.addWidget(self.scenario_chip)
        header_layout.addWidget(self.algorithm_chip)
        header_layout.addWidget(self.status_chip)
        root_layout.addWidget(header)

        top_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        top_splitter.setChildrenCollapsible(False)
        self.map_widget = SearchMapWidget()
        top_splitter.addWidget(self.map_widget)

        sidebar_container = QtWidgets.QWidget()
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar_container)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(10)
        sidebar_layout.addWidget(self._build_route_group())
        sidebar_layout.addWidget(self._build_algorithm_group())
        sidebar_layout.addWidget(self._build_playback_group())
        sidebar_layout.addStretch(1)

        sidebar_scroll = QtWidgets.QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sidebar_scroll.setWidget(sidebar_container)
        sidebar_scroll.setMinimumWidth(380)
        top_splitter.addWidget(sidebar_scroll)
        top_splitter.setStretchFactor(0, 5)
        top_splitter.setStretchFactor(1, 2)

        lower_panel = QtWidgets.QWidget()
        lower_layout = QtWidgets.QHBoxLayout(lower_panel)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(10)
        lower_layout.addWidget(self._build_live_metrics_group(), 2)
        lower_layout.addWidget(self._build_summary_group(), 4)
        lower_layout.addWidget(self._build_session_group(), 2)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(lower_panel)
        main_splitter.setStretchFactor(0, 6)
        main_splitter.setStretchFactor(1, 2)
        root_layout.addWidget(main_splitter, 1)

    @staticmethod
    def _make_chip(background: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setStyleSheet(
            f"background: {background}; border: 1px solid #d9cfc4; border-radius: 12px; "
            "padding: 6px 12px; font-weight: 600;"
        )
        return label

    def _build_route_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Route Setup")
        layout = QtWidgets.QFormLayout(group)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)
        layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.place_edit = QtWidgets.QLineEdit()
        self.network_combo = QtWidgets.QComboBox()
        self.network_combo.addItems(SUPPORTED_NETWORK_TYPES)
        self.start_query_edit = QtWidgets.QLineEdit()
        self.goal_query_edit = QtWidgets.QLineEdit()
        self.start_coord_edit = QtWidgets.QLineEdit()
        self.start_coord_edit.setPlaceholderText("optional: lat, lon")
        self.goal_coord_edit = QtWidgets.QLineEdit()
        self.goal_coord_edit.setPlaceholderText("optional: lat, lon")
        self.radius_spin = QtWidgets.QSpinBox()
        self.radius_spin.setRange(0, 500_000)
        self.radius_spin.setSingleStep(500)
        self.radius_spin.setSuffix(" m")
        self.radius_spin.setSpecialValueText("Whole place")
        self.load_button = QtWidgets.QPushButton("Load / Update Scenario")
        self.load_button.clicked.connect(self._on_load_clicked)

        layout.addRow("Place", self.place_edit)
        layout.addRow("Network", self.network_combo)
        layout.addRow("Start query", self.start_query_edit)
        layout.addRow("Goal query", self.goal_query_edit)
        layout.addRow("Start coords", self.start_coord_edit)
        layout.addRow("Goal coords", self.goal_coord_edit)
        layout.addRow("Working radius", self.radius_spin)
        layout.addRow(self.load_button)

        note = QtWidgets.QLabel(
            "Use text queries or provide coordinates. If geocoding is ambiguous, coordinates will be used as the fallback."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addRow(note)
        return group

    def _build_algorithm_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Algorithm")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)

        self.algorithm_combo = QtWidgets.QComboBox()
        self.algorithm_combo.addItems(SUPPORTED_ALGORITHMS)
        self.algorithm_combo.currentTextChanged.connect(self._on_algorithm_changed)
        self.compare_checkbox = QtWidgets.QCheckBox("Sequential compare mode")
        self.compare_checkbox.toggled.connect(self._on_compare_mode_changed)
        self.adaptive_checkbox = QtWidgets.QCheckBox("Adaptive stepping")
        self.adaptive_checkbox.setChecked(True)
        explanation = QtWidgets.QLabel(
            "BFS and DFS ignore weights. Dijkstra and A* optimize edge length, while Greedy uses the heuristic only."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {TEXT_MUTED};")

        layout.addWidget(self.algorithm_combo)
        layout.addWidget(self.compare_checkbox)
        layout.addWidget(self.adaptive_checkbox)
        layout.addWidget(explanation)
        return group

    def _build_playback_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Playback")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)

        button_row = QtWidgets.QGridLayout()
        button_row.setHorizontalSpacing(8)
        button_row.setVerticalSpacing(8)
        self.start_button = QtWidgets.QPushButton("Start / Resume")
        self.pause_button = QtWidgets.QPushButton("Pause")
        self.next_button = QtWidgets.QPushButton("Next Step")
        self.reset_button = QtWidgets.QPushButton("Reset")
        self.start_button.clicked.connect(self._on_start)
        self.pause_button.clicked.connect(self._on_pause)
        self.next_button.clicked.connect(self._on_next)
        self.reset_button.clicked.connect(self._on_reset)
        button_row.addWidget(self.start_button, 0, 0)
        button_row.addWidget(self.pause_button, 0, 1)
        button_row.addWidget(self.next_button, 1, 0)
        button_row.addWidget(self.reset_button, 1, 1)

        self.speed_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 40)
        self.speed_slider.valueChanged.connect(self._on_speed_value_changed)
        self.speed_value = QtWidgets.QLabel()
        self.batch_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.batch_slider.setRange(1, 25)
        self.batch_slider.valueChanged.connect(self._on_batch_value_changed)
        self.batch_value = QtWidgets.QLabel()

        layout.addLayout(button_row)
        layout.addLayout(self._slider_row("Speed", self.speed_slider, self.speed_value))
        layout.addLayout(self._slider_row("Batch", self.batch_slider, self.batch_value))
        return group

    @staticmethod
    def _slider_row(
        title: str,
        slider: QtWidgets.QSlider,
        value_label: QtWidgets.QLabel,
    ) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        label = QtWidgets.QLabel(title)
        label.setMinimumWidth(48)
        value_label.setMinimumWidth(30)
        value_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        return row

    def _build_live_metrics_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Live Metrics")
        layout = QtWidgets.QFormLayout(group)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(8)
        self.metric_labels: dict[str, QtWidgets.QLabel] = {}
        for key, label_text in (
            ("algorithm", "Algorithm"),
            ("status", "Status"),
            ("step", "Step"),
            ("runtime", "Runtime"),
            ("explored", "Explored"),
            ("frontier", "Frontier"),
            ("path_length", "Path length"),
            ("path_cost", "Path cost"),
            ("optimal", "Optimal"),
        ):
            value_label = QtWidgets.QLabel("--")
            value_label.setStyleSheet("font-weight: 600;")
            self.metric_labels[key] = value_label
            layout.addRow(label_text, value_label)
        return group

    def _build_summary_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Summary")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(14, 18, 14, 14)
        self.summary_table = QtWidgets.QTableWidget(0, 7)
        self.summary_table.setHorizontalHeaderLabels(
            ["Algorithm", "Status", "Visited", "Runtime", "Cost", "Path", "Optimal"]
        )
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.summary_table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.summary_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.summary_table)
        return group

    def _build_session_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Session")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(8)

        self.session_label = QtWidgets.QLabel("--")
        self.session_label.setWordWrap(True)
        legend = QtWidgets.QLabel(
            f"<b>Legend</b><br>"
            f"<span style='color:{START_COLOR};'>●</span> Start&nbsp;&nbsp;"
            f"<span style='color:{GOAL_COLOR};'>●</span> Goal<br>"
            f"<span style='color:{VISITED_COLOR};'>●</span> Visited history&nbsp;&nbsp;"
            f"<span style='color:{TRAIL_COLOR};'>●</span> Recent trail<br>"
            f"<span style='color:{FRONTIER_COLOR};'>●</span> Frontier&nbsp;&nbsp;"
            f"<span style='color:{PATH_COLOR};'>●</span> Final path"
        )
        legend.setStyleSheet(f"color: {TEXT_MAIN};")
        legend.setWordWrap(True)
        layout.addWidget(self.session_label)
        layout.addStretch(1)
        layout.addWidget(legend)
        return group

    def _set_bundle(self, bundle: SimulationBundle, initial: bool = False) -> None:
        self.bundle = bundle
        self.config = bundle.config
        self.traces = bundle.traces
        if self.current_algorithm not in self.traces:
            self.current_algorithm = normalize_algorithm_name(self.config.selected_algorithm)
        self.trace = self.traces[self.current_algorithm]
        self.map_widget.set_bundle(bundle)
        self._sync_controls_from_config()
        self._populate_summary_table()
        self._reset_visual_state()
        if not initial:
            self.status_message = "Ready"
            self._refresh_ui()

    def _sync_controls_from_config(self) -> None:
        self._suspend_widget_events = True
        self.place_edit.setText(self.config.place_name)
        self.network_combo.setCurrentText(self.config.network_type)
        self.start_query_edit.setText(self.config.start_query or "")
        self.goal_query_edit.setText(self.config.goal_query or "")
        self.start_coord_edit.setText(self._format_coord_pair(self.config.start_lat, self.config.start_lon))
        self.goal_coord_edit.setText(self._format_coord_pair(self.config.goal_lat, self.config.goal_lon))
        self.radius_spin.setValue(0 if self.config.graph_radius_m is None else int(round(self.config.graph_radius_m)))
        self.algorithm_combo.setCurrentText(self.current_algorithm)
        self.compare_checkbox.setChecked(self.config.compare_mode)
        self.speed_slider.setValue(int(round(self.config.animation_speed)))
        self.batch_slider.setValue(int(round(self.config.batch_steps)))
        self._update_slider_labels()
        self._suspend_widget_events = False

    @staticmethod
    def _format_coord_pair(lat: float | None, lon: float | None) -> str:
        if lat is None or lon is None:
            return ""
        return f"{lat:.6f}, {lon:.6f}"

    def _populate_summary_table(self) -> None:
        self.summary_table.setRowCount(len(self.traces))
        for row, (algorithm_name, trace) in enumerate(self.traces.items()):
            values = [
                algorithm_name,
                "found" if trace.metrics.found else "failed",
                f"{trace.metrics.visited_count:,}",
                format_seconds(trace.metrics.runtime_seconds),
                format_distance(trace.metrics.path_cost_m),
                format_distance(trace.metrics.path_length_m),
                format_optimal(trace.metrics.optimal_under_weighting),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    if column == 0
                    else QtCore.Qt.AlignmentFlag.AlignCenter
                )
                self.summary_table.setItem(row, column, item)

    def _reset_visual_state(self) -> None:
        self.pause()
        self.trace = self.traces[self.current_algorithm]
        self.event_index = 0
        self.frontier_nodes = set(self.trace.initial_frontier)
        self.history_nodes = []
        self.recent_nodes = deque(maxlen=self.config.trail_length)
        self.recent_edges = deque(maxlen=max(60, self.config.trail_length // 2))
        self.recent_frontier_nodes = deque(maxlen=max(40, self.config.trail_length // 2))
        self.history_stride = max(1, ceil(len(self.trace.events) / self.config.max_history_nodes))
        self.status_message = "Ready"
        self.compare_queue = []
        self._refresh_ui()

    def _on_playback_tick(self) -> None:
        if not self.is_running or self.is_loading:
            return

        active = self._advance_steps(self._steps_per_tick())
        self._refresh_ui()
        if not active:
            self.pause()

    def _steps_per_tick(self) -> int:
        speed_steps = max(1, self.speed_slider.value())
        batch_steps = max(1, self.batch_slider.value())
        adaptive_scale = 1

        if self.adaptive_checkbox.isChecked():
            total_steps = max(1, len(self.trace.events))
            progress = self.event_index / total_steps
            middle_scale = min(60, max(1, total_steps // 3000))
            if progress < 0.05 or progress > 0.95:
                adaptive_scale = 1
            elif progress < 0.15 or progress > 0.85:
                adaptive_scale = max(1, middle_scale // 2)
            else:
                adaptive_scale = middle_scale

        return max(1, speed_steps * batch_steps * adaptive_scale)

    def _advance_steps(self, count: int) -> bool:
        processed_any = False
        while count > 0:
            if self.event_index >= len(self.trace.events):
                self._mark_algorithm_complete()
                if not self._maybe_advance_compare_queue():
                    return processed_any
                continue

            event = self.trace.events[self.event_index]
            self.event_index += 1
            self._apply_event(event)
            processed_any = True
            count -= 1

        return True

    def _mark_algorithm_complete(self) -> None:
        self.last_completed_algorithm = self.current_algorithm
        self.status_message = "Found path" if self.trace.metrics.found else "Search failed"

    def _maybe_advance_compare_queue(self) -> bool:
        if not self.compare_checkbox.isChecked() or not self.compare_queue:
            return False

        next_algorithm = self.compare_queue.pop(0)
        self._activate_algorithm(next_algorithm, "Ready", clear_compare_queue=False)
        return True

    def _apply_event(self, event) -> None:
        if event.step == 1 or event.step % self.history_stride == 0 or event.status != "searching":
            self.history_nodes.append(event.current)
        self.recent_nodes.append(event.current)

        for node in event.frontier_removed:
            self.frontier_nodes.discard(node)
        for node in event.frontier_added:
            self.frontier_nodes.add(node)
            self.recent_frontier_nodes.append(node)

        if event.parent_edge is not None:
            self.recent_edges.append(event.parent_edge)

        self.status_message = (
            "Searching"
            if event.status == "searching"
            else ("Found path" if event.status == "found" else "Search failed")
        )

    def _on_start(self) -> None:
        if self.is_loading:
            return

        if self.event_index >= len(self.trace.events):
            self._activate_algorithm(self.current_algorithm, "Ready")

        if self.compare_checkbox.isChecked() and not self.compare_queue:
            self.compare_queue = self._compare_sequence()[1:]

        self.is_running = True
        self.playback_timer.start()
        self.statusBar().showMessage("Playback running")

    def pause(self) -> None:
        self.is_running = False
        self.playback_timer.stop()

    def _on_pause(self) -> None:
        self.pause()
        self.statusBar().showMessage("Playback paused")
        self._refresh_ui()

    def _on_next(self) -> None:
        if self.is_loading:
            return
        self.pause()
        self._advance_steps(1)
        self._refresh_ui()

    def _on_reset(self) -> None:
        if self.is_loading:
            return
        self._activate_algorithm(self.current_algorithm, "Ready")

    def _on_speed_value_changed(self, _value: int) -> None:
        self._update_slider_labels()
        if self._suspend_widget_events:
            return
        self.config = replace(self.config, animation_speed=float(self.speed_slider.value()))
        self._refresh_ui()

    def _on_batch_value_changed(self, _value: int) -> None:
        self._update_slider_labels()
        if self._suspend_widget_events:
            return
        self.config = replace(self.config, batch_steps=self.batch_slider.value())
        self._refresh_ui()

    def _update_slider_labels(self) -> None:
        self.speed_value.setText(str(self.speed_slider.value()))
        self.batch_value.setText(str(self.batch_slider.value()))

    def _on_compare_mode_changed(self, checked: bool) -> None:
        if self._suspend_widget_events:
            return
        self.config = replace(self.config, compare_mode=checked)
        if not checked:
            self.compare_queue = []
        self._refresh_ui()

    def _on_algorithm_changed(self, label: str) -> None:
        if self._suspend_widget_events or self.is_loading:
            return
        self._activate_algorithm(normalize_algorithm_name(label), "Ready")

    def _activate_algorithm(
        self,
        algorithm: str,
        status_message: str,
        clear_compare_queue: bool = True,
    ) -> None:
        self.pause()
        self.current_algorithm = normalize_algorithm_name(algorithm)
        self.trace = self.traces[self.current_algorithm]
        self.event_index = 0
        self.frontier_nodes = set(self.trace.initial_frontier)
        self.history_nodes = []
        self.recent_nodes = deque(maxlen=self.config.trail_length)
        self.recent_edges = deque(maxlen=max(60, self.config.trail_length // 2))
        self.recent_frontier_nodes = deque(maxlen=max(40, self.config.trail_length // 2))
        self.history_stride = max(1, ceil(len(self.trace.events) / self.config.max_history_nodes))
        self.status_message = status_message
        if clear_compare_queue:
            self.compare_queue = []
        self._refresh_ui()

    def _compare_sequence(self) -> list[str]:
        algorithms = list(self.traces.keys())
        start_index = algorithms.index(self.current_algorithm)
        return algorithms[start_index:] + algorithms[:start_index]

    def _on_load_clicked(self) -> None:
        if self.is_loading:
            return

        try:
            config = self._config_from_controls()
            config.validate()
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(error))
            return

        self._set_loading_state(True, "Loading route and precomputing traces...")
        task = ScenarioLoadTask(self.loader, config)
        task.signals.finished.connect(self._on_bundle_loaded)
        task.signals.error.connect(self._on_bundle_load_failed)
        self.thread_pool.start(task)

    def _config_from_controls(self) -> SimulationConfig:
        start_lat, start_lon = parse_coordinate_text(self.start_coord_edit.text())
        goal_lat, goal_lon = parse_coordinate_text(self.goal_coord_edit.text())
        graph_radius_m = None if self.radius_spin.value() == 0 else float(self.radius_spin.value())
        return replace(
            self.config,
            place_name=self.place_edit.text().strip(),
            network_type=self.network_combo.currentText(),
            start_query=self.start_query_edit.text().strip() or None,
            goal_query=self.goal_query_edit.text().strip() or None,
            start_lat=start_lat,
            start_lon=start_lon,
            goal_lat=goal_lat,
            goal_lon=goal_lon,
            graph_radius_m=graph_radius_m,
            animation_speed=float(self.speed_slider.value()),
            batch_steps=self.batch_slider.value(),
            compare_mode=self.compare_checkbox.isChecked(),
            selected_algorithm=self.algorithm_combo.currentText(),
        )

    def _set_loading_state(self, loading: bool, message: str) -> None:
        self.is_loading = loading
        self.pause()
        self.load_button.setEnabled(not loading)
        self.start_button.setEnabled(not loading)
        self.next_button.setEnabled(not loading)
        self.reset_button.setEnabled(not loading)
        self.status_message = message
        self.status_chip.setText(message)
        self.statusBar().showMessage(message)
        if loading:
            QtWidgets.QApplication.setOverrideCursor(
                QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor)
            )
        else:
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()
        self._refresh_ui()

    @QtCore.Slot(object)
    def _on_bundle_loaded(self, bundle: SimulationBundle) -> None:
        self.current_algorithm = normalize_algorithm_name(bundle.config.selected_algorithm)
        self._set_loading_state(False, "Scenario loaded")
        self._set_bundle(bundle)
        self.statusBar().showMessage("Scenario loaded")

    @QtCore.Slot(str)
    def _on_bundle_load_failed(self, message: str) -> None:
        self._set_loading_state(False, "Load failed")
        QtWidgets.QMessageBox.critical(self, "Load failed", message)
        self.statusBar().showMessage(f"Load failed: {message}")

    def _refresh_ui(self) -> None:
        current_event = self.trace.events[self.event_index - 1] if self.event_index else None
        title = self._title_text(current_event)
        self.map_widget.render_state(
            algorithm_name=self.current_algorithm,
            title_text=title,
            history_nodes=sample_nodes(self.history_nodes, self.config.max_history_nodes),
            recent_nodes=list(self.recent_nodes),
            frontier_nodes=sample_nodes(list(self.frontier_nodes), self.config.max_frontier_nodes),
            hot_frontier_nodes=sample_nodes(list(dict.fromkeys(self.recent_frontier_nodes)), 300),
            current_node=None if current_event is None else current_event.current,
            recent_edges=list(self.recent_edges),
            show_path=self.event_index >= len(self.trace.events) and bool(self.trace.path),
        )
        self._update_header(current_event)
        self._update_live_metrics(current_event)
        self._update_session_panel()
        self._highlight_summary_rows()

    def _title_text(self, current_event) -> str:
        if current_event is None:
            return (
                f"{self.current_algorithm} | Ready | Visited 0 | Frontier {len(self.frontier_nodes)} | Runtime 0.0 ms"
            )
        return (
            f"{self.current_algorithm} | Step {current_event.step:,} / {len(self.trace.events):,} | "
            f"Visited {current_event.visited_count:,} | Frontier {current_event.frontier_size:,} | "
            f"Runtime {format_seconds(current_event.elapsed_seconds)}"
        )

    def _update_header(self, current_event) -> None:
        self.scenario_chip.setText(f"{self.config.place_name} | {self.config.network_type}")
        self.algorithm_chip.setText(self.current_algorithm)
        self.status_chip.setText(self.status_message)

    def _update_live_metrics(self, current_event) -> None:
        if current_event is None:
            step = 0
            elapsed = 0.0
            visited_count = 0
            frontier_size = len(self.frontier_nodes)
        else:
            step = current_event.step
            elapsed = current_event.elapsed_seconds
            visited_count = current_event.visited_count
            frontier_size = current_event.frontier_size

        finished = self.event_index >= len(self.trace.events)
        values = {
            "algorithm": self.current_algorithm,
            "status": self.status_message.lower(),
            "step": f"{step:,} / {len(self.trace.events):,}",
            "runtime": format_seconds(elapsed),
            "explored": f"{visited_count:,}",
            "frontier": f"{frontier_size:,}",
            "path_length": format_distance(self.trace.metrics.path_length_m) if finished else "--",
            "path_cost": format_distance(self.trace.metrics.path_cost_m) if finished else "--",
            "optimal": format_optimal(self.trace.metrics.optimal_under_weighting) if finished else "--",
        }
        for key, value in values.items():
            self.metric_labels[key].setText(value)

    def _update_session_panel(self) -> None:
        last_trace = self.traces[self.last_completed_algorithm] if self.last_completed_algorithm else None
        queue_text = ", ".join(self.compare_queue) if self.compare_queue else "--"
        self.session_label.setText(
            "\n".join(
                [
                    f"Compare mode: {'on' if self.compare_checkbox.isChecked() else 'off'}",
                    f"Adaptive stepping: {'on' if self.adaptive_checkbox.isChecked() else 'off'}",
                    f"Speed / batch: {self.speed_slider.value()} / {self.batch_slider.value()}",
                    f"Queued algorithms: {queue_text}",
                    "",
                    f"Last completed: {self.last_completed_algorithm or '--'}",
                    f"Last cost: {format_distance(last_trace.metrics.path_cost_m) if last_trace else '--'}",
                    f"Last optimal: {format_optimal(last_trace.metrics.optimal_under_weighting) if last_trace else '--'}",
                ]
            )
        )

    def _highlight_summary_rows(self) -> None:
        for row, algorithm_name in enumerate(self.traces.keys()):
            is_current = algorithm_name == self.current_algorithm
            is_last = algorithm_name == self.last_completed_algorithm
            base_color = QtGui.QColor("#eef4ff") if is_current else QtGui.QColor("#ffffff")
            if is_last and not is_current:
                base_color = QtGui.QColor("#eefbf2")

            for column in range(self.summary_table.columnCount()):
                item = self.summary_table.item(row, column)
                if item is None:
                    continue
                item.setBackground(base_color)
                font = item.font()
                font.setBold(is_current)
                item.setFont(font)


def launch_app(bundle: SimulationBundle, loader: BundleLoader) -> int:
    """Create the Qt app, show the window, and enter the event loop."""

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    assert app is not None
    app.setApplicationName("Pathfinder Search Dashboard")
    app.setStyle("Fusion")
    window = SearchSimulationWindow(bundle, loader)
    window.show()
    return app.exec() if owns_app else 0
