#!/usr/bin/env python3
"""
CGT care-facility name-matching + merge pass (corrected).

Fixes vs previous version:
  - robust to missing id column (care_polygons has no osm_id)
  - tightened children name patterns (removed 'tuition' and loose terms
    that were matching huge numbers of non-care businesses)

Inputs:
  care.gpkg -> tag-matched facilities (extract_care_osm.sh)
                      layers: care_points, care_polygons[, care_lines]
  all_named.gpkg -> all named features (extract_named_candidates.sh)
                      layers: named_points, named_polys

Output:
  care_facilities.gpkg (layer care_facilities) + care_facilities.csv

Requires: geopandas (pip install geopandas)
Run:      python3 name_match.py
"""
import re
import sys
import warnings
import pandas as pd
import geopandas as gpd
warnings.filterwarnings("ignore")

CARE_GPKG = "care.gpkg"
NAMED_GPKG = "all_named.gpkg"
OUT_GPKG = "care_facilities.gpkg"
OUT_CSV = "care_facilities.csv"

# ---- name patterns (case-insensitive). High-signal terms only. ----
PATTERNS = {
    "child": re.compile(r"\btadika\b|\btaska\b|\btabika\b|asuhan\s*kanak|prasekolah|"
                        r"pra[-\s]?sekolah|tabika\s*kemas|nurseri|\bnursery\b|"
                        r"child\s?care|childcare|day\s?care\s+cent|daycare\s+cent|kindergarten", re.I),
    "elder": re.compile(r"rumah\s+jagaan|rumah\s+orang\s+tua|rumah\s+warga\s+emas|warga\s+emas|"
                        r"seri\s+kenangan|jagaan\s+warga|nursing\s+home|care\s+home|old\s+folk|"
                        r"aged\s+care|retirement\s+home|elderly\s+(care|home)", re.I),
    "disab": re.compile(r"\boku\b|orang\s+kurang\s+upaya|pemulihan\s+dalam\s+komuniti|\bpdk\b|"
                        r"pusat\s+pemulihan|special\s+needs|disabilit|rehabilitation\s+cent|"
                        r"\brehab\s+cent|autism|autisme|cerebral\s+palsy|down\s+syndrome|"
                        r"sindrom\s+down|pendidikan\s+khas|special\s+(needs\s+)?school", re.I),
}
GROUP_LABEL = {"child":"Children","elder":"Older persons","disab":"Persons with disabilities"}

OTHER_TAG_RE = re.compile(r'"([^"]+)"\s*=>\s*"([^"]*)"')
def parse_other(s):
    if not isinstance(s,str): return {}
    return {k:v for k,v in OTHER_TAG_RE.findall(s)}

def tag(row, key):
    lk = key.replace(":","_")
    if lk in row and pd.notna(row[lk]) and str(row[lk])!="":
        return str(row[lk]).lower()
    ot = parse_other(row.get("other_tags",""))
    return str(ot.get(key,"")).lower()

def group_from_tags(row):
    a=tag(row,"amenity"); sff=tag(row,"social_facility:for"); hc=tag(row,"healthcare")
    spec=tag(row,"healthcare:speciality"); sch=tag(row,"school"); isced=tag(row,"isced:level")
    if a in ("childcare","kindergarten","nursery","preschool") or sff in ("child","children") \
       or isced=="0" or "paediatric" in spec or "pediatric" in spec:
        return "child"
    if a in ("nursing_home","retirement_home") or sff in ("senior","elderly") or "geriatric" in spec:
        return "elder"
    if sff=="disabled" or hc=="rehabilitation" or sch=="special":
        return "disab"
    return "general"

def name_of(row):
    for k in ("name","name_en","name_ms"):
        if k in row and pd.notna(row[k]) and str(row[k]).strip():
            return str(row[k])
    return ""

def group_from_name(nm):
    return [g for g,p in PATTERNS.items() if p.search(nm)]

def load_layer(path, layer):
    try:
        g = gpd.read_file(path, layer=layer)
        return g if len(g) else None
    except Exception:
        return None

