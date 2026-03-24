"""Dashboard-style matplotlib visualization for the search simulator."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from math import ceil, cos, radians
from typing import Callable

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch
from matplotlib.widgets import Button, CheckButtons, RadioButtons, Slider, TextBox
import networkx as nx

from algorithms import SearchEvent, SearchTrace
from config import SimulationConfig, SUPPORTED_NETWORK_TYPES, normalize_algorithm_name
from map_utils import (
    ResolvedLocation,
    base_graph_segments,
    edge_segment,
    node_xy,
    nodes_to_xy,
    path_segments,
)

APP_BG = "#efe8df"
PANEL_BG = "#fbfaf7"
SIDEBAR_BG = "#f7f2ea"
CARD_BG = "#fffdf9"
CARD_BORDER = "#d9cfc4"
TEXT_MAIN = "#1f2933"
TEXT_MUTED = "#52606d"
GRAPH_COLOR = "#d9dfe4"
VISITED_COLOR = "#4c6fff"
TRAIL_COLOR = "#1d4ed8"
FRONTIER_COLOR = "#f59e0b"
FRONTIER_HOT_COLOR = "#ffbf47"
CURRENT_COLOR = "#0f172a"
PATH_COLOR = "#e63946"
START_COLOR = "#2f9e44"
GOAL_COLOR = "#d62828"


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

    normalised = text.replace(" ", "")
    if "," not in normalised:
        raise ValueError("Coordinates must look like 'lat, lon'.")

    lat_text, lon_text = normalised.split(",", maxsplit=1)
    return float(lat_text), float(lon_text)


def sample_nodes(nodes: list[int] | tuple[int, ...], max_count: int) -> list[int]:
    """Render at most `max_count` nodes to keep playback responsive."""

    if len(nodes) <= max_count:
        return list(nodes)

    step = max(1, ceil(len(nodes) / max_count))
    return list(nodes)[::step]


@dataclass(slots=True)
class SimulationBundle:
    """All route/search state needed to drive the dashboard."""

    config: SimulationConfig
    graph: nx.MultiDiGraph
    traces: dict[str, SearchTrace]
    start_node: int
    goal_node: int
    start_location: ResolvedLocation
    goal_location: ResolvedLocation


BundleLoader = Callable[[SimulationConfig], SimulationBundle]


class SearchDashboard:
    """Polished interactive dashboard for replaying search traces."""

    def __init__(self, bundle: SimulationBundle, loader: BundleLoader) -> None:
        self.loader = loader
        self.bundle = bundle
        self.config = bundle.config
        self.graph = bundle.graph
        self.traces = bundle.traces
        self.current_algorithm = normalize_algorithm_name(self.config.selected_algorithm)
        self.trace = self.traces[self.current_algorithm]
        self.path_segment_cache = {
            name: path_segments(self.graph, trace.path) for name, trace in self.traces.items()
        }

        self.event_index = 0
        self.is_running = False
        self.compare_queue: list[str] = []
        self.last_completed_algorithm: str | None = None
        self.status_message = "Ready"
        self._suspend_widget_events = False

        self.frontier_nodes: set[int] = set()
        self.history_nodes: list[int] = []
        self.recent_nodes: deque[int] = deque()
        self.recent_edges: deque[list[tuple[float, float]]] = deque()
        self.recent_frontier_nodes: deque[int] = deque()

        self.fig = plt.figure(figsize=(18.0, 10.8), facecolor=APP_BG)
        try:
            self.fig.canvas.manager.set_window_title("Pathfinder Search Dashboard")
        except Exception:
            pass

        self.ax_map = self.fig.add_axes([0.03, 0.16, 0.62, 0.79], facecolor=PANEL_BG)
        self.ax_sidebar = self.fig.add_axes([0.68, 0.16, 0.29, 0.79], facecolor=SIDEBAR_BG)
        self.ax_metrics = self.fig.add_axes([0.03, 0.03, 0.94, 0.10], facecolor="none")
        self.ax_sidebar.axis("off")
        self.ax_metrics.axis("off")

        self._draw_sidebar_shell()
        self._draw_metrics_shell()
        self._create_widgets()
        self._build_timer()
        self._set_bundle(bundle, initial=True)

    def _draw_sidebar_shell(self) -> None:
        self.ax_sidebar.clear()
        self.ax_sidebar.set_xlim(0, 1)
        self.ax_sidebar.set_ylim(0, 1)
        self.ax_sidebar.axis("off")

        self._card(self.ax_sidebar, 0.02, 0.53, 0.96, 0.44, "Route Setup")
        self._card(self.ax_sidebar, 0.02, 0.26, 0.96, 0.22, "Algorithm")
        self._card(self.ax_sidebar, 0.02, 0.10, 0.96, 0.12, "Playback")
        self._card(self.ax_sidebar, 0.02, 0.02, 0.96, 0.05, "Notes")

        self.ax_sidebar.text(
            0.05,
            0.045,
            "BFS/DFS ignore weights. Dijkstra and A* are optimal when their returned path matches the best weighted cost.",
            fontsize=9,
            color=TEXT_MUTED,
            va="center",
            ha="left",
            wrap=True,
        )

    def _draw_metrics_shell(self) -> None:
        self.ax_metrics.clear()
        self.ax_metrics.set_xlim(0, 1)
        self.ax_metrics.set_ylim(0, 1)
        self.ax_metrics.axis("off")

        self._card(self.ax_metrics, 0.00, 0.02, 0.26, 0.96, "Live Metrics")
        self._card(self.ax_metrics, 0.28, 0.02, 0.47, 0.96, "Algorithm Summary")
        self._card(self.ax_metrics, 0.77, 0.02, 0.23, 0.96, "Session")

        self.live_metrics_text = self.ax_metrics.text(
            0.02,
            0.72,
            "",
            va="top",
            ha="left",
            fontsize=10.5,
            family="monospace",
            color=TEXT_MAIN,
        )
        self.summary_text = self.ax_metrics.text(
            0.30,
            0.72,
            "",
            va="top",
            ha="left",
            fontsize=9.2,
            family="monospace",
            color=TEXT_MAIN,
        )
        self.session_text = self.ax_metrics.text(
            0.79,
            0.72,
            "",
            va="top",
            ha="left",
            fontsize=10.0,
            family="monospace",
            color=TEXT_MAIN,
        )

    @staticmethod
    def _card(ax, x: float, y: float, width: float, height: float, title: str) -> None:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                width,
                height,
                boxstyle="round,pad=0.012,rounding_size=0.02",
                facecolor=CARD_BG,
                edgecolor=CARD_BORDER,
                linewidth=1.2,
                zorder=0,
            )
        )
        ax.text(
            x + 0.02,
            y + height - 0.05,
            title,
            fontsize=11,
            fontweight="bold",
            color=TEXT_MAIN,
            va="center",
            ha="left",
        )

    def _create_widgets(self) -> None:
        self._suspend_widget_events = True

        self.place_box = TextBox(
            self.fig.add_axes([0.71, 0.87, 0.23, 0.035], facecolor="white"),
            "Place",
            initial=self.config.place_name,
        )
        self.start_query_box = TextBox(
            self.fig.add_axes([0.71, 0.82, 0.23, 0.035], facecolor="white"),
            "Start",
            initial=self.config.start_query or "",
        )
        self.goal_query_box = TextBox(
            self.fig.add_axes([0.71, 0.77, 0.23, 0.035], facecolor="white"),
            "Goal",
            initial=self.config.goal_query or "",
        )
        self.start_coord_box = TextBox(
            self.fig.add_axes([0.71, 0.72, 0.23, 0.035], facecolor="white"),
            "Start lat,lon",
            initial=self._format_coord_pair(self.config.start_lat, self.config.start_lon),
        )
        self.goal_coord_box = TextBox(
            self.fig.add_axes([0.71, 0.67, 0.23, 0.035], facecolor="white"),
            "Goal lat,lon",
            initial=self._format_coord_pair(self.config.goal_lat, self.config.goal_lon),
        )
        self.graph_radius_box = TextBox(
            self.fig.add_axes([0.83, 0.61, 0.11, 0.035], facecolor="white"),
            "Radius m",
            initial="" if self.config.graph_radius_m is None else f"{self.config.graph_radius_m:.0f}",
        )
        self.network_selector = RadioButtons(
            self.fig.add_axes([0.71, 0.58, 0.10, 0.07], facecolor=CARD_BG),
            SUPPORTED_NETWORK_TYPES,
            active=SUPPORTED_NETWORK_TYPES.index(self.config.network_type),
        )
        self.apply_route_button = Button(
            self.fig.add_axes([0.71, 0.54, 0.23, 0.04]),
            "Apply Route",
            color="#e8f1ec",
            hovercolor="#d6eadf",
        )

        self.algorithm_selector = RadioButtons(
            self.fig.add_axes([0.71, 0.40, 0.23, 0.10], facecolor=CARD_BG),
            list(self.traces.keys()),
            active=list(self.traces.keys()).index(self.current_algorithm),
        )
        self.compare_checks = CheckButtons(
            self.fig.add_axes([0.71, 0.35, 0.23, 0.05], facecolor=CARD_BG),
            ["Compare mode", "Adaptive stepping"],
            [self.config.compare_mode, True],
        )
        self.speed_slider = Slider(
            self.fig.add_axes([0.71, 0.32, 0.23, 0.022], facecolor=CARD_BG),
            "Speed",
            valmin=1.0,
            valmax=40.0,
            valinit=self.config.animation_speed,
            valstep=1.0,
        )
        self.batch_slider = Slider(
            self.fig.add_axes([0.71, 0.285, 0.23, 0.022], facecolor=CARD_BG),
            "Batch",
            valmin=1.0,
            valmax=25.0,
            valinit=float(self.config.batch_steps),
            valstep=1.0,
        )

        self.start_button = Button(
            self.fig.add_axes([0.71, 0.18, 0.11, 0.045]),
            "Start / Resume",
            color="#e7f5eb",
            hovercolor="#d6eddc",
        )
        self.pause_button = Button(
            self.fig.add_axes([0.83, 0.18, 0.11, 0.045]),
            "Pause / Stop",
            color="#fdeeee",
            hovercolor="#f9dcdc",
        )
        self.next_button = Button(
            self.fig.add_axes([0.71, 0.125, 0.11, 0.045]),
            "Next Step",
            color="#fdf3df",
            hovercolor="#f6e4b7",
        )
        self.reset_button = Button(
            self.fig.add_axes([0.83, 0.125, 0.11, 0.045]),
            "Reset",
            color="#eef2f6",
            hovercolor="#dde5ec",
        )

        self.apply_route_button.on_clicked(self.on_apply_route)
        self.algorithm_selector.on_clicked(self.on_algorithm_change)
        self.compare_checks.on_clicked(self.on_compare_change)
        self.speed_slider.on_changed(self.on_speed_change)
        self.batch_slider.on_changed(self.on_batch_change)
        self.start_button.on_clicked(self.on_start)
        self.pause_button.on_clicked(self.on_pause)
        self.next_button.on_clicked(self.on_next)
        self.reset_button.on_clicked(self.on_reset)

        self._suspend_widget_events = False

    def _build_timer(self) -> None:
        self.timer = self.fig.canvas.new_timer(interval=33)
        self.timer.add_callback(self._advance_from_timer)

    @staticmethod
    def _format_coord_pair(lat: float | None, lon: float | None) -> str:
        if lat is None or lon is None:
            return ""
        return f"{lat:.6f}, {lon:.6f}"

    def _set_bundle(self, bundle: SimulationBundle, initial: bool = False) -> None:
        self.bundle = bundle
        self.config = bundle.config
        self.graph = bundle.graph
        self.traces = bundle.traces
        self.path_segment_cache = {
            name: path_segments(self.graph, trace.path) for name, trace in self.traces.items()
        }

        if self.current_algorithm not in self.traces:
            self.current_algorithm = normalize_algorithm_name(self.config.selected_algorithm)
        else:
            self.current_algorithm = normalize_algorithm_name(self.current_algorithm)

        self.trace = self.traces[self.current_algorithm]
        self._sync_controls_from_config()
        self._rebuild_map_artists()
        self._reset_visual_state()

        if not initial:
            self.status_message = "Route loaded"
            self._refresh_dashboard()

    def _sync_controls_from_config(self) -> None:
        self._suspend_widget_events = True
        self.place_box.set_val(self.config.place_name)
        self.start_query_box.set_val(self.config.start_query or "")
        self.goal_query_box.set_val(self.config.goal_query or "")
        self.start_coord_box.set_val(
            self._format_coord_pair(self.config.start_lat, self.config.start_lon)
        )
        self.goal_coord_box.set_val(
            self._format_coord_pair(self.config.goal_lat, self.config.goal_lon)
        )
        self.graph_radius_box.set_val(
            "" if self.config.graph_radius_m is None else f"{self.config.graph_radius_m:.0f}"
        )

        network_index = SUPPORTED_NETWORK_TYPES.index(self.config.network_type)
        if self.network_selector.value_selected != self.config.network_type:
            self.network_selector.set_active(network_index)

        algorithm_names = list(self.traces.keys())
        active_index = algorithm_names.index(self.current_algorithm)
        if self.algorithm_selector.value_selected != self.current_algorithm:
            self.algorithm_selector.set_active(active_index)

        compare_enabled, adaptive_enabled = self.compare_checks.get_status()
        if compare_enabled != self.config.compare_mode:
            self.compare_checks.set_active(0)
        if adaptive_enabled is False:
            self.compare_checks.set_active(1)

        if int(round(self.speed_slider.val)) != int(round(self.config.animation_speed)):
            self.speed_slider.set_val(self.config.animation_speed)
        if int(round(self.batch_slider.val)) != int(round(self.config.batch_steps)):
            self.batch_slider.set_val(float(self.config.batch_steps))
        self._suspend_widget_events = False

    def _rebuild_map_artists(self) -> None:
        self.ax_map.clear()
        self.ax_map.set_facecolor(PANEL_BG)
        self.ax_map.set_xticks([])
        self.ax_map.set_yticks([])
        for spine in self.ax_map.spines.values():
            spine.set_visible(False)

        self.base_edges = LineCollection(
            base_graph_segments(self.graph),
            colors=GRAPH_COLOR,
            linewidths=0.75,
            alpha=0.9,
            zorder=1,
        )
        self.recent_edge_collection = LineCollection(
            [],
            colors=TRAIL_COLOR,
            linewidths=2.3,
            alpha=0.82,
            zorder=3,
        )
        self.path_collection = LineCollection(
            [],
            colors=PATH_COLOR,
            linewidths=3.4,
            alpha=0.95,
            zorder=6,
        )
        self.ax_map.add_collection(self.base_edges)
        self.ax_map.add_collection(self.recent_edge_collection)
        self.ax_map.add_collection(self.path_collection)

        self.history_scatter = self.ax_map.scatter(
            [],
            [],
            s=10,
            c=VISITED_COLOR,
            alpha=0.18,
            edgecolors="none",
            zorder=2,
        )
        self.recent_scatter = self.ax_map.scatter(
            [],
            [],
            s=34,
            c=TRAIL_COLOR,
            alpha=0.92,
            edgecolors="white",
            linewidths=0.2,
            zorder=4,
        )
        self.frontier_scatter = self.ax_map.scatter(
            [],
            [],
            s=26,
            c=FRONTIER_COLOR,
            alpha=0.45,
            edgecolors="none",
            zorder=4,
        )
        self.frontier_hot_scatter = self.ax_map.scatter(
            [],
            [],
            s=52,
            c=FRONTIER_HOT_COLOR,
            alpha=0.95,
            edgecolors="white",
            linewidths=0.25,
            zorder=5,
        )
        self.current_scatter = self.ax_map.scatter(
            [],
            [],
            s=120,
            c=CURRENT_COLOR,
            alpha=1.0,
            edgecolors="white",
            linewidths=0.8,
            zorder=7,
        )

        start_x, start_y = node_xy(self.graph, self.bundle.start_node)
        goal_x, goal_y = node_xy(self.graph, self.bundle.goal_node)
        self.ax_map.scatter(
            [start_x],
            [start_y],
            s=130,
            c=START_COLOR,
            marker="o",
            edgecolors="white",
            linewidths=1.0,
            zorder=8,
        )
        self.ax_map.scatter(
            [goal_x],
            [goal_y],
            s=145,
            c=GOAL_COLOR,
            marker="*",
            edgecolors="white",
            linewidths=0.9,
            zorder=8,
        )
        self.ax_map.annotate(
            "Start",
            (start_x, start_y),
            xytext=(10, 10),
            textcoords="offset points",
            fontsize=9,
            color=TEXT_MAIN,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=START_COLOR, lw=0.8),
        )
        self.ax_map.annotate(
            "Goal",
            (goal_x, goal_y),
            xytext=(10, -18),
            textcoords="offset points",
            fontsize=9,
            color=TEXT_MAIN,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=GOAL_COLOR, lw=0.8),
        )

        legend_handles = [
            Line2D([0], [0], color=GRAPH_COLOR, lw=2, label="Unexplored graph"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor=VISITED_COLOR, alpha=0.35, markersize=8, label="Visited"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor=FRONTIER_HOT_COLOR, markersize=8, label="Frontier"),
            Line2D([0], [0], color=PATH_COLOR, lw=3, label="Final path"),
        ]
        self.ax_map.legend(
            handles=legend_handles,
            loc="lower left",
            frameon=True,
            fontsize=9,
            facecolor="white",
            edgecolor=CARD_BORDER,
        )

        all_x = [float(data["x"]) for _node, data in self.graph.nodes(data=True)]
        all_y = [float(data["y"]) for _node, data in self.graph.nodes(data=True)]
        x_margin = max((max(all_x) - min(all_x)) * 0.03, 0.001)
        y_margin = max((max(all_y) - min(all_y)) * 0.03, 0.001)
        mean_lat = sum(all_y) / len(all_y)
        self.ax_map.set_xlim(min(all_x) - x_margin, max(all_x) + x_margin)
        self.ax_map.set_ylim(min(all_y) - y_margin, max(all_y) + y_margin)
        self.ax_map.set_aspect(1.0 / max(cos(radians(mean_lat)), 0.01))

        self.map_badge = self.ax_map.text(
            0.995,
            1.015,
            f"{self.config.place_name}  |  {self.config.network_type}",
            transform=self.ax_map.transAxes,
            ha="right",
            va="bottom",
            fontsize=10,
            color=TEXT_MUTED,
        )

    def _reset_visual_state(self) -> None:
        self.pause()
        self.trace = self.traces[self.current_algorithm]
        self.event_index = 0
        self.frontier_nodes = set(self.trace.initial_frontier)
        self.history_nodes = []
        self.recent_nodes = deque(maxlen=self.config.trail_length)
        self.recent_edges = deque(maxlen=max(40, self.config.trail_length // 2))
        self.recent_frontier_nodes = deque(maxlen=max(30, self.config.trail_length // 2))
        self.history_stride = max(1, ceil(len(self.trace.events) / self.config.max_history_nodes))
        self.status_message = "Ready"
        self.compare_queue = []
        self._refresh_dashboard()

    def _advance_from_timer(self) -> None:
        if not self.is_running:
            return

        steps = self._steps_per_tick()
        active = self._advance_steps(steps)
        self._refresh_dashboard()
        if not active:
            self.pause()

    def _steps_per_tick(self) -> int:
        speed_steps = int(round(self.speed_slider.val))
        batch_steps = int(round(self.batch_slider.val))
        adaptive_enabled = self.compare_checks.get_status()[1]
        adaptive_scale = 1

        if adaptive_enabled:
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
        self.status_message = (
            "Found path" if self.trace.metrics.found else "Search failed"
        )

    def _maybe_advance_compare_queue(self) -> bool:
        compare_mode_enabled = self.compare_checks.get_status()[0]
        if not compare_mode_enabled or not self.compare_queue:
            return False

        next_algorithm = self.compare_queue.pop(0)
        self._activate_algorithm(
            next_algorithm,
            reset_status="Ready",
            clear_compare_queue=False,
        )
        return True

    def _apply_event(self, event: SearchEvent) -> None:
        if event.step == 1 or event.step % self.history_stride == 0 or event.status != "searching":
            self.history_nodes.append(event.current)
        self.recent_nodes.append(event.current)

        for node in event.frontier_removed:
            self.frontier_nodes.discard(node)
        for node in event.frontier_added:
            self.frontier_nodes.add(node)
            self.recent_frontier_nodes.append(node)

        if event.parent_edge is not None:
            self.recent_edges.append(edge_segment(self.graph, *event.parent_edge))

        self.status_message = (
            "Searching"
            if event.status == "searching"
            else ("Found path" if event.status == "found" else "Search failed")
        )

    def on_start(self, _event) -> None:
        if self.event_index >= len(self.trace.events):
            self._activate_algorithm(self.current_algorithm, reset_status="Ready")

        if self.compare_checks.get_status()[0] and not self.compare_queue:
            self.compare_queue = self._compare_sequence()[1:]

        self.is_running = True
        self.timer.start()

    def pause(self) -> None:
        self.is_running = False
        self.timer.stop()

    def on_pause(self, _event) -> None:
        self.pause()
        self._refresh_dashboard()

    def on_next(self, _event) -> None:
        self.pause()
        self._advance_steps(1)
        self._refresh_dashboard()

    def on_reset(self, _event) -> None:
        self._activate_algorithm(self.current_algorithm, reset_status="Ready")

    def on_speed_change(self, _value: float) -> None:
        if self._suspend_widget_events:
            return
        self.config = replace(self.config, animation_speed=float(self.speed_slider.val))
        self._refresh_dashboard()

    def on_batch_change(self, _value: float) -> None:
        if self._suspend_widget_events:
            return
        self.config = replace(self.config, batch_steps=int(round(self.batch_slider.val)))
        self._refresh_dashboard()

    def on_compare_change(self, _label: str) -> None:
        if self._suspend_widget_events:
            return
        compare_mode_enabled = self.compare_checks.get_status()[0]
        self.config = replace(self.config, compare_mode=compare_mode_enabled)
        if not compare_mode_enabled:
            self.compare_queue = []
        self._refresh_dashboard()

    def on_algorithm_change(self, label: str) -> None:
        if self._suspend_widget_events:
            return
        self._activate_algorithm(normalize_algorithm_name(label), reset_status="Ready")

    def _activate_algorithm(
        self,
        algorithm: str,
        reset_status: str,
        clear_compare_queue: bool = True,
    ) -> None:
        self.pause()
        self.current_algorithm = normalize_algorithm_name(algorithm)
        self.trace = self.traces[self.current_algorithm]
        self.event_index = 0
        self.frontier_nodes = set(self.trace.initial_frontier)
        self.history_nodes = []
        self.recent_nodes = deque(maxlen=self.config.trail_length)
        self.recent_edges = deque(maxlen=max(40, self.config.trail_length // 2))
        self.recent_frontier_nodes = deque(maxlen=max(30, self.config.trail_length // 2))
        self.history_stride = max(1, ceil(len(self.trace.events) / self.config.max_history_nodes))
        self.status_message = reset_status
        if clear_compare_queue:
            self.compare_queue = []
        self._refresh_dashboard()

    def _compare_sequence(self) -> list[str]:
        algorithms = list(self.traces.keys())
        start_index = algorithms.index(self.current_algorithm)
        return algorithms[start_index:] + algorithms[:start_index]

    def on_apply_route(self, _event) -> None:
        if self._suspend_widget_events:
            return

        try:
            start_lat, start_lon = parse_coordinate_text(self.start_coord_box.text)
            goal_lat, goal_lon = parse_coordinate_text(self.goal_coord_box.text)
            radius_text = self.graph_radius_box.text.strip()
            graph_radius_m = None if not radius_text else float(radius_text)

            updated_config = replace(
                self.config,
                place_name=self.place_box.text.strip(),
                network_type=self.network_selector.value_selected,
                start_query=self.start_query_box.text.strip() or None,
                goal_query=self.goal_query_box.text.strip() or None,
                start_lat=start_lat,
                start_lon=start_lon,
                goal_lat=goal_lat,
                goal_lon=goal_lon,
                graph_radius_m=graph_radius_m,
                animation_speed=float(self.speed_slider.val),
                batch_steps=int(round(self.batch_slider.val)),
                compare_mode=self.compare_checks.get_status()[0],
                selected_algorithm=self.current_algorithm,
            )
            updated_config.validate()

            self.pause()
            self.status_message = "Loading route and precomputing traces..."
            self._refresh_dashboard()
            self.fig.canvas.draw_idle()
            plt.pause(0.001)

            new_bundle = self.loader(updated_config)
            self.current_algorithm = normalize_algorithm_name(updated_config.selected_algorithm)
            self._set_bundle(new_bundle)
        except Exception as error:
            self.status_message = f"Load failed: {error}"
            self._refresh_dashboard()

    def _refresh_dashboard(self) -> None:
        current_event = self.trace.events[self.event_index - 1] if self.event_index else None
        self._update_map_artists(current_event)
        self._update_metrics(current_event)
        self.fig.canvas.draw_idle()

    def _update_map_artists(self, current_event: SearchEvent | None) -> None:
        self.history_scatter.set_offsets(nodes_to_xy(self.graph, self.history_nodes))
        self.recent_scatter.set_offsets(nodes_to_xy(self.graph, list(self.recent_nodes)))
        self.frontier_scatter.set_offsets(
            nodes_to_xy(
                self.graph,
                sample_nodes(list(self.frontier_nodes), self.config.max_frontier_nodes),
            )
        )
        self.frontier_hot_scatter.set_offsets(
            nodes_to_xy(
                self.graph,
                sample_nodes(list(dict.fromkeys(self.recent_frontier_nodes)), 300),
            )
        )

        if current_event is None:
            self.current_scatter.set_offsets(nodes_to_xy(self.graph, []))
        else:
            self.current_scatter.set_offsets(nodes_to_xy(self.graph, [current_event.current]))

        self.recent_edge_collection.set_segments(list(self.recent_edges))
        if self.event_index >= len(self.trace.events) and self.trace.path:
            self.path_collection.set_segments(self.path_segment_cache[self.current_algorithm])
        else:
            self.path_collection.set_segments([])

        self.ax_map.set_title(self._title_text(current_event), fontsize=15, loc="left", pad=14)

    def _title_text(self, current_event: SearchEvent | None) -> str:
        if current_event is None:
            return (
                f"{self.current_algorithm} | Ready | Visited 0 | Frontier {len(self.frontier_nodes)} | Runtime 0.0 ms"
            )

        return (
            f"{self.current_algorithm} | Step {current_event.step:,} / {len(self.trace.events):,} | "
            f"Visited {current_event.visited_count:,} | Frontier {current_event.frontier_size:,} | "
            f"Runtime {format_seconds(current_event.elapsed_seconds)}"
        )

    def _update_metrics(self, current_event: SearchEvent | None) -> None:
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
        self.live_metrics_text.set_text(
            "\n".join(
                [
                    f"algorithm : {self.current_algorithm}",
                    f"status    : {self.status_message.lower()}",
                    f"step      : {step:,} / {len(self.trace.events):,}",
                    f"runtime   : {format_seconds(elapsed)}",
                    f"explored  : {visited_count:,}",
                    f"frontier  : {frontier_size:,}",
                    f"path cost : {format_distance(self.trace.metrics.path_cost_m) if finished else '--'}",
                    f"optimal   : {format_optimal(self.trace.metrics.optimal_under_weighting) if finished else '--'}",
                ]
            )
        )

        summary_lines = [
            "alg                       status   visited   runtime   cost      optimal",
        ]
        for name, trace in self.traces.items():
            prefix = ">" if name == self.current_algorithm else " "
            status_label = "found" if trace.metrics.found else "failed"
            summary_lines.append(
                (
                    f"{prefix} {name:<24}"
                    f"{status_label:<8}"
                    f"{trace.metrics.visited_count:>8,} "
                    f"{format_seconds(trace.metrics.runtime_seconds):>8} "
                    f"{format_distance(trace.metrics.path_cost_m):>9} "
                    f"{format_optimal(trace.metrics.optimal_under_weighting):>7}"
                )
            )
        self.summary_text.set_text("\n".join(summary_lines))

        compare_active, adaptive_active = self.compare_checks.get_status()
        last_trace = self.traces[self.last_completed_algorithm] if self.last_completed_algorithm else None
        session_lines = [
            f"compare : {'on' if compare_active else 'off'}",
            f"adaptive: {'on' if adaptive_active else 'off'}",
            f"speed   : {int(round(self.speed_slider.val))}",
            f"batch   : {int(round(self.batch_slider.val))}",
            f"queue   : {', '.join(self.compare_queue) if self.compare_queue else '--'}",
            "",
            f"last    : {self.last_completed_algorithm or '--'}",
            f"cost    : {format_distance(last_trace.metrics.path_cost_m) if last_trace else '--'}",
            f"optimal : {format_optimal(last_trace.metrics.optimal_under_weighting) if last_trace else '--'}",
        ]
        self.session_text.set_text("\n".join(session_lines))

    def show(self) -> None:
        plt.show()
