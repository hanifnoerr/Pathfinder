"""Configuration defaults and helpers for the graph search simulator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_NETWORK_TYPES = ("walk", "drive", "bike")
SUPPORTED_ALGORITHMS = (
    "BFS",
    "DFS",
    "Dijkstra",
    "A*",
    "Greedy Best-First Search",
)

ALGORITHM_ALIASES = {
    "bfs": "BFS",
    "dfs": "DFS",
    "dijkstra": "Dijkstra",
    "a*": "A*",
    "astar": "A*",
    "a-star": "A*",
    "greedy": "Greedy Best-First Search",
    "greedy best-first": "Greedy Best-First Search",
    "greedy best-first search": "Greedy Best-First Search",
}


def normalize_algorithm_name(value: str) -> str:
    """Return the canonical label used throughout the app."""

    key = value.strip().lower()
    if value in SUPPORTED_ALGORITHMS:
        return value
    if key in ALGORITHM_ALIASES:
        return ALGORITHM_ALIASES[key]

    supported = ", ".join(SUPPORTED_ALGORITHMS)
    raise ValueError(f"Unsupported algorithm '{value}'. Choose from: {supported}.")


@dataclass(slots=True)
class SimulationConfig:
    """User-editable simulation settings."""

    place_name: str = "Melbourne, Victoria, Australia"
    network_type: str = "walk"
    start_query: str | None = "Monash University Clayton campus"
    goal_query: str | None = "State Library Victoria, Melbourne"
    start_lat: float | None = None
    start_lon: float | None = None
    goal_lat: float | None = None
    goal_lon: float | None = None
    animation_speed: float = 8.0
    batch_steps: int = 1
    compare_mode: bool = False
    selected_algorithm: str = "A*"
    graph_radius_m: float | None = 12000.0
    graph_buffer_m: float = 1500.0
    trail_length: int = 180
    max_history_nodes: int = 12000
    max_frontier_nodes: int = 3000
    cache_dir: Path = Path("cache")
    metrics_csv_path: Path | None = None
    no_gui: bool = False

    def validate(self) -> None:
        if self.network_type not in SUPPORTED_NETWORK_TYPES:
            supported = ", ".join(SUPPORTED_NETWORK_TYPES)
            raise ValueError(
                f"Unsupported network_type '{self.network_type}'. Choose from: {supported}."
            )

        if self.animation_speed <= 0:
            raise ValueError("animation_speed must be greater than 0.")

        if self.batch_steps <= 0:
            raise ValueError("batch_steps must be greater than 0.")

        if self.trail_length <= 0:
            raise ValueError("trail_length must be greater than 0.")

        if self.max_history_nodes <= 0 or self.max_frontier_nodes <= 0:
            raise ValueError("render caps must be greater than 0.")

        normalize_algorithm_name(self.selected_algorithm)

        self._validate_coordinate_pair("start", self.start_lat, self.start_lon)
        self._validate_coordinate_pair("goal", self.goal_lat, self.goal_lon)

        if not self.place_name and self.graph_radius_m is None:
            raise ValueError(
                "place_name is required unless graph_radius_m is provided to download "
                "a point-centered graph around the start/goal area."
            )

    @staticmethod
    def _validate_coordinate_pair(name: str, lat: float | None, lon: float | None) -> None:
        if (lat is None) ^ (lon is None):
            raise ValueError(
                f"{name}_lat and {name}_lon must be provided together when using coordinates."
            )


DEFAULT_CONFIG = SimulationConfig()
