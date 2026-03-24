"""Interactive graph search simulator on real OpenStreetMap street networks."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
from math import cos, radians
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.widgets import Button, RadioButtons, Slider
import networkx as nx

from algorithms import SearchEvent, SearchTrace, compute_search_traces
from config import DEFAULT_CONFIG, SimulationConfig, normalize_algorithm_name
from map_utils import (
    ResolvedLocation,
    base_graph_segments,
    edge_segment,
    load_graph,
    nearest_graph_node,
    node_xy,
    nodes_to_xy,
    path_segments,
    resolve_location,
)

BACKGROUND_COLOR = "#f7f4ee"
GRAPH_COLOR = "#d7d2ca"
VISITED_COLOR = "#2a9d8f"
FRONTIER_COLOR = "#f4a261"
CURRENT_COLOR = "#457b9d"
PATH_COLOR = "#d62828"
START_COLOR = "#2a9d8f"
GOAL_COLOR = "#8d0801"


def parse_args() -> argparse.Namespace:
    """Parse command-line overrides for the default configuration."""

    parser = argparse.ArgumentParser(
        description="Visualize graph search algorithms on a real OpenStreetMap network."
    )
    parser.add_argument("--place-name", dest="place_name")
    parser.add_argument("--network-type", dest="network_type")
    parser.add_argument("--start-query", dest="start_query")
    parser.add_argument("--goal-query", dest="goal_query")
    parser.add_argument("--start-lat", dest="start_lat", type=float)
    parser.add_argument("--start-lon", dest="start_lon", type=float)
    parser.add_argument("--goal-lat", dest="goal_lat", type=float)
    parser.add_argument("--goal-lon", dest="goal_lon", type=float)
    parser.add_argument("--animation-speed", dest="animation_speed", type=float)
    parser.add_argument("--algorithm", dest="selected_algorithm")
    parser.add_argument("--graph-radius-m", dest="graph_radius_m", type=float)
    parser.add_argument("--graph-buffer-m", dest="graph_buffer_m", type=float)
    parser.add_argument("--cache-dir", dest="cache_dir", type=Path)
    parser.add_argument("--metrics-csv", dest="metrics_csv_path", type=Path)
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Precompute the traces and print/export metrics without opening the window.",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> SimulationConfig:
    """Merge CLI overrides on top of the editable defaults in config.py."""

    values = asdict(DEFAULT_CONFIG)
    for key, value in vars(args).items():
        if value is not None:
            values[key] = value

    config = replace(DEFAULT_CONFIG, **values)
    config.selected_algorithm = normalize_algorithm_name(config.selected_algorithm)
    config.validate()
    return config


def format_seconds(value: float) -> str:
    """Pretty-print timing values."""

    if value < 1.0:
        return f"{value * 1000.0:.1f} ms"
    return f"{value:.2f} s"


def format_distance(value: float | None) -> str:
    """Pretty-print route length values."""

    if value is None:
        return "--"
    if value >= 1000.0:
        return f"{value / 1000.0:.2f} km"
    return f"{value:.0f} m"


def export_metrics_csv(csv_path: Path, traces: dict[str, SearchTrace]) -> None:
    """Persist summary metrics for later comparison."""

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "algorithm",
                "found",
                "steps",
                "visited_count",
                "frontier_peak",
                "runtime_seconds",
                "path_length_m",
                "explored_fraction",
            ],
        )
        writer.writeheader()
        for trace in traces.values():
            metrics = trace.metrics
            writer.writerow(
                {
                    "algorithm": metrics.algorithm,
                    "found": metrics.found,
                    "steps": metrics.steps,
                    "visited_count": metrics.visited_count,
                    "frontier_peak": metrics.frontier_peak,
                    "runtime_seconds": f"{metrics.runtime_seconds:.8f}",
                    "path_length_m": (
                        "" if metrics.path_length_m is None else f"{metrics.path_length_m:.3f}"
                    ),
                    "explored_fraction": f"{metrics.explored_fraction:.6f}",
                }
            )


class SearchSimulator:
    """Interactive matplotlib UI that replays precomputed search traces."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        traces: dict[str, SearchTrace],
        selected_algorithm: str,
        start_node: int,
        goal_node: int,
        start_location: ResolvedLocation,
        goal_location: ResolvedLocation,
        config: SimulationConfig,
    ) -> None:
        self.graph = graph
        self.traces = traces
        self.selected_algorithm = normalize_algorithm_name(selected_algorithm)
        self.start_node = start_node
        self.goal_node = goal_node
        self.start_location = start_location
        self.goal_location = goal_location
        self.config = config

        self.trace = self.traces[self.selected_algorithm]
        self.event_index = 0
        self.is_running = False
        self.visited_nodes: list[int] = []
        self.frontier_nodes: set[int] = set(self.trace.initial_frontier)
        self.tree_segments: list[list[tuple[float, float]]] = []

        self.fig = plt.figure(figsize=(16, 10), facecolor=BACKGROUND_COLOR)
        self.ax_map = self.fig.add_axes([0.05, 0.16, 0.68, 0.79])
        self.ax_info = self.fig.add_axes([0.76, 0.36, 0.21, 0.58])
        self.ax_info.axis("off")
        self.ax_info.set_facecolor("#fbfaf6")

        self.ax_start = self.fig.add_axes([0.05, 0.05, 0.10, 0.05])
        self.ax_pause = self.fig.add_axes([0.16, 0.05, 0.10, 0.05])
        self.ax_next = self.fig.add_axes([0.27, 0.05, 0.10, 0.05])
        self.ax_reset = self.fig.add_axes([0.38, 0.05, 0.10, 0.05])
        self.ax_speed = self.fig.add_axes([0.50, 0.055, 0.21, 0.03])
        self.ax_radio = self.fig.add_axes([0.76, 0.13, 0.21, 0.17])

        self.start_button = Button(self.ax_start, "Start / Resume", color="#e9f5ef")
        self.pause_button = Button(self.ax_pause, "Stop / Pause", color="#fdf0ef")
        self.next_button = Button(self.ax_next, "Next", color="#f7efe2")
        self.reset_button = Button(self.ax_reset, "Reset / Init", color="#eef2f5")
        self.speed_slider = Slider(
            self.ax_speed,
            "Speed (steps/s)",
            valmin=0.5,
            valmax=30.0,
            valinit=self.config.animation_speed,
            valstep=0.5,
        )
        self.algorithm_selector = RadioButtons(
            self.ax_radio, list(self.traces.keys()), active=self._algorithm_index()
        )

        self.timer = self._build_timer(self._interval_ms(self.config.animation_speed))

        self.base_edges = LineCollection(
            base_graph_segments(self.graph),
            colors=GRAPH_COLOR,
            linewidths=0.8,
            alpha=0.8,
            zorder=1,
        )
        self.explored_edges = LineCollection(
            [],
            colors=VISITED_COLOR,
            linewidths=1.6,
            alpha=0.35,
            zorder=2,
        )
        self.path_edges = LineCollection(
            [],
            colors=PATH_COLOR,
            linewidths=3.2,
            alpha=0.95,
            zorder=5,
        )
        self.ax_map.add_collection(self.base_edges)
        self.ax_map.add_collection(self.explored_edges)
        self.ax_map.add_collection(self.path_edges)

        self.visited_scatter = self.ax_map.scatter(
            [],
            [],
            s=18,
            c=VISITED_COLOR,
            alpha=0.9,
            edgecolors="none",
            zorder=3,
            label="Visited",
        )
        self.frontier_scatter = self.ax_map.scatter(
            [],
            [],
            s=28,
            c=FRONTIER_COLOR,
            alpha=0.95,
            edgecolors="white",
            linewidths=0.2,
            zorder=4,
            label="Frontier",
        )
        self.current_scatter = self.ax_map.scatter(
            [],
            [],
            s=80,
            c=CURRENT_COLOR,
            alpha=1.0,
            edgecolors="white",
            linewidths=0.7,
            zorder=6,
            label="Current node",
        )

        start_x, start_y = node_xy(self.graph, self.start_node)
        goal_x, goal_y = node_xy(self.graph, self.goal_node)
        self.ax_map.scatter(
            [start_x],
            [start_y],
            s=120,
            c=START_COLOR,
            marker="o",
            edgecolors="white",
            linewidths=1.0,
            zorder=7,
            label="Start",
        )
        self.ax_map.scatter(
            [goal_x],
            [goal_y],
            s=140,
            c=GOAL_COLOR,
            marker="*",
            edgecolors="white",
            linewidths=0.8,
            zorder=7,
            label="Goal",
        )

        self.state_text = self.ax_info.text(
            0.0,
            1.0,
            "",
            va="top",
            ha="left",
            fontsize=10.5,
            family="monospace",
            color="#1f2933",
        )
        self.summary_text = self.ax_info.text(
            0.0,
            0.40,
            "",
            va="top",
            ha="left",
            fontsize=9.5,
            family="monospace",
            color="#3e4c59",
        )

        self._configure_axes()
        self._bind_callbacks()
        self._redraw_state()

    def _algorithm_index(self) -> int:
        labels = list(self.traces.keys())
        return labels.index(self.selected_algorithm)

    @staticmethod
    def _interval_ms(speed: float) -> int:
        return max(20, int(round(1000.0 / max(speed, 0.1))))

    def _build_timer(self, interval_ms: int):
        timer = self.fig.canvas.new_timer(interval=interval_ms)
        timer.add_callback(self._advance_from_timer)
        return timer

    def _configure_axes(self) -> None:
        self.ax_map.set_facecolor(BACKGROUND_COLOR)
        self.ax_map.set_xticks([])
        self.ax_map.set_yticks([])
        self.ax_map.set_xlabel("")
        self.ax_map.set_ylabel("")

        all_x = [float(data["x"]) for _node, data in self.graph.nodes(data=True)]
        all_y = [float(data["y"]) for _node, data in self.graph.nodes(data=True)]
        x_margin = max((max(all_x) - min(all_x)) * 0.03, 0.001)
        y_margin = max((max(all_y) - min(all_y)) * 0.03, 0.001)
        mean_lat = sum(all_y) / len(all_y)
        self.ax_map.set_xlim(min(all_x) - x_margin, max(all_x) + x_margin)
        self.ax_map.set_ylim(min(all_y) - y_margin, max(all_y) + y_margin)
        self.ax_map.set_aspect(1.0 / max(cos(radians(mean_lat)), 0.01))
        self.ax_map.legend(loc="lower left", frameon=True, fontsize=9)

    def _bind_callbacks(self) -> None:
        self.start_button.on_clicked(self.on_start)
        self.pause_button.on_clicked(self.on_pause)
        self.next_button.on_clicked(self.on_next)
        self.reset_button.on_clicked(self.on_reset)
        self.speed_slider.on_changed(self.on_speed_change)
        self.algorithm_selector.on_clicked(self.on_algorithm_change)

    def _advance_from_timer(self) -> None:
        if not self.is_running:
            return
        if not self.step_forward():
            self.pause()

    def on_start(self, _event) -> None:
        self.is_running = True
        self.timer.start()

    def pause(self) -> None:
        self.is_running = False
        self.timer.stop()

    def on_pause(self, _event) -> None:
        self.pause()

    def on_next(self, _event) -> None:
        self.pause()
        self.step_forward()

    def on_reset(self, _event) -> None:
        self.pause()
        self.event_index = 0
        self.visited_nodes = []
        self.frontier_nodes = set(self.trace.initial_frontier)
        self.tree_segments = []
        self._redraw_state()

    def on_speed_change(self, speed: float) -> None:
        was_running = self.is_running
        self.timer.stop()
        self.timer = self._build_timer(self._interval_ms(speed))
        if was_running:
            self.timer.start()

    def on_algorithm_change(self, label: str) -> None:
        self.pause()
        self.selected_algorithm = normalize_algorithm_name(label)
        self.trace = self.traces[self.selected_algorithm]
        self.event_index = 0
        self.visited_nodes = []
        self.frontier_nodes = set(self.trace.initial_frontier)
        self.tree_segments = []
        self._redraw_state()

    def step_forward(self) -> bool:
        if self.event_index >= len(self.trace.events):
            return False

        event = self.trace.events[self.event_index]
        self.event_index += 1
        self._apply_event(event)
        return self.event_index < len(self.trace.events)

    def _apply_event(self, event: SearchEvent) -> None:
        self.visited_nodes.append(event.current)
        for frontier_node in event.frontier_removed:
            self.frontier_nodes.discard(frontier_node)
        for frontier_node in event.frontier_added:
            self.frontier_nodes.add(frontier_node)
        if event.parent_edge is not None:
            self.tree_segments.append(edge_segment(self.graph, *event.parent_edge))
        self._redraw_state(event)

    def _current_event(self) -> SearchEvent | None:
        if self.event_index == 0:
            return None
        return self.trace.events[self.event_index - 1]

    def _redraw_state(self, current_event: SearchEvent | None = None) -> None:
        current_event = current_event or self._current_event()

        self.visited_scatter.set_offsets(nodes_to_xy(self.graph, self.visited_nodes))
        self.frontier_scatter.set_offsets(
            nodes_to_xy(self.graph, list(self.frontier_nodes))
        )

        if current_event is None:
            self.current_scatter.set_offsets(nodes_to_xy(self.graph, []))
        else:
            self.current_scatter.set_offsets(nodes_to_xy(self.graph, [current_event.current]))

        self.explored_edges.set_segments(self.tree_segments)
        if self.event_index >= len(self.trace.events) and self.trace.path:
            self.path_edges.set_segments(path_segments(self.graph, self.trace.path))
        else:
            self.path_edges.set_segments([])

        self.ax_map.set_title(self._title_text(current_event), fontsize=14, loc="left")
        self.state_text.set_text(self._state_panel_text(current_event))
        self.summary_text.set_text(self._summary_panel_text())
        self.fig.canvas.draw_idle()

    def _title_text(self, current_event: SearchEvent | None) -> str:
        if current_event is None:
            return (
                f"{self.selected_algorithm} Search | Ready | "
                f"Start {self.start_location.label} -> Goal {self.goal_location.label}"
            )

        return (
            f"{self.selected_algorithm} Search | Step {current_event.step}/{len(self.trace.events)} | "
            f"Visited {current_event.visited_count} nodes | "
            f"Search time {format_seconds(current_event.elapsed_seconds)}"
        )

    def _state_panel_text(self, current_event: SearchEvent | None) -> str:
        if current_event is None:
            status = "ready"
            step = 0
            elapsed = 0.0
            visited_count = 0
            frontier_size = len(self.frontier_nodes)
        else:
            status = current_event.status
            if current_event.status == "searching" and self.event_index >= len(self.trace.events):
                status = "found" if self.trace.metrics.found else "failed"
            step = current_event.step
            elapsed = current_event.elapsed_seconds
            visited_count = current_event.visited_count
            frontier_size = current_event.frontier_size

        lines = [
            "Current State",
            f"Algorithm     : {self.selected_algorithm}",
            f"Status        : {status}",
            f"Step          : {step} / {len(self.trace.events)}",
            f"Elapsed       : {format_seconds(elapsed)}",
            f"Visited       : {visited_count}",
            f"Frontier      : {frontier_size}",
            f"Path length   : {format_distance(self.trace.metrics.path_length_m) if status == 'found' else '--'}",
            "",
            "Locations",
            f"Place         : {self.config.place_name or 'point-based download'}",
            f"Network       : {self.config.network_type}",
            f"Start         : {self.start_location.label}",
            f"Goal          : {self.goal_location.label}",
            "",
            "Notes",
            "BFS/DFS ignore weights.",
            "Dijkstra and A* use edge length.",
            "Greedy uses the heuristic only.",
        ]
        return "\n".join(lines)

    def _summary_panel_text(self) -> str:
        lines = ["Comparison", ""]
        for algorithm_name, trace in self.traces.items():
            prefix = ">" if algorithm_name == self.selected_algorithm else " "
            found_label = "found" if trace.metrics.found else "failed"
            lines.append(
                (
                    f"{prefix} {algorithm_name:<24}"
                    f"{found_label:<7} "
                    f"visited {trace.metrics.visited_count:>5} | "
                    f"steps {trace.metrics.steps:>5} | "
                    f"time {format_seconds(trace.metrics.runtime_seconds):>8} | "
                    f"path {format_distance(trace.metrics.path_length_m):>8}"
                )
            )
        return "\n".join(lines)

    def show(self) -> None:
        plt.show()


