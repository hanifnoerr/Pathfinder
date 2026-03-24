"""OpenStreetMap loading, geocoding, nearest-node lookup, and plot helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import networkx as nx
import numpy as np
import osmnx as ox

from algorithms import haversine_distance_m
from config import SimulationConfig

NodeId = int


@dataclass(slots=True)
class ResolvedLocation:
    """A user-provided endpoint resolved into coordinates."""

    label: str
    lat: float
    lon: float
    source: str
    geocode_query_used: str | None = None


def configure_osmnx(cache_dir: Path) -> None:
    """Keep OSMnx caches local to the project."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    http_cache_dir = cache_dir / "osmnx_http"
    http_cache_dir.mkdir(parents=True, exist_ok=True)

    ox.settings.use_cache = True
    ox.settings.log_console = False
    ox.settings.cache_folder = str(http_cache_dir)


def slugify(value: str) -> str:
    """Create a filesystem-safe cache key."""

    collapsed = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return collapsed or "graph"


def geocode_candidates(query: str, place_name: str | None) -> list[str]:
    """Try the raw query first, then a place-qualified version when useful."""

    candidates = [query]
    if place_name and place_name.lower() not in query.lower():
        candidates.append(f"{query}, {place_name}")
    return candidates


def resolve_location(
    label: str,
    place_name: str | None,
    query: str | None,
    lat: float | None,
    lon: float | None,
) -> ResolvedLocation:
    """Resolve text input or direct coordinates into a single location object."""

    if query:
        last_error: Exception | None = None
        for candidate in geocode_candidates(query, place_name):
            try:
                resolved_lat, resolved_lon = ox.geocode(candidate)
                return ResolvedLocation(
                    label=query,
                    lat=float(resolved_lat),
                    lon=float(resolved_lon),
                    source="query",
                    geocode_query_used=candidate,
                )
            except Exception as error:  # pragma: no cover - depends on remote geocoder
                last_error = error

        if lat is not None and lon is not None:
            return ResolvedLocation(
                label=f"{label} coordinates",
                lat=float(lat),
                lon=float(lon),
                source="coordinates-fallback",
            )

        raise ValueError(
            f"Could not geocode {label} query '{query}'. "
            "Try a more specific name or provide explicit latitude/longitude."
        ) from last_error

    if lat is not None and lon is not None:
        return ResolvedLocation(
            label=f"{label} coordinates",
            lat=float(lat),
            lon=float(lon),
            source="coordinates",
        )

    raise ValueError(
        f"{label} must be provided either as a text query or as latitude/longitude."
    )


def compute_download_radius_m(
    start: ResolvedLocation, goal: ResolvedLocation, base_radius_m: float, buffer_m: float
) -> float:
    """Expand point-based graph downloads so both endpoints fit comfortably."""

    straight_line_distance = haversine_distance_m(
        start.lat,
        start.lon,
        goal.lat,
        goal.lon,
    )
    return max(float(base_radius_m), straight_line_distance / 2.0 + float(buffer_m))


def graph_cache_path(
    config: SimulationConfig,
    start: ResolvedLocation,
    goal: ResolvedLocation,
) -> Path:
    """Build a local graph cache filename from the active configuration."""

    graph_cache_dir = config.cache_dir / "graphs"
    graph_cache_dir.mkdir(parents=True, exist_ok=True)

    if config.graph_radius_m is None:
        file_name = f"{slugify(config.place_name)}_{config.network_type}.graphml"
    else:
        center_lat = (start.lat + goal.lat) / 2.0
        center_lon = (start.lon + goal.lon) / 2.0
        radius = int(
            round(
                compute_download_radius_m(
                    start, goal, config.graph_radius_m, config.graph_buffer_m
                )
            )
        )
        file_name = (
            f"point_{center_lat:.4f}_{center_lon:.4f}_{radius}_{config.network_type}.graphml"
        )

    return graph_cache_dir / file_name


def load_graph(
    config: SimulationConfig,
    start: ResolvedLocation,
    goal: ResolvedLocation,
) -> nx.MultiDiGraph:
    """Load a cached graph or download it from OpenStreetMap via OSMnx."""

    configure_osmnx(config.cache_dir)
    cache_path = graph_cache_path(config, start, goal)

    if cache_path.exists():
        return ox.load_graphml(filepath=str(cache_path))

    if config.graph_radius_m is None:
        graph = ox.graph_from_place(
            query=config.place_name,
            network_type=config.network_type,
            simplify=True,
            retain_all=False,
        )
    else:
        radius_m = compute_download_radius_m(
            start, goal, config.graph_radius_m, config.graph_buffer_m
        )
        graph = ox.graph_from_point(
            center_point=((start.lat + goal.lat) / 2.0, (start.lon + goal.lon) / 2.0),
            dist=radius_m,
            network_type=config.network_type,
            simplify=True,
            retain_all=False,
        )

    ox.save_graphml(graph, filepath=str(cache_path))
    return graph


def nearest_graph_node(graph: nx.MultiDiGraph, location: ResolvedLocation) -> NodeId:
    """Snap a latitude/longitude point to the nearest network node.

    This avoids OSMnx's optional scikit-learn dependency by doing a small
    vectorised great-circle search over the loaded graph nodes.
    """

    node_ids = np.fromiter(graph.nodes, dtype=np.int64)
    node_lats = np.array([float(graph.nodes[node]["y"]) for node in node_ids], dtype=float)
    node_lons = np.array([float(graph.nodes[node]["x"]) for node in node_ids], dtype=float)

    radius_m = 6_371_000.0
    lat1 = np.radians(location.lat)
    lon1 = np.radians(location.lon)
    lat2 = np.radians(node_lats)
    lon2 = np.radians(node_lons)

    d_lat = lat2 - lat1
    d_lon = lon2 - lon1

    a = (
        np.sin(d_lat / 2.0) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(d_lon / 2.0) ** 2
    )
    distances = 2.0 * radius_m * np.arcsin(np.sqrt(a))
    nearest_index = int(np.argmin(distances))
    return int(node_ids[nearest_index])


def node_xy(graph: nx.MultiDiGraph, node: NodeId) -> tuple[float, float]:
    """Return a node position in plotting coordinates."""

    return float(graph.nodes[node]["x"]), float(graph.nodes[node]["y"])


def nodes_to_xy(graph: nx.MultiDiGraph, nodes: list[NodeId] | tuple[NodeId, ...]) -> np.ndarray:
    """Convert a sequence of node ids into a scatter offset array."""

    if not nodes:
        return np.empty((0, 2))

    return np.array([node_xy(graph, node) for node in nodes], dtype=float)


def edge_segment(graph: nx.MultiDiGraph, source: NodeId, target: NodeId) -> list[tuple[float, float]]:
    """Return one straight map segment between two graph nodes."""

    return [node_xy(graph, source), node_xy(graph, target)]


def base_graph_segments(graph: nx.MultiDiGraph) -> list[list[tuple[float, float]]]:
    """Build light neutral segments for the full background street map."""

    undirected_graph = nx.Graph(graph)
    return [edge_segment(graph, source, target) for source, target in undirected_graph.edges()]


def path_segments(
    graph: nx.MultiDiGraph, path: list[NodeId]
) -> list[list[tuple[float, float]]]:
    """Build highlighted line segments for the final route."""

    if len(path) < 2:
        return []
    return [
        edge_segment(graph, source, target)
        for source, target in zip(path[:-1], path[1:])
    ]
