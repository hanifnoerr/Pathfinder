"""Entry point for the PySide6 + PyQtGraph search simulation desktop app."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
from pathlib import Path

from algorithms import SearchTrace, compute_search_traces
from config import DEFAULT_CONFIG, SimulationConfig, normalize_algorithm_name
from map_utils import load_graph, nearest_graph_node, resolve_location
from visualization import (
    SimulationBundle,
    format_distance,
    format_optimal,
    format_seconds,
)


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
    parser.add_argument("--batch-steps", dest="batch_steps", type=int)
    parser.add_argument("--algorithm", dest="selected_algorithm")
    parser.add_argument("--graph-radius-m", dest="graph_radius_m", type=float)
    parser.add_argument("--graph-buffer-m", dest="graph_buffer_m", type=float)
    parser.add_argument("--trail-length", dest="trail_length", type=int)
    parser.add_argument("--max-history-nodes", dest="max_history_nodes", type=int)
    parser.add_argument("--max-frontier-nodes", dest="max_frontier_nodes", type=int)
    parser.add_argument("--cache-dir", dest="cache_dir", type=Path)
    parser.add_argument("--metrics-csv", dest="metrics_csv_path", type=Path)
    parser.add_argument("--compare-mode", dest="compare_mode", action="store_true")
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
                "path_cost_m",
                "path_length_m",
                "optimal_under_weighting",
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
                    "path_cost_m": "" if metrics.path_cost_m is None else f"{metrics.path_cost_m:.3f}",
                    "path_length_m": "" if metrics.path_length_m is None else f"{metrics.path_length_m:.3f}",
                    "optimal_under_weighting": metrics.optimal_under_weighting,
                    "explored_fraction": f"{metrics.explored_fraction:.6f}",
                }
            )


def load_simulation_bundle(config: SimulationConfig) -> SimulationBundle:
    """Resolve the chosen route, load the graph, and precompute all traces."""

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

    return SimulationBundle(
        config=config,
        graph=graph,
        traces=traces,
        start_node=start_node,
        goal_node=goal_node,
        start_location=start_location,
        goal_location=goal_location,
    )


def print_metrics_summary(traces: dict[str, SearchTrace]) -> None:
    """Emit a compact console summary for headless runs."""

    header = (
        f"{'Algorithm':<24} {'Status':<8} {'Visited':>8} "
        f"{'Runtime':>12} {'Cost':>12} {'Optimal':>9}"
    )
    print(header)
    print("-" * len(header))
    for trace in traces.values():
        status = "found" if trace.metrics.found else "failed"
        print(
            f"{trace.metrics.algorithm:<24} "
            f"{status:<8} "
            f"{trace.metrics.visited_count:>8} "
            f"{format_seconds(trace.metrics.runtime_seconds):>12} "
            f"{format_distance(trace.metrics.path_cost_m):>12} "
            f"{format_optimal(trace.metrics.optimal_under_weighting):>9}"
        )


def main() -> None:
    """Entrypoint for the desktop app."""

    config = build_config(parse_args())
    bundle = load_simulation_bundle(config)

    if config.no_gui:
        print_metrics_summary(bundle.traces)
        return

    from ui import launch_app

    raise SystemExit(launch_app(bundle, load_simulation_bundle))


if __name__ == "__main__":
    main()
