"""Search algorithms that record step-by-step replay traces."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from heapq import heappop, heappush
from math import asin, cos, inf, radians, sin, sqrt
from time import perf_counter
from typing import Callable

import networkx as nx

NodeId = int
ParentMap = dict[NodeId, NodeId | None]


@dataclass(slots=True)
class SearchEvent:
    """A single replayable search step."""

    step: int
    current: NodeId
    frontier_size: int
    visited_count: int
    elapsed_seconds: float
    status: str
    parent_edge: tuple[NodeId, NodeId] | None = None
    frontier_added: tuple[NodeId, ...] = ()
    frontier_removed: tuple[NodeId, ...] = ()


@dataclass(slots=True)
class SearchMetrics:
    """Summary metrics collected during the search."""

    algorithm: str
    found: bool
    steps: int
    visited_count: int
    frontier_peak: int
    runtime_seconds: float
    path_length_m: float | None
    explored_fraction: float


@dataclass(slots=True)
class SearchTrace:
    """All data needed to replay and compare one algorithm run."""

    algorithm: str
    events: list[SearchEvent]
    visit_order: list[NodeId]
    initial_frontier: tuple[NodeId, ...]
    frontier_history: list[int]
    parent_map: ParentMap
    path: list[NodeId]
    metrics: SearchMetrics


def haversine_distance_m(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Return the great-circle distance between two latitude/longitude points."""

    radius_m = 6_371_000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)

    a = sin(d_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(d_lon / 2) ** 2
    return 2 * radius_m * asin(sqrt(a))


def heuristic_distance_m(graph: nx.MultiDiGraph, node: NodeId, goal: NodeId) -> float:
    """Straight-line geographic distance to the goal for A* and Greedy."""

    node_data = graph.nodes[node]
    goal_data = graph.nodes[goal]
    return haversine_distance_m(
        float(node_data["y"]),
        float(node_data["x"]),
        float(goal_data["y"]),
        float(goal_data["x"]),
    )


def edge_length_m(graph: nx.MultiDiGraph, source: NodeId, target: NodeId) -> float:
    """Use the shortest parallel edge length between two connected nodes."""

    edge_bundle = graph.get_edge_data(source, target, default={})
    if not edge_bundle:
        source_data = graph.nodes[source]
        target_data = graph.nodes[target]
        return haversine_distance_m(
            float(source_data["y"]),
            float(source_data["x"]),
            float(target_data["y"]),
            float(target_data["x"]),
        )

    lengths = [
        float(attributes.get("length", 0.0))
        for attributes in edge_bundle.values()
        if attributes is not None
    ]
    if lengths:
        return min(lengths)

    source_data = graph.nodes[source]
    target_data = graph.nodes[target]
    return haversine_distance_m(
        float(source_data["y"]),
        float(source_data["x"]),
        float(target_data["y"]),
        float(target_data["x"]),
    )


def sorted_neighbors(graph: nx.MultiDiGraph, node: NodeId) -> list[tuple[NodeId, float]]:
    """Return outgoing neighbors in a stable order for reproducible traces."""

    neighbors: list[tuple[NodeId, float]] = []
    for neighbor in graph.successors(node):
        neighbors.append((int(neighbor), edge_length_m(graph, node, int(neighbor))))

    neighbors.sort(key=lambda item: (item[1], item[0]))
    return neighbors


def reconstruct_path(parent_map: ParentMap, goal: NodeId) -> list[NodeId]:
    """Rebuild the final route from the recorded parent pointers."""

    if goal not in parent_map:
        return []

    path: list[NodeId] = []
    current: NodeId | None = goal
    while current is not None:
        path.append(current)
        current = parent_map[current]
    path.reverse()
    return path


def path_length_m(graph: nx.MultiDiGraph, path: list[NodeId]) -> float | None:
    """Compute the weighted route distance for any returned path."""

    if len(path) < 2:
        return 0.0 if path else None

    total = 0.0
    for source, target in zip(path[:-1], path[1:]):
        total += edge_length_m(graph, source, target)
    return total


def finalise_trace(
    graph: nx.MultiDiGraph,
    algorithm: str,
    started_at: float,
    events: list[SearchEvent],
    visit_order: list[NodeId],
    initial_frontier: tuple[NodeId, ...],
    parent_map: ParentMap,
    goal: NodeId,
    frontier_peak: int,
) -> SearchTrace:
    """Build the immutable replay/metrics structure returned to the UI."""

    runtime_seconds = perf_counter() - started_at
    found = bool(visit_order) and visit_order[-1] == goal
    if not found and events:
        events[-1].status = "failed"

    path = reconstruct_path(parent_map, goal) if found else []
    route_length = path_length_m(graph, path)
    node_count = max(graph.number_of_nodes(), 1)

    metrics = SearchMetrics(
        algorithm=algorithm,
        found=found,
        steps=len(events),
        visited_count=len(visit_order),
        frontier_peak=frontier_peak,
        runtime_seconds=runtime_seconds,
        path_length_m=route_length,
        explored_fraction=len(visit_order) / node_count,
    )

    return SearchTrace(
        algorithm=algorithm,
        events=events,
        visit_order=visit_order,
        initial_frontier=initial_frontier,
        frontier_history=[event.frontier_size for event in events],
        parent_map=parent_map,
        path=path,
        metrics=metrics,
    )


def breadth_first_search(
    graph: nx.MultiDiGraph, start: NodeId, goal: NodeId
) -> SearchTrace:
    """Run BFS on the street graph while ignoring edge weights."""

    started_at = perf_counter()
    queue: deque[NodeId] = deque([start])
    discovered: set[NodeId] = {start}
    parent_map: ParentMap = {start: None}
    visit_order: list[NodeId] = []
    events: list[SearchEvent] = []
    frontier_peak = 1

    while queue:
        current = queue.popleft()
        visit_order.append(current)
        status = "found" if current == goal else "searching"

        if status != "found":
            # BFS ignores edge lengths on purpose so it behaves as an unweighted search.
            frontier_added: list[NodeId] = []
            for neighbor, _length in sorted_neighbors(graph, current):
                if neighbor in discovered:
                    continue
                discovered.add(neighbor)
                parent_map[neighbor] = current
                queue.append(neighbor)
                frontier_added.append(neighbor)
        else:
            frontier_added = []

        frontier_peak = max(frontier_peak, len(queue))
        parent_node = parent_map.get(current)
        events.append(
            SearchEvent(
                step=len(events) + 1,
                current=current,
                frontier_size=len(queue),
                visited_count=len(visit_order),
                elapsed_seconds=perf_counter() - started_at,
                status=status,
                parent_edge=(parent_node, current) if parent_node is not None else None,
                frontier_added=tuple(frontier_added),
                frontier_removed=(current,),
            )
        )

        if status == "found":
            break

    return finalise_trace(
        graph,
        "BFS",
        started_at,
        events,
        visit_order,
        (start,),
        parent_map,
        goal,
        frontier_peak,
    )


def depth_first_search(
    graph: nx.MultiDiGraph, start: NodeId, goal: NodeId
) -> SearchTrace:
    """Run DFS on the street graph while ignoring edge weights."""

    started_at = perf_counter()
    stack: list[NodeId] = [start]
    discovered: set[NodeId] = {start}
    parent_map: ParentMap = {start: None}
    visit_order: list[NodeId] = []
    events: list[SearchEvent] = []
    frontier_peak = 1

    while stack:
        current = stack.pop()
        visit_order.append(current)
        status = "found" if current == goal else "searching"

        if status != "found":
            # DFS also ignores weights so the comparison stays faithful to classic DFS.
            neighbors = sorted_neighbors(graph, current)
            frontier_added: list[NodeId] = []
            for neighbor, _length in reversed(neighbors):
                if neighbor in discovered:
                    continue
                discovered.add(neighbor)
                parent_map[neighbor] = current
                stack.append(neighbor)
                frontier_added.append(neighbor)
        else:
            frontier_added = []

        frontier_peak = max(frontier_peak, len(stack))
        parent_node = parent_map.get(current)
        events.append(
            SearchEvent(
                step=len(events) + 1,
                current=current,
                frontier_size=len(stack),
                visited_count=len(visit_order),
                elapsed_seconds=perf_counter() - started_at,
                status=status,
                parent_edge=(parent_node, current) if parent_node is not None else None,
                frontier_added=tuple(frontier_added),
                frontier_removed=(current,),
            )
        )

        if status == "found":
            break

    return finalise_trace(
        graph,
        "DFS",
        started_at,
        events,
        visit_order,
        (start,),
        parent_map,
        goal,
        frontier_peak,
    )


def best_first_search(
    graph: nx.MultiDiGraph,
    start: NodeId,
    goal: NodeId,
    algorithm: str,
    priority_function: Callable[[float, NodeId], float],
    allow_relaxation: bool,
) -> SearchTrace:
    """Shared engine for Dijkstra, A*, and Greedy Best-First Search."""

    started_at = perf_counter()
    g_costs: dict[NodeId, float] = {start: 0.0}
    parent_map: ParentMap = {start: None}
    frontier_priorities: dict[NodeId, float] = {start: priority_function(0.0, start)}
    heap: list[tuple[float, float, NodeId]] = [(frontier_priorities[start], 0.0, start)]
    closed: set[NodeId] = set()
    visit_order: list[NodeId] = []
    events: list[SearchEvent] = []
    frontier_peak = 1

    while heap:
        priority, queued_cost, current = heappop(heap)
        current_priority = frontier_priorities.get(current)
        current_cost = g_costs.get(current, inf)

        if current_priority is None:
            continue
        if priority > current_priority + 1e-9:
            continue
        if queued_cost > current_cost + 1e-9:
            continue

        frontier_priorities.pop(current, None)
        if current in closed:
            continue

        closed.add(current)
        visit_order.append(current)
        status = "found" if current == goal else "searching"

        if status != "found":
            frontier_added: list[NodeId] = []
            for neighbor, length in sorted_neighbors(graph, current):
                if neighbor in closed:
                    continue

                tentative_cost = current_cost + length
                should_add = False

                if allow_relaxation:
                    if tentative_cost + 1e-9 < g_costs.get(neighbor, inf):
                        should_add = True
                else:
                    if neighbor not in g_costs:
                        should_add = True

                if not should_add:
                    continue

                was_in_frontier = neighbor in frontier_priorities
                g_costs[neighbor] = tentative_cost
                parent_map[neighbor] = current
                neighbor_priority = priority_function(tentative_cost, neighbor)
                frontier_priorities[neighbor] = neighbor_priority
                heappush(heap, (neighbor_priority, tentative_cost, neighbor))
                if not was_in_frontier:
                    frontier_added.append(neighbor)
        else:
            frontier_added = []

        frontier_peak = max(frontier_peak, len(frontier_priorities))
        parent_node = parent_map.get(current)
        events.append(
            SearchEvent(
                step=len(events) + 1,
                current=current,
                frontier_size=len(frontier_priorities),
                visited_count=len(visit_order),
                elapsed_seconds=perf_counter() - started_at,
                status=status,
                parent_edge=(parent_node, current) if parent_node is not None else None,
                frontier_added=tuple(frontier_added),
                frontier_removed=(current,),
            )
        )

        if status == "found":
            break

    return finalise_trace(
        graph,
        algorithm,
        started_at,
        events,
        visit_order,
        (start,),
        parent_map,
        goal,
        frontier_peak,
    )


def dijkstra_search(graph: nx.MultiDiGraph, start: NodeId, goal: NodeId) -> SearchTrace:
    """Run Dijkstra using edge length as the route cost."""

    return best_first_search(
        graph=graph,
        start=start,
        goal=goal,
        algorithm="Dijkstra",
        priority_function=lambda cost, _node: cost,
        allow_relaxation=True,
    )


def a_star_search(graph: nx.MultiDiGraph, start: NodeId, goal: NodeId) -> SearchTrace:
    """Run A* using edge length plus straight-line distance to the goal."""

    return best_first_search(
        graph=graph,
        start=start,
        goal=goal,
        algorithm="A*",
        priority_function=lambda cost, node: cost + heuristic_distance_m(graph, node, goal),
        allow_relaxation=True,
    )


def greedy_best_first_search(
    graph: nx.MultiDiGraph, start: NodeId, goal: NodeId
) -> SearchTrace:
    """Run Greedy Best-First Search using only the heuristic."""

    return best_first_search(
        graph=graph,
        start=start,
        goal=goal,
        algorithm="Greedy Best-First Search",
        priority_function=lambda _cost, node: heuristic_distance_m(graph, node, goal),
        allow_relaxation=False,
    )


SEARCH_ALGORITHMS: dict[str, Callable[[nx.MultiDiGraph, NodeId, NodeId], SearchTrace]] = {
    "BFS": breadth_first_search,
    "DFS": depth_first_search,
    "Dijkstra": dijkstra_search,
    "A*": a_star_search,
    "Greedy Best-First Search": greedy_best_first_search,
}


def compute_search_traces(
    graph: nx.MultiDiGraph,
    start: NodeId,
    goal: NodeId,
    selected_algorithms: list[str] | None = None,
) -> dict[str, SearchTrace]:
    """Precompute search traces so the UI only replays stored states."""

    algorithm_names = selected_algorithms or list(SEARCH_ALGORITHMS.keys())
    return {
        algorithm_name: SEARCH_ALGORITHMS[algorithm_name](graph, start, goal)
        for algorithm_name in algorithm_names
    }
