"""
Build a synthetic GTFS feed (bus / metro) from route shapes and stops.

Inputs per mode:
  - lines_gdf : LineString / MultiLineString routes
  - stops_gdf : Point stops
  - assumptions: speed (km/h), dwell time (s), headway (min), service hours

Stops are snapped to each route within `snap_tol_m` and ordered along the line
using `LineString.project`. Travel times are derived from inter-stop distance
and the configured speed; dwell time is added at every stop. Service is
expressed as `frequencies.txt` (headway-based) so `stop_times.txt` carries the
relative pattern only.

Multiple modes can be merged (`merge_gtfs_tables`) into a single feed for use
with routing engines (e.g. r5py, OpenTripPlanner).
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import linemerge


@dataclass
class GTFSConfig:
    mode: str                            # "bus" or "metro"
    metric_crs: int = 32617              # local UTM (override per project)
    snap_tol_m: float = 40
    speed_kmh: float = 16
    dwell_sec: int = 25
    headway_min: float = 15
    service_start: str = "05:00:00"
    service_end: str = "23:00:00"
    calendar_start: str = "20260101"
    calendar_end: str = "20261231"
    timezone: str = "America/Panama"
    lang: str = "es"
    agency_name: Optional[str] = None
    agency_url: str = "https://example.org"
    route_type: int = field(init=False)

    def __post_init__(self) -> None:
        # GTFS route_type: 3 = bus, 1 = subway/metro
        self.route_type = 1 if self.mode == "metro" else 3


def _ensure_linestring(geom):
    if geom is None or geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString":
        merged = linemerge(geom)
        if merged.geom_type == "LineString":
            return merged
        return max(merged.geoms, key=lambda g: g.length)
    return geom


def _seconds_to_hhmmss(t: int) -> str:
    # GTFS allows >24h, no modulo
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _prefix_ids(gdf: gpd.GeoDataFrame, col: str, prefix: str) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out[col] = out[col].astype(str)
    mask = ~out[col].str.startswith(prefix)
    out.loc[mask, col] = prefix + out.loc[mask, col]
    return out


def build_route_stop_sequences(
    lines_gdf: gpd.GeoDataFrame,
    stops_gdf: gpd.GeoDataFrame,
    *,
    route_id_col: str,
    stop_id_col: str,
    cfg: GTFSConfig,
) -> pd.DataFrame:
    """For each route, find stops within `snap_tol_m` and order them by
    measure along the line. Returns route_id, stop_id, stop_sequence,
    measure_m, snap_dist_m.
    """
    lines = lines_gdf.to_crs(cfg.metric_crs).copy()
    stops = stops_gdf.to_crs(cfg.metric_crs).copy()
    lines["_line"] = lines.geometry.apply(_ensure_linestring)

    sindex = stops.sindex
    rows: list[dict] = []

    for _, r in lines.iterrows():
        line = r["_line"]
        if line is None:
            continue
        route_id = str(r[route_id_col])

        minx, miny, maxx, maxy = line.bounds
        bbox = (minx - cfg.snap_tol_m, miny - cfg.snap_tol_m,
                maxx + cfg.snap_tol_m, maxy + cfg.snap_tol_m)
        idx = list(sindex.intersection(bbox))
        if not idx:
            continue

        cand = stops.iloc[idx].copy()
        cand["_dist"] = cand.geometry.distance(line)
        cand = cand[cand["_dist"] <= cfg.snap_tol_m]
        if cand.empty:
            continue

        cand["_measure"] = cand.geometry.apply(line.project)
        cand = cand.sort_values("_measure").reset_index(drop=True)
        for seq, s in enumerate(cand.itertuples(index=False), start=1):
            rows.append({
                "route_id": route_id,
                "stop_id": str(getattr(s, stop_id_col)),
                "stop_sequence": seq,
                "measure_m": float(s._measure),
                "snap_dist_m": float(s._dist),
            })
    return pd.DataFrame(rows)


def make_gtfs_tables(
    lines_gdf: gpd.GeoDataFrame,
    stops_gdf: gpd.GeoDataFrame,
    seq_df: pd.DataFrame,
    *,
    cfg: GTFSConfig,
    route_id_col: str,
    stop_id_col: str,
    route_name_col: Optional[str] = None,
    stop_name_col: Optional[str] = None,
    service_start_col: Optional[str] = None,
    service_end_col: Optional[str] = None,
    headway_min_col: Optional[str] = None,
) -> dict[str, pd.DataFrame]:
    mode = cfg.mode
    spd_mps = cfg.speed_kmh * 1000 / 3600

    agency = pd.DataFrame([{
        "agency_id": f"AG_{mode}",
        "agency_name": cfg.agency_name or f"Transit {mode}",
        "agency_url": cfg.agency_url,
        "agency_timezone": cfg.timezone,
        "agency_lang": cfg.lang,
    }])

    stops_w = stops_gdf.to_crs(4326)
    stops = pd.DataFrame({
        "stop_id": stops_w[stop_id_col].astype(str),
        "stop_name": stops_w[stop_name_col].astype(str) if stop_name_col else stops_w[stop_id_col].astype(str),
        "stop_lat": stops_w.geometry.y,
        "stop_lon": stops_w.geometry.x,
    }).drop_duplicates("stop_id")

    routes = pd.DataFrame({
        "route_id": lines_gdf[route_id_col].astype(str),
        "route_short_name": lines_gdf[route_id_col].astype(str),
        "route_long_name": lines_gdf[route_name_col].astype(str) if route_name_col else lines_gdf[route_id_col].astype(str),
        "route_type": cfg.route_type,
        "agency_id": f"AG_{mode}",
    }).drop_duplicates("route_id")

    trips = routes[["route_id"]].copy()
    trips["service_id"] = f"WD_{mode}"
    trips["trip_id"] = trips["route_id"].apply(lambda x: f"{mode}_{x}_t0")

    st_rows: list[dict] = []
    for _, tr in trips.iterrows():
        sub = seq_df[seq_df["route_id"] == tr["route_id"]].sort_values("stop_sequence")
        if sub.empty:
            continue
        measures = sub["measure_m"].to_numpy()
        seg = np.diff(measures, prepend=measures[0])
        seg[0] = 0.0
        t = 0
        for i, (sid, d_m) in enumerate(zip(sub["stop_id"].to_numpy(), seg), start=1):
            if i > 1:
                t += int(round(float(d_m) / spd_mps))
            arr = t
            t += cfg.dwell_sec
            st_rows.append({
                "trip_id": tr["trip_id"],
                "arrival_time": _seconds_to_hhmmss(arr),
                "departure_time": _seconds_to_hhmmss(t),
                "stop_id": sid,
                "stop_sequence": i,
            })
    stop_times = pd.DataFrame(st_rows)

    line_idx = lines_gdf.set_index(lines_gdf[route_id_col].astype(str))
    freq_rows: list[dict] = []
    for _, tr in trips.iterrows():
        rid = tr["route_id"]
        start, end = cfg.service_start, cfg.service_end
        if service_start_col and service_start_col in lines_gdf.columns:
            v = line_idx.loc[rid, service_start_col]
            if pd.notna(v) and isinstance(v, str) and ":" in v:
                start = v
        if service_end_col and service_end_col in lines_gdf.columns:
            v = line_idx.loc[rid, service_end_col]
            if pd.notna(v) and isinstance(v, str) and ":" in v:
                end = v
        hw = float(line_idx.loc[rid, headway_min_col]) if (
            headway_min_col and headway_min_col in lines_gdf.columns
        ) else cfg.headway_min
        freq_rows.append({
            "trip_id": tr["trip_id"],
            "start_time": start,
            "end_time": end,
            "headway_secs": int(hw * 60),
        })
    frequencies = pd.DataFrame(freq_rows)

    calendar = pd.DataFrame([{
        "service_id": f"WD_{mode}",
        "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1,
        "saturday": 0, "sunday": 0,
        "start_date": cfg.calendar_start,
        "end_date": cfg.calendar_end,
    }])

    feed_info = pd.DataFrame([{
        "feed_publisher_name": "GeoCareTool synthetic GTFS",
        "feed_publisher_url": cfg.agency_url,
        "feed_lang": cfg.lang,
        "feed_version": f"synthetic_{mode}_v1",
    }])

    return {
        "agency.txt": agency,
        "stops.txt": stops,
        "routes.txt": routes,
        "trips.txt": trips,
        "stop_times.txt": stop_times,
        "frequencies.txt": frequencies,
        "calendar.txt": calendar,
        "feed_info.txt": feed_info,
    }


def write_gtfs_zip(tables: dict[str, pd.DataFrame], out_zip: str | Path) -> None:
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, df in tables.items():
            zf.writestr(name, df.to_csv(index=False).encode("utf-8"))


def merge_gtfs_tables(a: dict, b: dict) -> dict:
    """Merge two GTFS table dicts, deduping ID columns where appropriate."""
    dedup_keys = {
        "stops.txt": "stop_id",
        "routes.txt": "route_id",
        "trips.txt": "trip_id",
        "calendar.txt": "service_id",
        "agency.txt": "agency_id",
    }
    out = {}
    for k in sorted(set(a) | set(b)):
        if k in a and k in b:
            df = pd.concat([a[k], b[k]], ignore_index=True)
        else:
            df = (a[k] if k in a else b[k]).copy()
        if k in dedup_keys:
            df = df.drop_duplicates(dedup_keys[k])
        out[k] = df
    return out


def build_mode_gtfs(
    cfg: GTFSConfig,
    lines_path: str | Path,
    stops_path: str | Path,
    out_zip: str | Path,
    *,
    route_id_col: str,
    stop_id_col: str,
    route_name_col: Optional[str] = None,
    stop_name_col: Optional[str] = None,
    prefix_ids: bool = True,
    service_start_col: Optional[str] = None,
    service_end_col: Optional[str] = None,
    headway_min_col: Optional[str] = None,
) -> tuple[pd.DataFrame, dict]:
    lines = gpd.read_file(lines_path)
    stops = gpd.read_file(stops_path)
    if prefix_ids:
        stops = _prefix_ids(stops, stop_id_col, f"{cfg.mode}_")

    seq = build_route_stop_sequences(
        lines, stops, route_id_col=route_id_col, stop_id_col=stop_id_col, cfg=cfg
    )
    if seq.empty:
        raise RuntimeError(
            f"No stops within snap_tol_m ({cfg.snap_tol_m}) of any line for mode "
            f"'{cfg.mode}'. Check CRS and tolerance."
        )

    tables = make_gtfs_tables(
        lines, stops, seq, cfg=cfg,
        route_id_col=route_id_col, stop_id_col=stop_id_col,
        route_name_col=route_name_col, stop_name_col=stop_name_col,
        service_start_col=service_start_col, service_end_col=service_end_col,
        headway_min_col=headway_min_col,
    )
    write_gtfs_zip(tables, out_zip)
    return seq, tables
