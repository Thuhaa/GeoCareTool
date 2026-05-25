# Accessibility Module

`GeoCareTool.Accessibility` — two sub-modules for measuring how reachable each care facility is.

| Sub-module | Method | What you get |
| ---------- | ------ | ------------ |
| `Walking` | OSM pedestrian network + concave-hull isochrones | One polygon per facility = the area you can walk to in N minutes |
| `PublicTransport` | Synthetic GTFS + `r5py` routing | Travel-time matrix from population origins to facilities |

Both produce inputs for `CareDeserts`.

---

## `Walking` — Pedestrian isochrones

### Concept

An **isochrone** is a polygon of all points reachable from an origin within a time budget. We build one per facility by:

1. Downloading the **OSM walking graph** in tiles that cover the facility set (`download_tile_networks`), cached on disk.
2. Adding a `travel_time` attribute to every edge: `edge.length / walk_speed_ms`.
3. For each facility, finding the nearest graph node and computing an `ego_graph` of all nodes whose cumulative `travel_time` ≤ the budget.
4. Wrapping those node coordinates in a **concave hull** (with a configurable tightness ratio), buffering by `node_buffer_m`, and simplifying.
5. If anything fails (isolated facility, missing tile), falling back to a **straight-line circular buffer** at the same straight-line walking distance.

### `IsochroneConfig`

Single source of truth for parameters. All fields have sensible defaults.

| Field | Default | Meaning |
| --- | --- | --- |
| `walk_speed_ms` | `1.4` | Walking speed in m/s (≈ 5 km/h, standard adult). |
| `walk_time_min` | `20` | Time budget in minutes. |
| `tile_size_deg` | `0.25` | Side length of OSM download tiles (degrees). |
| `tile_buffer_deg` | `0.025` | Tiles overlap by this much so edge facilities still have a complete graph. |
| `concave_ratio` | `0.3` | Hull tightness — `0` = tightest concave, `1` = convex hull. |
| `node_buffer_m` | `120` | Buffer applied to the hull (metric CRS) to fill gaps between reached nodes. |
| `simplify_m` | `30` | Vertex simplification tolerance for the final polygon. |
| `metric_crs` | `"EPSG:3857"` | Override with your city's local UTM zone for accurate metres. |
| `geographic_crs` | `"EPSG:4326"` | CRS the input/output geometries use. |
| `cache_dir` | `None` | osmnx cache folder. Set this — re-runs become instant. |
| `keep_fields` | `[]` | Columns from the input facility GDF to copy into the output. |

Two derived properties: `walk_time_s` and `walk_distance_m` (used by the fallback circle).

### `download_tile_networks(points, cfg)`

Downloads and caches one OSM walking graph per tile containing at least one facility. Each edge gets a `travel_time` attribute. Returns `{tile_key: nx.MultiDiGraph | None}` — `None` for tiles where the OSM download failed. Includes a `time.sleep(0.5)` between tiles to be a polite Overpass user.

### `isochrone_smooth(G, center_node, cfg)`

Compute the smooth isochrone polygon from `center_node` in graph `G`. Returns a shapely polygon in `cfg.geographic_crs`, or `None` if fewer than 3 nodes are reached. Falls back to a convex hull if concave-hull computation fails.

### `fallback_circle(lon, lat, cfg)`

Circular buffer of radius `walk_distance_m` (= `walk_speed_ms × walk_time_s`). Used when no network is available for a facility.

### `compute_isochrones(facilities, cfg, *, lon_col="lon", lat_col="lat", networks=None)`

Main entry point. For each row in `facilities`, computes either a smooth isochrone or a fallback circle. Returns a GeoDataFrame with the polygon geometries plus any columns listed in `cfg.keep_fields` and a `source` column ("osm" or "fallback") for transparency.

### Example

```python
from GeoCareTool.Accessibility.Walking import (
    IsochroneConfig, download_tile_networks, compute_isochrones,
)

cfg = IsochroneConfig(
    walk_speed_ms=1.4,
    walk_time_min=20,
    metric_crs="EPSG:32646",                  # UTM 46N for Bhutan
    cache_dir=ROOT / "osmnx_cache",
    keep_fields=["place_id", "name", "category"],
)
facilities["lon"] = facilities.geometry.x
facilities["lat"] = facilities.geometry.y

networks = download_tile_networks(zip(facilities["lon"], facilities["lat"]), cfg)
isochrones = compute_isochrones(facilities, cfg, networks=networks)
isochrones.to_file(ROOT / "accessibility" / "isochrones_walk_20min.gpkg", driver="GPKG")
```

---

## `PublicTransport` — Synthetic GTFS + Travel-time matrix

Two stages:

1. **`synthetic_gtfs`** — build a valid GTFS feed from minimal inputs (line shapes + stops).
2. **`travel_time_matrix`** — feed it to `r5py` with an OSM PBF to compute origin→destination travel times.

### Why "synthetic" GTFS?

Many cities don't publish an official GTFS feed. We **synthesise one** from:
- A GeoDataFrame of **line shapes** (route geometries, LineStrings).
- A GeoDataFrame of **stops** (Points).
- A handful of operational assumptions (speed, dwell, headway, service hours).

> **Document your assumptions.** A 20-minute headway vs 5-minute headway changes the answer dramatically. The feed is only as good as the inputs.

### `synthetic_gtfs.GTFSConfig`

