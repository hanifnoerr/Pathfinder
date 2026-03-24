# Graph Search Map Simulator

This project is a local Python simulator that replays classic graph search algorithms on a real OpenStreetMap street network. It uses `OSMnx` to download and cache the map, `NetworkX` for the graph, and `matplotlib` widgets for the live interactive controls.

The default configuration uses Melbourne only as an example:

- Place: `Melbourne, Victoria, Australia`
- Start: `Monash University Clayton campus`
- Goal: `State Library Victoria, Melbourne`

You can switch to another city, region, network type, start, or goal without changing the search or plotting logic.

## Files

- `main.py`: entry point, CLI handling, and interactive UI
- `algorithms.py`: BFS, DFS, Dijkstra, A*, and Greedy Best-First Search with replay traces
- `map_utils.py`: geocoding, graph download/cache, nearest-node lookup, and plotting helpers
- `config.py`: editable defaults for place, endpoints, network type, speed, and algorithm
- `requirements.txt`: Python dependencies

## Features

- Real street or walking network from OpenStreetMap
- Configurable `place_name`, `network_type`, `start_query`, `goal_query`
- Optional direct coordinates with `start_lat`, `start_lon`, `goal_lat`, `goal_lon`
- Precomputed step-by-step search traces for smooth replay
- Interactive matplotlib controls:
  - `Start / Resume`
  - `Stop / Pause`
  - `Next`
  - `Reset / Init`
  - speed slider
  - algorithm radio selector
- Live overlay showing:
  - selected algorithm
  - step count
  - elapsed search time
  - visited node count
  - frontier size
  - path length
  - current status
- Comparison panel listing metrics for all algorithms
- Optional CSV export of summary metrics
- Local graph caching in `cache/graphs/`

## Installation

Activate your environment, then install the dependencies.

```powershell
conda activate pathfinder
pip install -r requirements.txt
```

If you prefer using the environment's Python directly:

```powershell
C:\Users\han\anaconda3\envs\pathfinder\python.exe -m pip install -r requirements.txt
```

## Run

With the default Melbourne example:

```powershell
python main.py
```

Or with the environment Python explicitly:

```powershell
C:\Users\han\anaconda3\envs\pathfinder\python.exe main.py
```

## Configuration

Edit the defaults in `config.py`, or override them from the command line.

Example configuration values in `config.py`:

```python
DEFAULT_CONFIG = SimulationConfig(
    place_name="Melbourne, Victoria, Australia",
    network_type="walk",
    start_query="Monash University Clayton campus",
    goal_query="State Library Victoria, Melbourne",
    graph_radius_m=12000.0,
    animation_speed=8.0,
    selected_algorithm="A*",
)
```

The default example uses a route-centered graph radius so the initial Melbourne run stays responsive.

### Useful CLI examples

Run the default example but start on Dijkstra:

```powershell
python main.py --algorithm Dijkstra
```

Switch to a driving network:

```powershell
python main.py --network-type drive
```

Run in another city:

```powershell
python main.py `
  --place-name "Sydney, New South Wales, Australia" `
  --start-query "University of Sydney" `
  --goal-query "Sydney Opera House" `
  --network-type walk
```

Use direct coordinates instead of text lookup:

```powershell
python main.py `
  --place-name "Melbourne, Victoria, Australia" `
  --start-lat -37.9106 --start-lon 145.1362 `
  --goal-lat -37.8097 --goal-lon 144.9653
```

If text geocoding fails, the program will fall back to coordinates when both latitude and longitude are provided.

### Large-region performance

For large places, you can download a point-centered graph around the route corridor instead of the whole place:

```powershell
python main.py `
  --place-name "Victoria, Australia" `
  --start-query "Monash University Clayton campus" `
  --goal-query "State Library Victoria, Melbourne" `
  --graph-radius-m 12000
```

When `--graph-radius-m` is set, the simulator downloads a graph around the midpoint between start and goal, enlarged by the straight-line distance plus a buffer.

## Algorithm notes

- BFS and DFS run on the same real street graph but ignore edge weights by design.
- Dijkstra and A* use the OSM edge `length` attribute as the route cost.
- A* uses straight-line geographic distance to the goal as the heuristic.
- Greedy Best-First Search uses the heuristic only, so it is not guaranteed to return an optimal route.
- Dijkstra and A* should match on path cost when both find a path.

## Optional headless run

This is mainly an interactive GUI project, but you can precompute everything without opening the window:

```powershell
python main.py --no-gui --metrics-csv outputs/search_metrics.csv
```

## Geocoding notes

- Queries work best when they are specific, for example `State Library Victoria, Melbourne`.
- The resolver tries both the raw query and a place-qualified variant such as `<query>, <place_name>`.
- If geocoding is ambiguous or fails, provide explicit coordinates instead.
