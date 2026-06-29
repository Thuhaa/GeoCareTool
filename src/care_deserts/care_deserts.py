"""
Care deserts: low accessibility ∩ high demand.

Two interchangeable accessibility methods are supported:

  A) Isochrone coverage   -> a demand unit is "covered" if its geometry
                              intersects (or is within) the union of supply
                              isochrone polygons.
  B) Travel-time threshold -> a demand unit is "covered" if its travel time
                              to the nearest supply point is below a
                              threshold (in minutes).

For irregular polygons (e.g. census blocks of very different sizes) method B
supports a dynamic threshold correction that increases the allowed travel time
for large, sparsely populated polygons.

High demand can be measured against either a global threshold or a local
(district-level) threshold computed from a z-scored population column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd

DemandThresholdMethod = Literal["mean", "mean_plus_std", "median"]
SpatialPredicate = Literal["intersects", "within", "contains"]


# ─────────────────────────────────────────────────────────────────────────────
# Demand
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DemandStats:
    mean: float
    std: float
    median: float


def standardize_population(
    gdf: gpd.GeoDataFrame,
    *,
    pop_col: str,
    out_col: str = "population_normal",
) -> tuple[gpd.GeoDataFrame, DemandStats]:
    """Z-score `pop_col` (population per polygon)."""
    out = gdf.copy()
    x = out[pop_col].astype(float).to_numpy()
    mu = float(np.nanmean(x))
    sigma = float(np.nanstd(x, ddof=0))
    med = float(np.nanmedian(x))
    out[out_col] = np.zeros_like(x) if sigma == 0 else (x - mu) / sigma
    return out, DemandStats(mu, sigma, med)


def _threshold(values: np.ndarray, method: DemandThresholdMethod) -> float:
    arr = np.nan_to_num(values, nan=0.0)
    if arr.size == 0:
        return np.inf
    if method == "median":
        return float(np.median(arr))
    if method == "mean":
        return float(np.mean(arr))
    if method == "mean_plus_std":
        return float(np.mean(arr) + np.std(arr, ddof=0))
    raise ValueError(f"Unknown demand_method: {method}")


def add_high_demand_flag(
    gdf: gpd.GeoDataFrame,
    *,
    pop_col: str,
    method: DemandThresholdMethod = "median",
    group_cols: Optional[Sequence[str]] = None,
    normalized_col: str = "population_normal",
    out_col: str = "high_demand",
    out_threshold_col: str = "demand_threshold",
) -> gpd.GeoDataFrame:
    """Flag polygons with high demand.

    The threshold is applied to the standardized `pop_col`. If `group_cols` is
    given, the threshold is computed within each group (e.g. district); zero-
    demand polygons are always low-demand.
    """
    out, _ = standardize_population(gdf, pop_col=pop_col, out_col=normalized_col)

    if group_cols:
        thr = (
            out.groupby(list(group_cols))[normalized_col]
               .apply(lambda s: _threshold(s.to_numpy(), method))
               .rename(out_threshold_col)
               .reset_index()
        )
        out = out.merge(thr, on=list(group_cols), how="left")
        out[out_threshold_col] = out[out_threshold_col].fillna(np.inf)
    else:
        positive = out.loc[out[normalized_col] > 0, normalized_col].to_numpy()
        if positive.size == 0:
            raise ValueError("No positive demand values found.")
        out[out_threshold_col] = _threshold(positive, method)

    out[out_col] = (
        (out[normalized_col] >= out[out_threshold_col]) & (out[pop_col] > 0)
    ).astype(int)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# accessibility
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_same_crs(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame):
    if left.crs and right.crs and left.crs != right.crs:
        right = right.to_crs(left.crs)
    return left, right


def coverage_from_isochrones(
    demand: gpd.GeoDataFrame,
    isochrones: gpd.GeoDataFrame,
    *,
    predicate: SpatialPredicate = "intersects",
    out_col: str = "covered",
    low_acc_col: str = "low_accessibility",
) -> gpd.GeoDataFrame:
    """Spatial-join demand polygons against isochrones.

    `out_col` = 1 where demand overlaps the isochrone union, 0 otherwise.
    `low_acc_col` is the negation, kept for backward compatibility.
    """
    demand, isochrones = _ensure_same_crs(demand, isochrones)
    out = demand[demand.geometry.notnull() & ~demand.geometry.is_empty].copy()
    out["geometry"] = out.geometry.buffer(0)

    iso = isochrones[isochrones.geometry.notnull() & ~isochrones.geometry.is_empty].copy()
    iso["geometry"] = iso.geometry.buffer(0)

    try:
        joined = gpd.sjoin(
            out[["geometry"]], iso[["geometry"]], how="left", predicate=predicate
        )
        out[out_col] = joined.index_right.notna().groupby(level=0).any().astype(int).values
    except Exception:
        union = iso.unary_union
        out[out_col] = out.intersects(union).astype(int).values

    out[low_acc_col] = (1 - out[out_col]).astype(int)
    return out


def low_accessibility_from_travel_time(
    gdf: gpd.GeoDataFrame,
    *,
    travel_time_col: str,
    threshold_min: float = 20,
    out_col: str = "low_accessibility",
) -> gpd.GeoDataFrame:
    """Low-accessibility flag from a travel-time column (minutes)."""
    out = gdf.copy()
    out[out_col] = (out[travel_time_col] > threshold_min).astype(int)
    out.attrs["base_threshold_min"] = threshold_min
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic threshold correction (irregular polygons)
# ─────────────────────────────────────────────────────────────────────────────

def compute_area_density(
    gdf: gpd.GeoDataFrame,
    *,
    projected_crs: str | int,
    total_pop_col: str,
    area_col: str = "area_m2",
    density_col: str = "density_per_km2",
) -> gpd.GeoDataFrame:
    """Project to a metric CRS and compute polygon area (m²) and density."""
    out = gdf.to_crs(projected_crs).copy()
    out[area_col] = out.geometry.area
    out[density_col] = out[total_pop_col] / (out[area_col] / 1e6)
    return out


def apply_dynamic_threshold(
    gdf: gpd.GeoDataFrame,
    *,
    travel_time_col: str,
    base_threshold_min: float,
    beta: float,
    gamma: float,
    area_quantile: float = 0.80,
    area_col: str = "area_m2",
    density_col: str = "density_per_km2",
    out_threshold_col: str = "adjusted_threshold",
    out_low_acc_col: str = "low_accessibility",
) -> gpd.GeoDataFrame:
    """Inflate the travel-time threshold for large, low-density polygons:

        adj = base + beta * (log1p(area) - log1p(A_q)) + gamma / log1p(density)
              for polygons with area > q-th area quantile, else 0.

    Then `low_acc = travel_time > adjusted_threshold`.
    """
    if area_col not in gdf.columns or density_col not in gdf.columns:
        raise KeyError(
            f"Missing '{area_col}' / '{density_col}'. Run compute_area_density first."
        )
    out = gdf.copy()
    A_thr = out[area_col].quantile(area_quantile)

    def _adj(row):
        if row[area_col] > A_thr:
            return (
                beta * (np.log1p(row[area_col]) - np.log1p(A_thr))
                + gamma / np.log1p(row[density_col])
            )
        return 0.0

    out["combined_adjustment"] = out.apply(_adj, axis=1)
    out[out_threshold_col] = base_threshold_min + out["combined_adjustment"]
    out[out_low_acc_col] = (out[travel_time_col] > out[out_threshold_col]).astype(int)
    out.attrs["adjustment_params"] = dict(
        beta=beta, gamma=gamma, area_quantile=area_quantile,
        base_threshold_min=base_threshold_min, A_threshold=float(A_thr),
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Final indicator + end-to-end pipeline
# ─────────────────────────────────────────────────────────────────────────────

def build_care_desert_indicator(
    gdf: gpd.GeoDataFrame,
    *,
    low_acc_col: str = "low_accessibility",
    high_demand_col: str = "high_demand",
    out_col: str = "care_desert",
    out_label_col: str = "care_desert_label",
    label: str = "care_desert",
) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out[out_col] = (out[low_acc_col] * out[high_demand_col]).astype(int)
    out[out_label_col] = np.where(out[out_col] == 1, label, np.nan)
    return out


def run_care_desert(
    demand: gpd.GeoDataFrame,
    isochrones: gpd.GeoDataFrame,
    *,
    pop_col: str,
    demand_method: DemandThresholdMethod = "median",
    group_cols: Optional[Sequence[str]] = None,
    out_path: Optional[str] = None,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """End-to-end: isochrone coverage + (grouped) high-demand flag + indicator.

    Returns (gdf with flags, group-level summary).
    """
    gdf = coverage_from_isochrones(demand, isochrones)
    gdf[pop_col] = pd.to_numeric(gdf[pop_col], errors="coerce").fillna(0)
    gdf = add_high_demand_flag(
        gdf, pop_col=pop_col, method=demand_method, group_cols=group_cols
    )
    gdf = build_care_desert_indicator(gdf)

    summary = _summarize(gdf, pop_col=pop_col, group_cols=group_cols)

    if out_path:
        out = gdf.copy()
        out.to_file(out_path, driver="FlatGeobuf")
        summary.to_csv(str(out_path).rsplit(".", 1)[0] + "_summary.csv", index=False)

    return gdf, summary


def _summarize(
    gdf: gpd.GeoDataFrame,
    *,
    pop_col: str,
    group_cols: Optional[Sequence[str]],
    desert_col: str = "care_desert",
) -> pd.DataFrame:
    if not group_cols:
        return pd.DataFrame([{
            "segments": len(gdf),
            "deserts": int(gdf[desert_col].sum()),
            "pop_total": float(gdf[pop_col].sum()),
            "pop_in_deserts": float(gdf.loc[gdf[desert_col] == 1, pop_col].sum()),
        }])

    grp = gdf.groupby(list(group_cols))
    res = grp.agg(
        segments=(desert_col, "size"),
        deserts=(desert_col, "sum"),
        pop_total=(pop_col, "sum"),
    ).reset_index()
    pop_des = (
        gdf[gdf[desert_col] == 1]
        .groupby(list(group_cols))[pop_col].sum()
        .rename("pop_in_deserts").reset_index()
    )
    res = res.merge(pop_des, on=list(group_cols), how="left").fillna({"pop_in_deserts": 0})
    res["pct_pop_in_deserts"] = np.where(
        res["pop_total"] > 0, 100 * res["pop_in_deserts"] / res["pop_total"], 0.0
    )
    return res