| Field | Default | Meaning |
| --- | --- | --- |
| `mode` | — | `"bus"` or `"metro"`. Sets `route_type` (3 or 1). |
| `metric_crs` | `32617` | Local UTM zone for the snap operation. |
| `snap_tol_m` | `40` | Max distance to snap a stop to its nearest line. |
| `speed_kmh` | `16` | Vehicle running speed between stops. |
| `dwell_sec` | `25` | Seconds spent at each stop. |
| `headway_min` | `15` | Minutes between consecutive vehicles on a route. |
| `service_start` / `service_end` | `05:00:00` / `23:00:00` | Service hours (`HH:MM:SS`). |
| `calendar_start` / `calendar_end` | `20260101` / `20261231` | `calendar.txt` validity window. |
| `timezone` | `"America/Panama"` | IANA TZ for `agency.txt`. |
| `lang` | `"es"` | Agency language. |
| `agency_name`, `agency_url` | | Cosmetic GTFS fields. |

### Functions

#### `build_route_stop_sequences(lines_gdf, stops_gdf, *, route_id_col, stop_id_col, cfg)`

For each route, finds stops within `snap_tol_m` and orders them by their measure along the line (via `LineString.project`). Returns a long DataFrame with `route_id, stop_id, stop_sequence, measure_m, snap_dist_m`.

#### `make_gtfs_tables(...)`

Builds the eight standard GTFS DataFrames (`agency.txt`, `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `frequencies.txt`, …) from the sequences. Service is expressed as **`frequencies.txt`** (headway-based), so `stop_times.txt` carries only the relative pattern.

#### `write_gtfs_zip(tables, out_zip)`

Pack a dict of DataFrames into a valid GTFS zip.

#### `merge_gtfs_tables(a, b)`

Combine two GTFS table dicts (e.g. bus + metro) into one feed. Dedupes ID columns where appropriate (`stop_id`, `route_id`, `trip_id`, `service_id`, `agency_id`).

#### `build_mode_gtfs(cfg, lines_path, stops_path, out_zip, ...)`

End-to-end convenience for one mode: read GeoJSON inputs → build sequences → make tables → write zip. Returns `(seq_df, tables_dict)`.

### Example

```python
from GeoCareTool.Accessibility.PublicTransport import GTFSConfig, build_mode_gtfs

bus_cfg = GTFSConfig(
    mode="bus",
    metric_crs=32646,
    snap_tol_m=40,
    speed_kmh=18,
    dwell_sec=25,
    headway_min=20,
    service_start="06:00:00",
    service_end="21:00:00",
    timezone="Asia/Thimphu",
    lang="en",
    agency_name="Thimphu City Bus (synthetic)",
)

bus_seq, bus_tables = build_mode_gtfs(
    bus_cfg,
    lines_path="data/thimphu/bus_routes.geojson",
    stops_path="data/thimphu/bus_stops.geojson",
    out_zip=ROOT / "accessibility" / "gtfs_bus_thimphu.zip",
    route_id_col="route_id",
    route_name_col="route_name",
    stop_id_col="stop_id",
    stop_name_col="stop_name",
)
```

### `travel_time_matrix` — `r5py` wrapper

#### `TTMConfig`

| Field | Default | Meaning |
| --- | --- | --- |
| `departure` | — | `datetime` object — schedules matter, so the answer depends on this. |
| `max_time_min` | `90` | Cap on returned travel times (origins beyond this get NaN). |
| `walking_speed_kmh` | `4.5` | Used for first/last-mile walking. |
| `max_walking_time_min` | `20` | Max walking on either leg. |
| `transport_modes` | `("WALK", "TRANSIT")` | Allowed r5py modes. |
| `crs` | `4326` | CRS to feed into r5py (it wants WGS84). |

#### `build_network(osm_pbf, gtfs_paths)`

Returns an `r5py.TransportNetwork` from an OSM PBF (download from Geofabrik) and a list of GTFS feed paths. **`r5py` is a lazy import** — you only pay the JVM startup cost if you actually call this.

#### `compute_ttm(origins, destinations, network, cfg, ...)`

Returns a long-format DataFrame `from_id, to_id, travel_time` (minutes). Origins and destinations are GeoDataFrames; if no `id` column exists it falls back to the index.

#### `nearest_destination(ttm)`

Collapse to one row per origin = minimum travel time to *any* destination. Useful when the question is "how long to the nearest facility?" rather than the full matrix.

#### `aggregate_by_segment(ttm, points_to_segments, ...)`

Given a TTM at point granularity (household centroids) and a mapping from points to demand segments, return the minimum travel time per segment.

### Example

```python
from GeoCareTool.Accessibility.PublicTransport import (
    TTMConfig, build_network, compute_ttm,
)
from datetime import datetime

network = build_network(
    osm_pbf="data/thimphu/bhutan-latest.osm.pbf",
    gtfs_paths=[ROOT / "accessibility" / "gtfs_bus_thimphu.zip"],
)
cfg = TTMConfig(
    departure=datetime(2026, 5, 5, 8, 0, 0),   # weekday 8 AM
    max_time_min=60,
    walking_speed_kmh=4.5,
    max_walking_time_min=15,
)
ttm = compute_ttm(origins=households, destinations=facilities, network=network, cfg=cfg)
ttm.to_csv(ROOT / "accessibility" / "ttm_households_to_facilities.csv", index=False)
```

---

## Dependencies

- `Walking`: `osmnx`, `networkx`, `shapely`, `geopandas`.
- `PublicTransport.synthetic_gtfs`: `geopandas`, `shapely`.
- `PublicTransport.travel_time_matrix`: **`r5py`** (lazy import) — requires Java 11+.

## License

This project is licensed under the MIT License.
