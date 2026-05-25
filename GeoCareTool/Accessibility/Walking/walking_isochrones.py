"""
Walking isochrones from OSM pedestrian networks.

Computes smooth isochrone polygons (concave hull of reachable nodes) for a set
of supply-side facilities (centers of care). For each facility:

  1. Snap to nearest node in the local OSM pedestrian graph.
  2. ego_graph by travel time (length / walking speed).
  3. concave_hull of reached node coordinates -> smooth polygon.
  4. Buffer + simplify in a metric CRS for cleaner geometry.
  5. Fallback to a circular buffer of equivalent walking distance if the
     network is unavailable or the ego graph is too small.

Networks are downloaded once per spatial tile (degree-based grid) and cached on
disk via osmnx, so multiple facilities in the same tile share the graph.
"""

from __future__ import annotations

import logging
import time
import warnings
from dataclasses import dataclass, field
from math import floor
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import concave_hull
from shapely.geometry import MultiPoint, Point

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)


@dataclass
class IsochroneConfig:
    """Configuration for a walking isochrone run."""

    # Walking model
    walk_speed_ms: float = 1.4          # m/s (~5 km/h)
    walk_time_min: float = 20           # minutes

    # Spatial tiling for OSM downloads (degrees)
    tile_size_deg: float = 0.25
    tile_buffer_deg: float = 0.025

    # Concave hull / smoothing
    concave_ratio: float = 0.3          # 0 = tightest, 1 = convex hull
    node_buffer_m: float = 120          # metric buffer to fill gaps
    simplify_m: float = 30              # vertex simplification

    # CRS
    metric_crs: str = "EPSG:3857"       # override with a local UTM zone
    geographic_crs: str = "EPSG:4326"

    # OSM cache directory (osmnx-managed)
    cache_dir: Optional[Path] = None

    # Fields from the input facility GDF to keep in the output
    keep_fields: list[str] = field(default_factory=list)

    @property
    def walk_time_s(self) -> float:
        return self.walk_time_min * 60

    @property
    def walk_distance_m(self) -> float:
        return self.walk_speed_ms * self.walk_time_s


def _tile_key(lon: float, lat: float, size: float) -> tuple[int, int]:
    return (floor(lon / size), floor(lat / size))


def _tile_bbox(col: int, row: int, size: float, buf: float) -> tuple[float, float, float, float]:
    return (
        col * size - buf,
        row * size - buf,
        (col + 1) * size + buf,
        (row + 1) * size + buf,
    )


def _add_travel_time(G: nx.MultiDiGraph, speed_ms: float) -> nx.MultiDiGraph:
    for _, _, data in G.edges(data=True):
        data["travel_time"] = data.get("length", 0) / speed_ms
    return G


def _configure_osmnx(cfg: IsochroneConfig) -> None:
    ox.settings.use_cache = True
    if cfg.cache_dir is not None:
        ox.settings.cache_folder = str(cfg.cache_dir)
    ox.settings.timeout = 300
    ox.settings.log_console = False


def download_tile_networks(
    points: Iterable[tuple[float, float]],
    cfg: IsochroneConfig,
) -> dict[tuple[int, int], Optional[nx.MultiDiGraph]]:
    """Download (and cache) the OSM walking graph for every tile that contains
    at least one supply point. Returns {tile_key: graph_or_None}.
    """
    _configure_osmnx(cfg)
    pts = pd.DataFrame(points, columns=["lon", "lat"]).dropna()
    needed = {_tile_key(r.lon, r.lat, cfg.tile_size_deg) for _, r in pts.iterrows()}

    networks: dict[tuple[int, int], Optional[nx.MultiDiGraph]] = {}
    for i, (col, row) in enumerate(sorted(needed), 1):
        bbox = _tile_bbox(col, row, cfg.tile_size_deg, cfg.tile_buffer_deg)
        log.info("Tile %d/%d %s bbox=%s", i, len(needed), (col, row),
                 tuple(round(x, 3) for x in bbox))
        try:
            G = ox.graph_from_bbox(bbox, network_type="walk", retain_all=True)
            networks[(col, row)] = _add_travel_time(G, cfg.walk_speed_ms)
        except Exception as exc:
            log.warning("No network for tile %s: %s", (col, row), exc)
            networks[(col, row)] = None
        time.sleep(0.5)
    return networks


