"""
Travel-time matrices over a public transport network.

Thin wrapper around r5py to compute origin -> destination matrices given a
GTFS feed (e.g. one built by `synthetic_gtfs`) and an OSM street network. The
output is a long-format DataFrame (from_id, to_id, travel_time) that downstream
care-deserts code can join to a demand layer to derive a per-segment minimum
travel time to the nearest care center.

r5py is an optional dependency; it is imported lazily so the rest of the
package works without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd


@dataclass
class TTMConfig:
    departure: datetime
    max_time_min: int = 90
    walking_speed_kmh: float = 4.5
    max_walking_time_min: int = 20
    transport_modes: tuple[str, ...] = ("WALK", "TRANSIT")
    crs: int = 4326


def _load_r5py():
    try:
        import r5py
    except ImportError as exc:
        raise ImportError(
            "travel_time_matrix requires r5py. Install with `pip install r5py`."
        ) from exc
    return r5py


def build_network(osm_pbf: str | Path, gtfs_paths: list[str | Path]):
    """Build an r5py TransportNetwork from an OSM PBF and one or more GTFS feeds."""
    r5py = _load_r5py()
    return r5py.TransportNetwork(str(osm_pbf), [str(p) for p in gtfs_paths])


def _prepare_points(gdf: gpd.GeoDataFrame, id_col: str, cfg: TTMConfig) -> gpd.GeoDataFrame:
    out = gdf.to_crs(cfg.crs).copy()
    if id_col not in out.columns:
        out = out.reset_index(drop=True)
        out[id_col] = out.index.astype(str)
    out[id_col] = out[id_col].astype(str)
    return out[[id_col, "geometry"]].rename(columns={id_col: "id"})


def compute_ttm(
    origins: gpd.GeoDataFrame,
    destinations: gpd.GeoDataFrame,
    network,
    cfg: TTMConfig,
    *,
    origin_id_col: str = "id",
    dest_id_col: str = "id",
) -> pd.DataFrame:
    """Compute the travel-time matrix (long format) between origins and destinations."""
    r5py = _load_r5py()

    o = _prepare_points(origins, origin_id_col, cfg)
    d = _prepare_points(destinations, dest_id_col, cfg)

    computer = r5py.TravelTimeMatrixComputer(
        network,
        origins=o,
        destinations=d,
        departure=cfg.departure,
        transport_modes=[getattr(r5py.TransportMode, m) for m in cfg.transport_modes],
        max_time=pd.Timedelta(minutes=cfg.max_time_min),
        speed_walking=cfg.walking_speed_kmh,
        max_time_walking=pd.Timedelta(minutes=cfg.max_walking_time_min),
    )
    ttm = computer.compute_travel_times()
    return ttm.rename(columns={"travel_time": "travel_time"})


def nearest_destination(
    ttm: pd.DataFrame,
    *,
    from_col: str = "from_id",
    time_col: str = "travel_time",
) -> pd.DataFrame:
    """Collapse a TTM to one row per origin: minimum travel time."""
    return (
        ttm.groupby(from_col, as_index=False)[time_col]
           .min()
           .rename(columns={time_col: "tt_min"})
    )


def aggregate_by_segment(
    ttm: pd.DataFrame,
    points_to_segments: pd.DataFrame,
    *,
    from_col: str = "from_id",
    point_id_col: str = "id",
    segment_id_col: str = "segment_id",
    time_col: str = "travel_time",
) -> pd.DataFrame:
    """Given a TTM at point granularity (e.g. household centroids) and a mapping
    from points to segments, return the minimum travel time per segment.
    """
    nearest = nearest_destination(ttm, from_col=from_col, time_col=time_col)
    joined = points_to_segments[[point_id_col, segment_id_col]].merge(
        nearest, left_on=point_id_col, right_on=from_col, how="left"
    )
    return (
        joined.groupby(segment_id_col, as_index=False)["tt_min"]
              .min()
              .rename(columns={"tt_min": "tt_pt_min"})
    )
