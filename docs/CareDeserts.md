# Care Deserts Module

`GeoCareTool.CareDeserts` — classify polygons of a city as **care deserts** by combining demand and accessibility.

## What is a care desert?

A **care desert** is the intersection of two conditions:

```
care_desert  =  high_demand  ∧  low_accessibility
```

- **High demand** = the polygon has an above-threshold population of the relevant group (children, elderly, disability…), optionally measured **within a regional group** (district, sub-district) rather than globally.
- **Low accessibility** = the polygon is not covered by any facility's catchment, measured one of two ways:
  - **Method A (isochrones)** — geometric overlap with the walking-isochrone layer from `Accessibility.Walking`.
  - **Method B (travel time)** — the polygon's centroid is more than N minutes from the nearest facility (using the TTM from `Accessibility.PublicTransport`).

The combined module (`combined_modes`) layers PT accessibility on top of walking deserts and distinguishes:

| Type | Column | Definition |
| ---- | ------ | ---------- |
| **Type 1** | `absolute_desert` | No walking access **AND** no PT access — highest-priority interventions |
| **Type 2** | `pt_reachable` | No walking access **BUT** reachable by PT — existing transport mitigates the gap |

The split tells planners *which kind of investment* the area needs.

---

## `care_deserts` — Walking-only deserts

### Demand

#### `standardize_population(gdf, *, pop_col, out_col="population_normal")`

Z-scores `pop_col`. Returns `(gdf_with_zscore_column, DemandStats(mean, std, median))`.

#### `add_high_demand_flag(gdf, *, pop_col, method="median", group_cols=None, ...)`

Flag polygons with high demand by comparing the standardised population against a threshold.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `pop_col` | — | Population column for the group. |
| `method` | `"median"` | One of `"median"`, `"mean"`, `"mean_plus_std"`. |
| `group_cols` | `None` | If given, compute the threshold *within* each group (e.g. `["dzongkhag", "gewog"]`). Otherwise use a global threshold. |
| `normalized_col` | `"population_normal"` | Where the z-score is written. |
| `out_col` | `"high_demand"` | 0/1 flag column. |
| `out_threshold_col` | `"demand_threshold"` | The numeric threshold used (one per row when `group_cols` is set). |

Zero-population polygons are always low-demand by construction.

### Accessibility

#### `coverage_from_isochrones(demand, isochrones, *, predicate="intersects", ...)`

Spatial-join demand polygons against the union of isochrone polygons. Adds:
- `covered` — 1 if any isochrone overlaps the polygon.
- `low_accessibility` — `1 - covered`.

Geometries are buffered by 0 to repair invalid topology before the join.

#### `low_accessibility_from_travel_time(gdf, *, travel_time_col, threshold_min=20, ...)`

Alternative: low accessibility = travel time exceeds `threshold_min` minutes. Sets `low_accessibility = 1` where it does.

### Dynamic threshold for irregular polygons

Census polygons vary wildly in area (urban tracts of a few hectares vs rural blocks of dozens of km²). A flat travel-time threshold unfairly penalises large polygons. These two functions inflate the threshold for big, low-density polygons:

#### `compute_area_density(gdf, *, projected_crs, total_pop_col, ...)`

Project to a metric CRS and compute `area_m2` and `density_per_km2` columns.

#### `apply_dynamic_threshold(gdf, *, travel_time_col, base_threshold_min, beta, gamma, area_quantile=0.80, ...)`

For polygons above the `area_quantile`-th area quantile, the adjusted threshold is:

```
adj = base + β·(log1p(area) - log1p(A_q)) + γ / log1p(density)
```

The `low_acc` flag is then `travel_time > adjusted_threshold`. Parameters are written to `gdf.attrs["adjustment_params"]` for traceability.

### Indicator + pipeline

#### `build_care_desert_indicator(gdf, *, low_acc_col, high_demand_col, ...)`

Multiplies the two flags. Adds:
- `care_desert` — 1 if both flags are 1.
- `care_desert_label` — the string `"care_desert"` where the flag is 1, else `None` (useful for QGIS labelling).

#### `run_care_desert(demand, isochrones, *, pop_col, demand_method="median", group_cols=None, out_path=None)`

End-to-end pipeline:

1. `coverage_from_isochrones(demand, isochrones)`
2. `add_high_demand_flag(..., method=demand_method, group_cols=group_cols)`
3. `build_care_desert_indicator(...)`
4. Optionally writes to `out_path` (FlatGeoBuf) plus a `_summary.csv`.

