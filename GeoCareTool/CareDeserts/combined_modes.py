"""
Combined walking + public-transport care deserts.

Starts from the walking-based care-desert layer (with `low_accessibility`,
`high_demand` and `care_desert` flags already computed) and overlays
a public-transport travel-time threshold to classify each high-demand
segment into:

  Type 1  absolute_desert        no walk access AND no PT access
  Type 2  pt_reachable           no walk access BUT PT-accessible

The PT travel time per segment is the minimum across all internal points
(e.g. household centroids) that fall inside the segment, joined to a
precomputed origin->nearest-destination travel-time table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd


@dataclass
class CombinedConfig:
    pt_threshold_min: float = 30
    pt_unreachable_value: float = 999
    walk_low_acc_col: str = "low_accessibility"
    high_demand_col: str = "high_demand"
    walk_desert_col: str = "care_desert"


def _find_col(df: pd.DataFrame, name_lower: str) -> str:
    for c in df.columns:
        if str(c).lower() == name_lower:
            return c
    raise KeyError(f"Column '{name_lower}' not found. Available: {list(df.columns)}")


def segment_pt_travel_time(
    points: gpd.GeoDataFrame | pd.DataFrame,
    ttm: pd.DataFrame,
    *,
    point_id_col: str,
    segment_id_col: str,
    ttm_from_col: str = "from_id",
    ttm_time_col: str = "travel_time",
    unreachable_value: float = 999,
) -> pd.DataFrame:
    """For each segment, the minimum PT travel time across all of its points.

    Both `point_id_col` and `ttm_from_col` are coerced to string for safety.
    Segments with no covered points get `unreachable_value`.
    """
    pts = pd.DataFrame(points).copy()
    pts[point_id_col] = pts[point_id_col].astype(str)
    pts[segment_id_col] = pts[segment_id_col].astype(str).str.strip()

    nearest = (
        ttm.assign(**{ttm_from_col: ttm[ttm_from_col].astype(str)})
           .groupby(ttm_from_col, as_index=False)[ttm_time_col]
           .min()
           .rename(columns={ttm_time_col: "_tt_raw"})
    )
    joined = pts[[point_id_col, segment_id_col]].merge(
        nearest, left_on=point_id_col, right_on=ttm_from_col, how="left"
    )
    out = (
        joined.groupby(segment_id_col, as_index=False)["_tt_raw"]
              .min()
              .rename(columns={"_tt_raw": "tt_pt_min"})
    )
    out["tt_pt_min"] = out["tt_pt_min"].fillna(unreachable_value)
    return out


def classify_combined_deserts(
    walking_gdf: gpd.GeoDataFrame,
    segment_pt: pd.DataFrame,
    *,
    segment_id_col: str,
    cfg: CombinedConfig = CombinedConfig(),
) -> gpd.GeoDataFrame:
    """Join PT travel times onto the walking-deserts GDF and classify."""
    out = walking_gdf.copy()
    seg_col_actual = _find_col(out, segment_id_col.lower())
    out[seg_col_actual] = out[seg_col_actual].astype(str).str.strip()

    out = out.merge(
        segment_pt.rename(columns={segment_id_col: "_seg_key"}),
        left_on=seg_col_actual, right_on="_seg_key", how="left",
    ).drop(columns=["_seg_key"], errors="ignore")

    out["tt_pt_min"] = out["tt_pt_min"].fillna(cfg.pt_unreachable_value)
    out["low_acc_pt"] = (out["tt_pt_min"] > cfg.pt_threshold_min).astype(int)

    for col in (cfg.walk_low_acc_col, cfg.high_demand_col, cfg.walk_desert_col):
        if col not in out.columns:
            raise KeyError(f"Walking-deserts layer missing column '{col}'.")

    high_dem = out[cfg.high_demand_col] == 1
    no_walk = out[cfg.walk_low_acc_col] == 1
    no_pt = out["low_acc_pt"] == 1

    out["absolute_desert"] = (no_walk & no_pt & high_dem).astype(int)
    out["pt_reachable"] = (no_walk & ~no_pt & high_dem).astype(int)
    out["absolute_desert_label"] = np.where(out["absolute_desert"] == 1, "Absolute desert", None)
    out["pt_reachable_label"] = np.where(out["pt_reachable"] == 1, "PT-reachable only", None)
    return out


def summarize_combined(
    gdf: gpd.GeoDataFrame,
    *,
    pop_col: str,
    label: str,
    cfg: CombinedConfig = CombinedConfig(),
) -> pd.DataFrame:
    """Population & segment counts for each desert category."""
    g = gdf.copy()
    g["_pop"] = pd.to_numeric(g[pop_col], errors="coerce").fillna(0)
    pop = g["_pop"]
    total_pop = float(pop.sum())

    def _seg(c): return int((g[c] == 1).sum())
    def _pop(c): return float(pop[g[c] == 1].sum())
    def _pct(num, den): return round(100 * num / den, 2) if den > 0 else np.nan

    cats = [
        ("high_demand", cfg.high_demand_col),
        ("low_walking_access", cfg.walk_low_acc_col),
        ("walking_desert", cfg.walk_desert_col),
        ("low_pt_access", "low_acc_pt"),
        ("absolute_desert_type1", "absolute_desert"),
        ("pt_reachable_only_type2", "pt_reachable"),
    ]
    row = {"population_group": label, "total_segments": len(g), "total_population": int(total_pop)}
    for name, col in cats:
        s, p = _seg(col), _pop(col)
        row[f"segments_{name}"] = s
        row[f"pop_{name}"] = int(p)
        row[f"pct_pop_{name}"] = _pct(p, total_pop)
    return pd.DataFrame([row])


def run_combined(
    *,
    walking_desert_path: str | Path,
    points_path: str | Path,
    ttm_path: str | Path,
    pop_col: str,
    label: str,
    segment_id_col: str = "codigo",
    point_id_col: str = "_id",
    out_dir: Optional[str | Path] = None,
    name: Optional[str] = None,
    area_filter: Optional[dict] = None,
    cfg: CombinedConfig = CombinedConfig(),
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """End-to-end pipeline mirroring the combined-modes pipeline.

    `area_filter` optionally restricts the walking layer by attribute equality
    or membership, e.g. `{"prov_id": "08", "dist_id": {"08", "10"}}`.
    """
    gdf = gpd.read_file(walking_desert_path)
    if area_filter:
        for k, v in area_filter.items():
            col = _find_col(gdf, k.lower())
            mask = gdf[col].isin(v) if isinstance(v, (set, list, tuple)) else (gdf[col] == v)
            gdf = gdf[mask]
        gdf = gdf.reset_index(drop=True)

    pts = gpd.read_file(points_path)
    if point_id_col not in pts.columns:
        pts = pts.reset_index(drop=True)
        pts[point_id_col] = pts.index.astype(str)

    ttm = pd.read_csv(ttm_path, dtype={"from_id": str, "to_id": str})

    seg_pt = segment_pt_travel_time(
        pts, ttm,
        point_id_col=point_id_col,
        segment_id_col=_find_col(pts, segment_id_col.lower()),
        unreachable_value=cfg.pt_unreachable_value,
    )

    out_gdf = classify_combined_deserts(
        gdf, seg_pt, segment_id_col=segment_id_col, cfg=cfg
    )
    stats = summarize_combined(out_gdf, pop_col=pop_col, label=label, cfg=cfg)

    if out_dir and name:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_gdf.to_file(out_dir / f"combined_deserts_{name}.fgb", driver="FlatGeobuf")
        stats.to_csv(out_dir / f"stats_{name}.csv", index=False)

    return out_gdf, stats