def isochrone_smooth(
    G: nx.MultiDiGraph,
    center_node: int,
    cfg: IsochroneConfig,
):
    """Return a smooth isochrone polygon (in WGS84) reachable from `center_node`.
    Returns None if too few nodes are reached.
    """
    sub = nx.ego_graph(G, center_node, radius=cfg.walk_time_s, distance="travel_time")
    if len(sub) < 3:
        return None

    coords = [(d["x"], d["y"]) for _, d in sub.nodes(data=True)]
    mp = MultiPoint(coords)

    try:
        poly = concave_hull(mp, ratio=cfg.concave_ratio)
    except Exception:
        poly = mp.convex_hull
    if poly.is_empty or poly.area == 0:
        return None

    gs = gpd.GeoSeries([poly], crs=cfg.geographic_crs).to_crs(cfg.metric_crs)
    poly_m = gs.iloc[0].buffer(cfg.node_buffer_m).simplify(cfg.simplify_m)
    return gpd.GeoSeries([poly_m], crs=cfg.metric_crs).to_crs(cfg.geographic_crs).iloc[0]


def fallback_circle(lon: float, lat: float, cfg: IsochroneConfig):
    """Circular buffer of radius equal to straight-line walking distance."""
    pt_m = gpd.GeoSeries([Point(lon, lat)], crs=cfg.geographic_crs).to_crs(cfg.metric_crs).iloc[0]
    circle = pt_m.buffer(cfg.walk_distance_m).simplify(cfg.simplify_m)
    return gpd.GeoSeries([circle], crs=cfg.metric_crs).to_crs(cfg.geographic_crs).iloc[0]


def compute_isochrones(
    facilities: gpd.GeoDataFrame,
    cfg: IsochroneConfig,
    *,
    lon_col: str = "lon",
    lat_col: str = "lat",
    networks: Optional[dict] = None,
) -> gpd.GeoDataFrame:
    """Compute one isochrone polygon per row in `facilities`.

    The input GeoDataFrame must have lon/lat columns (degrees, WGS84). If the
    cached `networks` dict is not supplied it is built on the fly from the
    facility coordinates.
    """
    if networks is None:
        networks = download_tile_networks(
            zip(facilities[lon_col], facilities[lat_col]), cfg
        )

    keep = [c for c in cfg.keep_fields if c in facilities.columns]
    rows: list[dict] = []
    n_ok = n_fb = n_skip = 0

    for _, row in facilities.iterrows():
        lon, lat = row.get(lon_col), row.get(lat_col)
        if pd.isna(lon) or pd.isna(lat):
            n_skip += 1
            continue

        G = networks.get(_tile_key(lon, lat, cfg.tile_size_deg))
        poly = None
        source = "osm"
        if G is not None:
            try:
                cn = ox.nearest_nodes(G, lon, lat)
                poly = isochrone_smooth(G, cn, cfg)
            except Exception as exc:
                log.debug("Isochrone failed at (%.5f,%.5f): %s", lat, lon, exc)

        if poly is None:
            poly = fallback_circle(lon, lat, cfg)
            source = "fallback_circular"
            n_fb += 1
        else:
            n_ok += 1

        rec = {c: row[c] for c in keep}
        rec["isochrone_source"] = source
        rec["geometry"] = poly
        rows.append(rec)

    log.info("Isochrones: osm=%d fallback=%d skipped=%d", n_ok, n_fb, n_skip)
    if not rows:
        return gpd.GeoDataFrame(columns=[*keep, "isochrone_source", "geometry"], crs=cfg.geographic_crs)
    return gpd.GeoDataFrame(rows, crs=cfg.geographic_crs)