def to_points(gdf, idprefix):
    """Reduce any geometry to a representative point; build uid + lon/lat."""
    if gdf is None or gdf.empty:
        return None
    gdf = gdf.copy()
    # anything that isn't already a Point -> representative point
    non_point = ~gdf.geometry.geom_type.isin(["Point"])
    if non_point.any():
        gdf.loc[non_point, "geometry"] = gdf.loc[non_point, "geometry"].representative_point()
    idcol = next((c for c in ["osm_id","osm_way_id","full_id","id","@id"] if c in gdf.columns), None)
    if idcol is not None:
        gdf["uid"] = idprefix + gdf[idcol].astype(str)
    else:
        gdf = gdf.reset_index(drop=True)
        gdf["uid"] = idprefix + gdf.index.astype(str)
    gdf["lon"] = gdf.geometry.x
    gdf["lat"] = gdf.geometry.y
    return gdf

# -------- 1. TAG matches --------
tag_parts=[]
for lyr,pfx in [("care_points","n"),("care_polygons","w"),("care_lines","l")]:
    g=to_points(load_layer(CARE_GPKG,lyr),pfx)
    if g is not None: tag_parts.append(g)
if not tag_parts:
    print("ERROR: no tag-matched layers in",CARE_GPKG); sys.exit(1)
tagdf=pd.concat(tag_parts,ignore_index=True)
tagdf["care_group"]=tagdf.apply(group_from_tags,axis=1)
tagdf["match_method"]="tag"
tagdf["name_out"]=tagdf.apply(name_of,axis=1)
print(f">> tag-matched features: {len(tagdf)}")

# -------- 2. NAME matches (from all named features) --------
name_parts=[]
for lyr,pfx in [("named_points","n"),("named_polys","w")]:
    g=to_points(load_layer(NAMED_GPKG,lyr),pfx)
    if g is not None: name_parts.append(g)
namedf=pd.concat(name_parts,ignore_index=True) if name_parts else gpd.GeoDataFrame()
keep=[]
if len(namedf):
    namedf["name_out"]=namedf.apply(name_of,axis=1)
    for _,r in namedf.iterrows():
        nm=r["name_out"]
        if not nm:
            continue
        hits=group_from_name(nm)
        if hits:
            row=r.copy(); row["care_group"]=hits[0]; row["match_method"]="name"
            keep.append(row)
namematch=gpd.GeoDataFrame(keep, crs=namedf.crs) if keep else gpd.GeoDataFrame()
print(f">> name-matched features: {len(namematch)}")

# -------- 3. merge + dedupe by uid --------
alldf=pd.concat([tagdf,namematch],ignore_index=True)
methods=alldf.groupby("uid")["match_method"].agg(lambda s:"both" if set(s)>={"tag","name"} else list(s)[0])
firsttag=alldf.sort_values("match_method").drop_duplicates("uid",keep="first").set_index("uid")
firsttag["match_method"]=methods
final=firsttag.reset_index()
final=gpd.GeoDataFrame(final, geometry="geometry", crs="EPSG:4326")

final["amenity"]=final.apply(lambda r:tag(r,"amenity"),axis=1)
final["social_facility"]=final.apply(lambda r:tag(r,"social_facility"),axis=1)
final["healthcare"]=final.apply(lambda r:tag(r,"healthcare"),axis=1)
final["operator"]=final.apply(lambda r:tag(r,"operator"),axis=1)
final["phone"]=final.apply(lambda r:tag(r,"phone") or tag(r,"contact:phone"),axis=1)
final["care_group_label"]=final["care_group"].map(GROUP_LABEL).fillna("General / review")
out=final[["uid","name_out","care_group","care_group_label","match_method",
           "amenity","social_facility","healthcare","operator","phone","lon","lat","geometry"]]
out=out.rename(columns={"name_out":"name"})

out.to_file(OUT_GPKG, layer="care_facilities", driver="GPKG")
out.drop(columns="geometry").to_csv(OUT_CSV, index=False)

print(f"\n>> wrote {OUT_GPKG} (layer care_facilities) and {OUT_CSV}")
print(f">> total unique facilities: {len(out)}")
print("\nBy care group:");  print(out["care_group_label"].value_counts().to_string())
print("\nBy match method:"); print(out["match_method"].value_counts().to_string())
print("\nReview 'name' matches and the 'General / review' group before using as final supply.")