Returns `(gdf_with_flags, group_level_summary_df)`.

### Example

```python
from GeoCareTool.CareDeserts import run_care_desert

demand = gpd.read_file("data/thimphu/census_tracts.gpkg")
POPULATIONS = {"children": "pop_0_5", "elderly": "pop_65plus", "disability": "pop_disability"}

walk_results = {}
for group, pop_col in POPULATIONS.items():
    gdf, summary = run_care_desert(
        demand=demand,
        isochrones=isochrones,
        pop_col=pop_col,
        demand_method="median",
        group_cols=["dzongkhag", "gewog"],   # threshold within sub-district
        out_path=str(ROOT / "care_deserts" / f"deserts_walk_{group}.fgb"),
    )
    walk_results[group] = (gdf, summary)
    print(f"{group}: {int(gdf['care_desert'].sum())} desert segments")
```

---

## `combined_modes` — Walking + PT deserts

Takes the walking-deserts layer from above, overlays PT travel times, and classifies each high-demand segment into Type 1 vs Type 2 deserts.

### `CombinedConfig`

| Field | Default | Meaning |
| --- | --- | --- |
| `pt_threshold_min` | `30` | "Reachable by PT" means door-to-door travel time ≤ this. |
| `pt_unreachable_value` | `999` | Sentinel for unreachable origins in the TTM. |
| `walk_low_acc_col` | `"low_accessibility"` | Column from `care_deserts`. |
| `high_demand_col` | `"high_demand"` | Column from `care_deserts`. |
| `walk_desert_col` | `"care_desert"` | Column from `care_deserts`. |

### Functions

#### `segment_pt_travel_time(points, ttm, *, point_id_col, segment_id_col, ...)`

For each segment, the **minimum PT travel time across all internal points** (e.g. household centroids). Segments with no covered points get `unreachable_value`. Both `point_id_col` and the TTM's `from_id` are coerced to string to avoid silent merge misses on mismatched dtypes.

#### `classify_combined_deserts(walking_gdf, segment_pt, *, segment_id_col, cfg)`

Join PT travel times onto the walking-deserts GDF and add:
- `tt_pt_min` — minimum PT travel time for the segment.
- `low_acc_pt` — `1` if `tt_pt_min > pt_threshold_min`.
- `absolute_desert` — Type 1 indicator (1 = absolute desert).
- `pt_reachable` — Type 2 indicator (1 = only reachable by PT).
- `absolute_desert_label` and `pt_reachable_label` — string labels for QGIS (`"Absolute desert"` / `"PT-reachable only"`).

#### `summarize_combined(gdf, *, pop_col, label, cfg)`

A one-row DataFrame with segment counts and population totals (absolute and percent) for each desert category. Columns:

- `population_group` — the label passed in (e.g. `"children"`)
- `total_segments`, `total_population`
- For each category in `high_demand`, `low_walking_access`, `walking_desert`, `low_pt_access`, `absolute_desert_type1`, `pt_reachable_only_type2`:
  - `segments_<category>`, `pop_<category>`, `pct_pop_<category>`

#### `run_combined(*, walking_desert_path, points_path, ttm_path, pop_col, label, ...)`

End-to-end pipeline. Reads the three inputs from disk, runs `segment_pt_travel_time` → `classify_combined_deserts` → `summarize_combined`, and (optionally) writes a `combined_deserts_{name}.fgb` plus `stats_{name}.csv` to `out_dir`.

`area_filter` optionally restricts the walking layer by attribute equality or set membership, e.g. `{"prov_id": "08", "dist_id": {"08", "10"}}`.

### Example

```python
from GeoCareTool.CareDeserts import CombinedConfig, run_combined

comb_cfg = CombinedConfig(pt_threshold_min=30, pt_unreachable_value=999)
TTM_PATH        = ROOT / "accessibility" / "ttm_households_to_facilities.csv"
HOUSEHOLDS_PATH = "data/thimphu/households.gpkg"

combined_results = {}
for group, pop_col in POPULATIONS.items():
    out_gdf, stats = run_combined(
        walking_desert_path=ROOT / "care_deserts" / f"deserts_walk_{group}.fgb",
        points_path=HOUSEHOLDS_PATH,
        ttm_path=TTM_PATH,
        pop_col=pop_col,
        label=group,
        segment_id_col="tract_id",
        point_id_col="id",
        out_dir=ROOT / "care_deserts",
        name=group,
        cfg=comb_cfg,
    )
    combined_results[group] = (out_gdf, stats)
```

## License

This project is licensed under the MIT License.