def print_metrics_summary(traces: dict[str, SearchTrace]) -> None:
    """Emit a compact console summary for headless runs."""

    header = (
        f"{'Algorithm':<24} {'Status':<8} {'Visited':>8} {'Steps':>8} "
        f"{'Runtime':>12} {'Path':>12}"
    )
    print(header)
    print("-" * len(header))
    for trace in traces.values():
        status = "found" if trace.metrics.found else "failed"
        print(
            f"{trace.metrics.algorithm:<24} "
            f"{status:<8} "
            f"{trace.metrics.visited_count:>8} "
            f"{trace.metrics.steps:>8} "
            f"{format_seconds(trace.metrics.runtime_seconds):>12} "
            f"{format_distance(trace.metrics.path_length_m):>12}"
        )


def main() -> None:
    """Entrypoint for the simulator."""

    args = parse_args()
    config = build_config(args)

    start_location = resolve_location(
        label="start",
        place_name=config.place_name,
        query=config.start_query,
        lat=config.start_lat,
        lon=config.start_lon,
    )
    goal_location = resolve_location(
        label="goal",
        place_name=config.place_name,
        query=config.goal_query,
        lat=config.goal_lat,
        lon=config.goal_lon,
    )

    graph = load_graph(config, start_location, goal_location)
    start_node = nearest_graph_node(graph, start_location)
    goal_node = nearest_graph_node(graph, goal_location)
    traces = compute_search_traces(graph, start_node, goal_node)

    if config.metrics_csv_path is not None:
        export_metrics_csv(config.metrics_csv_path, traces)

    if config.no_gui:
        print_metrics_summary(traces)
        return

    simulator = SearchSimulator(
        graph=graph,
        traces=traces,
        selected_algorithm=config.selected_algorithm,
        start_node=start_node,
        goal_node=goal_node,
        start_location=start_location,
        goal_location=goal_location,
        config=config,
    )
    simulator.show()


if __name__ == "__main__":
    main()
