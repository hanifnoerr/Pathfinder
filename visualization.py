"""PyQtGraph-based map visualization helpers for the search simulator."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, cos, radians

import networkx as nx
import numpy as np
import pyqtgraph as pg
from PySide6 import QtWidgets

from algorithms import SearchTrace
from config import SimulationConfig
from map_utils import ResolvedLocation

pg.setConfigOptions(antialias=False)

MAP_BG = "#f7f8fb"
GRAPH_COLOR = "#d9dde4"
VISITED_COLOR = "#4c6fff"
TRAIL_COLOR = "#1d4ed8"
FRONTIER_COLOR = "#f59e0b"
FRONTIER_HOT_COLOR = "#ffbf47"
CURRENT_COLOR = "#0f172a"
PATH_COLOR = "#e63946"
START_COLOR = "#2f9e44"
GOAL_COLOR = "#d62828"
TEXT_MAIN = "#1f2933"
TEXT_MUTED = "#52606d"


def format_seconds(value: float) -> str:
    """Pretty-print timing values."""

    if value < 1.0:
        return f"{value * 1000.0:.1f} ms"
    return f"{value:.2f} s"


def format_distance(value: float | None) -> str:
    """Pretty-print distance/cost values."""

    if value is None:
        return "--"
    if value >= 1000.0:
        return f"{value / 1000.0:.2f} km"
    return f"{value:.0f} m"


def format_optimal(value: bool | None) -> str:
    """Pretty-print optimality status."""

    if value is None:
        return "--"
    return "yes" if value else "no"


def parse_coordinate_text(value: str) -> tuple[float | None, float | None]:
    """Parse an optional 'lat, lon' text field."""

    text = value.strip()
    if not text:
        return None, None

    cleaned = text.replace(" ", "")
    if "," not in cleaned:
        raise ValueError("Coordinates must look like 'lat, lon'.")

    lat_text, lon_text = cleaned.split(",", maxsplit=1)
    return float(lat_text), float(lon_text)


def sample_nodes(nodes: list[int] | tuple[int, ...], max_count: int) -> list[int]:
    """Render at most `max_count` nodes to keep playback responsive."""

    if len(nodes) <= max_count:
        return list(nodes)

    step = max(1, ceil(len(nodes) / max_count))
    return list(nodes)[::step]


@dataclass(slots=True)
class SimulationBundle:
    """All route/search state needed to drive the desktop app."""

    config: SimulationConfig
    graph: nx.MultiDiGraph
    traces: dict[str, SearchTrace]
    start_node: int
    goal_node: int
    start_location: ResolvedLocation
    goal_location: ResolvedLocation


def _empty_points() -> np.ndarray:
    return np.empty((0, 2), dtype=float)


def _empty_polyline() -> tuple[np.ndarray, np.ndarray]:
    return np.empty(0, dtype=float), np.empty(0, dtype=float)


class SearchMapWidget(QtWidgets.QWidget):
    """Fast layered PyQtGraph map view with reusable overlay items."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.plot_widget = pg.PlotWidget(background=MAP_BG)
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.hideButtons()
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.getPlotItem().hideAxis("left")
        self.plot_widget.getPlotItem().hideAxis("bottom")
        self.plot_widget.getViewBox().setDefaultPadding(0.02)
        layout.addWidget(self.plot_widget)

        self.base_item = pg.PlotCurveItem(
            pen=pg.mkPen(GRAPH_COLOR, width=1.0),
            connect="finite",
            skipFiniteCheck=True,
        )
        self.recent_edges_item = pg.PlotCurveItem(
            pen=pg.mkPen(TRAIL_COLOR, width=2.0),
            connect="finite",
            skipFiniteCheck=True,
        )
        self.path_item = pg.PlotCurveItem(
            pen=pg.mkPen(PATH_COLOR, width=3.4),
            connect="finite",
            skipFiniteCheck=True,
        )

        self.history_points = pg.ScatterPlotItem(
            size=6,
            brush=pg.mkBrush(76, 111, 255, 55),
            pen=None,
            pxMode=True,
        )
        self.recent_points = pg.ScatterPlotItem(
            size=10,
            brush=pg.mkBrush(TRAIL_COLOR),
            pen=pg.mkPen("#ffffff", width=0.6),
            pxMode=True,
        )
        self.frontier_points = pg.ScatterPlotItem(
            size=8,
            brush=pg.mkBrush(245, 158, 11, 90),
            pen=None,
            pxMode=True,
        )
        self.frontier_hot_points = pg.ScatterPlotItem(
            size=12,
            brush=pg.mkBrush(FRONTIER_HOT_COLOR),
            pen=pg.mkPen("#ffffff", width=0.6),
            pxMode=True,
        )
        self.current_point = pg.ScatterPlotItem(
            size=16,
            brush=pg.mkBrush(CURRENT_COLOR),
            pen=pg.mkPen("#ffffff", width=0.8),
            pxMode=True,
        )
        self.start_point = pg.ScatterPlotItem(
            size=14,
            brush=pg.mkBrush(START_COLOR),
            pen=pg.mkPen("#ffffff", width=0.8),
            pxMode=True,
        )
        self.goal_point = pg.ScatterPlotItem(
            size=16,
            brush=pg.mkBrush(GOAL_COLOR),
            pen=pg.mkPen("#ffffff", width=0.8),
            symbol="star",
            pxMode=True,
        )
        self.start_label = pg.TextItem(color=TEXT_MAIN, anchor=(0, 1))
        self.goal_label = pg.TextItem(color=TEXT_MAIN, anchor=(0, 0))

        for item in (
            self.base_item,
            self.history_points,
            self.recent_edges_item,
            self.recent_points,
            self.frontier_points,
            self.frontier_hot_points,
            self.path_item,
            self.current_point,
            self.start_point,
            self.goal_point,
            self.start_label,
            self.goal_label,
        ):
            self.plot_widget.addItem(item)

        self.bundle: SimulationBundle | None = None
        self.node_positions: dict[int, tuple[float, float]] = {}
        self.path_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.lon_scale = 1.0

    def set_bundle(self, bundle: SimulationBundle) -> None:
        """Load a new graph bundle into the view."""

        self.bundle = bundle
        mean_lat = float(
            sum(float(data["y"]) for _node, data in bundle.graph.nodes(data=True))
            / max(bundle.graph.number_of_nodes(), 1)
        )
        self.lon_scale = max(cos(radians(mean_lat)), 0.01)
        self.node_positions = {
            int(node): (
                float(data["x"]) * self.lon_scale,
                float(data["y"]),
            )
            for node, data in bundle.graph.nodes(data=True)
        }

        base_x, base_y = self._graph_polyline(bundle.graph)
        self.base_item.setData(base_x, base_y)

        self.path_cache = {
            name: self._path_polyline(trace.path)
            for name, trace in bundle.traces.items()
        }

        start_point = self.node_positions[bundle.start_node]
        goal_point = self.node_positions[bundle.goal_node]
        self.start_point.setData(pos=np.array([start_point], dtype=float))
        self.goal_point.setData(pos=np.array([goal_point], dtype=float))
        self.start_label.setText("Start")
        self.goal_label.setText("Goal")
        self.start_label.setPos(start_point[0], start_point[1])
        self.goal_label.setPos(goal_point[0], goal_point[1])

        x_values = np.array([position[0] for position in self.node_positions.values()], dtype=float)
        y_values = np.array([position[1] for position in self.node_positions.values()], dtype=float)
        x_margin = max((float(np.max(x_values)) - float(np.min(x_values))) * 0.03, 0.001)
        y_margin = max((float(np.max(y_values)) - float(np.min(y_values))) * 0.03, 0.001)
        self.plot_widget.setLimits(
            xMin=float(np.min(x_values)) - x_margin,
            xMax=float(np.max(x_values)) + x_margin,
            yMin=float(np.min(y_values)) - y_margin,
            yMax=float(np.max(y_values)) + y_margin,
        )
        self.plot_widget.setRange(
            xRange=(float(np.min(x_values)) - x_margin, float(np.max(x_values)) + x_margin),
            yRange=(float(np.min(y_values)) - y_margin, float(np.max(y_values)) + y_margin),
            padding=0.0,
        )

        self.clear_dynamic_state()

    def clear_dynamic_state(self) -> None:
        """Clear search overlays while preserving the loaded map."""

        self.history_points.setData(pos=_empty_points())
        self.recent_points.setData(pos=_empty_points())
        self.frontier_points.setData(pos=_empty_points())
        self.frontier_hot_points.setData(pos=_empty_points())
        self.current_point.setData(pos=_empty_points())
        self.recent_edges_item.setData(*_empty_polyline())
        self.path_item.setData(*_empty_polyline())
        self.plot_widget.setTitle(
            "<span style='font-size:14pt; color:#1f2933;'>Ready</span>"
        )

    def render_state(
        self,
        *,
        algorithm_name: str,
        title_text: str,
        history_nodes: list[int],
        recent_nodes: list[int],
        frontier_nodes: list[int],
        hot_frontier_nodes: list[int],
        current_node: int | None,
        recent_edges: list[tuple[int, int]],
        show_path: bool,
    ) -> None:
        """Update the dynamic search overlays."""

        self.plot_widget.setTitle(
            f"<span style='font-size:14pt; color:{TEXT_MAIN};'>{title_text}</span>"
        )
        self.history_points.setData(pos=self._points_for_nodes(history_nodes))
        self.recent_points.setData(pos=self._points_for_nodes(recent_nodes))
        self.frontier_points.setData(pos=self._points_for_nodes(frontier_nodes))
        self.frontier_hot_points.setData(pos=self._points_for_nodes(hot_frontier_nodes))
        if current_node is None:
            self.current_point.setData(pos=_empty_points())
        else:
            self.current_point.setData(pos=self._points_for_nodes([current_node]))

        self.recent_edges_item.setData(*self._edges_polyline(recent_edges))
        if show_path:
            self.path_item.setData(*self.path_cache.get(algorithm_name, _empty_polyline()))
        else:
            self.path_item.setData(*_empty_polyline())

    def _graph_polyline(self, graph: nx.MultiDiGraph) -> tuple[np.ndarray, np.ndarray]:
        segments = [
            (self.node_positions[int(source)], self.node_positions[int(target)])
            for source, target in nx.Graph(graph).edges()
        ]
        return self._segments_to_polyline(segments)

    def _path_polyline(self, path: list[int]) -> tuple[np.ndarray, np.ndarray]:
        if len(path) < 2:
            return _empty_polyline()
        segments = [
            (self.node_positions[int(source)], self.node_positions[int(target)])
            for source, target in zip(path[:-1], path[1:])
        ]
        return self._segments_to_polyline(segments)

    def _edges_polyline(self, edges: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
        if not edges:
            return _empty_polyline()
        segments = [
            (self.node_positions[int(source)], self.node_positions[int(target)])
            for source, target in edges
        ]
        return self._segments_to_polyline(segments)

    def _points_for_nodes(self, nodes: list[int] | tuple[int, ...]) -> np.ndarray:
        if not nodes:
            return _empty_points()
        return np.array([self.node_positions[int(node)] for node in nodes], dtype=float)

    @staticmethod
    def _segments_to_polyline(
        segments: list[tuple[tuple[float, float], tuple[float, float]]]
    ) -> tuple[np.ndarray, np.ndarray]:
        if not segments:
            return _empty_polyline()

        x_values = np.empty(len(segments) * 3, dtype=float)
        y_values = np.empty(len(segments) * 3, dtype=float)
        cursor = 0
        for start_point, end_point in segments:
            x_values[cursor] = start_point[0]
            y_values[cursor] = start_point[1]
            x_values[cursor + 1] = end_point[0]
            y_values[cursor + 1] = end_point[1]
            x_values[cursor + 2] = np.nan
            y_values[cursor + 2] = np.nan
            cursor += 3
        return x_values, y_values
