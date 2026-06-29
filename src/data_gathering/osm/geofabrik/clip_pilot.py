#!/usr/bin/env python3
"""
Clip the OSM care-facility candidate layer to the pilot area, and apply a light
spatial de-duplication (merge near-duplicate points that share a name).

Inputs:
  care_facilities.gpkg   -> output of name_match.py (layer: care_facilities)
  PILOT                   -> your pilot-area polygon (Selangor + KL + Putrajaya)
                             any format geopandas can read (.gpkg/.geojson/.shp)

Output:
  care_facilities_pilot.gpkg (layer care_facilities) + care_facilities_pilot.csv

Usage:
  python3 clip_pilot.py                       # uses the PILOT path set below
  python3 clip_pilot.py path/to/pilot.gpkg    # or pass the polygon as an argument
  python3 clip_pilot.py pilot.gpkg layername  # if the polygon file has named layers

Requires: geopandas (pip install geopandas)
"""
import sys
import re
import warnings
import pandas as pd
import geopandas as gpd
warnings.filterwarnings("ignore")

FACILITIES = "care_facilities.gpkg"
FAC_LAYER  = "care_facilities"
PILOT      = "pilot_area.gpkg"     # <-- set this to your pilot polygon path
OUT_GPKG   = "care_facilities_pilot.gpkg"
OUT_CSV    = "care_facilities_pilot.csv"

DEDUP = True              # set False to clip only, no de-duplication
DEDUP_RADIUS_M = 50       # points within this distance sharing a name are merged
METRIC_CRS = 3375         # GDM2000 / Peninsular Malaysia RSO (metres)

# allow overriding the pilot path / layer from the command line
PILOT_LAYER = None
if len(sys.argv) >= 2: PILOT = sys.argv[1]
if len(sys.argv) >= 3: PILOT_LAYER = sys.argv[2]

def union_all(gdf):
    try:    return gdf.geometry.union_all()
    except Exception: return gdf.geometry.unary_union

def normalize(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)   # strip punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s

# -------- 1. load --------
fac = gpd.read_file(FACILITIES, layer=FAC_LAYER)
if fac.crs is None:
    fac = fac.set_crs(4326)
print(f">> facilities loaded: {len(fac)}")

pilot = gpd.read_file(PILOT, layer=PILOT_LAYER) if PILOT_LAYER else gpd.read_file(PILOT)
pilot = pilot.to_crs(fac.crs)
pilot_geom = union_all(pilot)

# -------- 2. clip to pilot polygon --------
clipped = fac[fac.geometry.within(pilot_geom)].copy()
print(f">> within pilot area: {len(clipped)}  (dropped {len(fac)-len(clipped)} outside)")

if clipped.empty:
    print("WARNING: nothing fell inside the pilot polygon. Check the polygon CRS / extent.")
    clipped.to_file(OUT_GPKG, layer="care_facilities", driver="GPKG")
    sys.exit(0)

# -------- 3. light spatial de-duplication --------
if DEDUP:
    m = clipped.to_crs(METRIC_CRS).reset_index(drop=True)

    # cluster points that fall within DEDUP_RADIUS_M of each other
    buf = m.geometry.buffer(DEDUP_RADIUS_M / 2.0)
    merged = gpd.GeoSeries([union_all(gpd.GeoDataFrame(geometry=buf))], crs=METRIC_CRS)
    clusters = merged.explode(index_parts=False).reset_index(drop=True)
    cl = gpd.GeoDataFrame({"cluster_id": range(len(clusters))}, geometry=clusters, crs=METRIC_CRS)
    m = gpd.sjoin(m, cl, predicate="within", how="left").drop(columns=["index_right"])

    # build a de-dup key: same cluster + same care group + same normalised name.
    # rows with a blank name are kept distinct (never merged blindly).
    m["norm"] = m["name"].map(normalize)
    m["rank"] = m["match_method"].map({"both": 0, "tag": 1, "name": 2}).fillna(3)
    m["filled"] = m.notna().sum(axis=1)
    m["dupkey"] = [
        f"{c}|{g}|{n}" if n else f"U|{i}"
        for i, (c, g, n) in enumerate(zip(m["cluster_id"], m["care_group"], m["norm"]))
    ]

    reps = []
    for _, g in m.groupby("dupkey", sort=False):
        g = g.sort_values(["rank", "filled"], ascending=[True, False])
        rep = g.iloc[0].copy()
        rep["n_merged"] = len(g)
        reps.append(rep)

    ded = gpd.GeoDataFrame(reps, geometry="geometry", crs=METRIC_CRS).to_crs(4326)
    ded = ded.drop(columns=[c for c in ["cluster_id","norm","rank","filled","dupkey"] if c in ded.columns])
    ded["lon"] = ded.geometry.x
    ded["lat"] = ded.geometry.y
    print(f">> after de-duplication: {len(ded)}  (merged {len(clipped)-len(ded)} near-duplicates)")
    result = ded
else:
    clipped["n_merged"] = 1
    result = clipped

# -------- 4. write --------
result.to_file(OUT_GPKG, layer="care_facilities", driver="GPKG")
result.drop(columns="geometry").to_csv(OUT_CSV, index=False)

print(f"\n>> wrote {OUT_GPKG} (layer care_facilities) and {OUT_CSV}")
print(f">> final facilities in pilot area: {len(result)}")
print("\nBy care group:");   print(result["care_group_label"].value_counts().to_string())
print("\nBy match method:");  print(result["match_method"].value_counts().to_string())
print("\nReview the 'name' matches and 'General / review' group before using as final supply.")