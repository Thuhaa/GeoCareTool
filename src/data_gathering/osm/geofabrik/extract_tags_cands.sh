#!/usr/bin/env bash
# =============================================================================
# CGT care-facility extraction from an OSM PBF
# Comprehensive TAG-based filter (children / elderly / disability) -> GeoPackage
#
# Requires: osmium-tool, GDAL (ogr2ogr, ogrinfo)
#   Ubuntu/Debian:  sudo apt install osmium-tool gdal-bin
#   macOS (brew):   brew install osmium-tool gdal
#
# Input: a regional .osm.pbf  (e.g. Geofabrik malaysia-singapore-brunei-latest.osm.pbf)
# Output: care.gpkg  with layers  care_points, care_polygons, care_lines
#
# NOTE: this exhausts plausible TAGS only. Untagged / name-only facilities are
# NOT caught here -> run the separate name-matching pass on care.gpkg afterwards.
# =============================================================================
set -euo pipefail

# ---- 0. settings -----------------------------------------------------------
INPUT="${1:-malaysia-singapore-brunei-260628.osm.pbf}"   # pass your PBF as arg 1
FILTERED="care_tagged.osm.pbf"
OUT="care.gpkg"
CONF="osmconf.ini"

command -v osmium >/dev/null || { echo "ERROR: osmium not found"; exit 1; }
command -v ogr2ogr >/dev/null || { echo "ERROR: ogr2ogr (GDAL) not found"; exit 1; }
[ -f "$INPUT" ] || { echo "ERROR: input PBF not found: $INPUT"; exit 1; }

echo ">> Filtering care facilities from: $INPUT"

# ---- 1. comprehensive tag filter ------------------------------------------
# Each line is OR'd. nwr = nodes, ways, relations. KEY (no value) matches ANY value.
osmium tags-filter "$INPUT" \
  nwr/amenity=childcare,kindergarten,nursery,preschool,nursing_home,retirement_home,social_facility,social_centre,care_home \
  nwr/social_facility \
  nwr/social_facility:for \
  nwr/healthcare=rehabilitation,centre,nursing,hospice,counselling \
  nwr/school=special \
  nwr/isced:level=0 \
  nwr/office=charity \
  nwr/healthcare:speciality=paediatrics,geriatrics \
  --overwrite -o "$FILTERED"

echo ">> Wrote filtered subset: $FILTERED"

# ---- 2. GDAL OSM config: promote care tags to real columns ----------------
cat > "$CONF" <<'EOF'
[general]
attribute_name_laundering=yes
report_all_nodes=no
report_all_ways=no
closed_ways_are_polygons=aeroway,amenity,boundary,building,craft,geological,historic,landuse,leisure,military,natural,office,place,shop,sport,tourism

# attribute set reused across geometry layers
[points]
osm_id=yes
osm_version=no
osm_timestamp=no
attributes=name,name:en,name:ms,alt_name,old_name,amenity,healthcare,healthcare:speciality,social_facility,social_facility:for,school,isced:level,operator,operator:type,office,phone,contact:phone,website,contact:website,email,opening_hours,wheelchair,capacity,beds,rooms,addr:full,addr:street,addr:city,addr:postcode,description
other_tags=yes

[lines]
osm_id=yes
attributes=name,name:en,name:ms,amenity,healthcare,social_facility,social_facility:for,school,operator
other_tags=yes

[multipolygons]
osm_id=no
osm_way_id=yes
attributes=name,name:en,name:ms,alt_name,old_name,amenity,healthcare,healthcare:speciality,social_facility,social_facility:for,school,isced:level,operator,operator:type,office,phone,contact:phone,website,contact:website,email,opening_hours,wheelchair,capacity,beds,rooms,addr:full,addr:street,addr:city,addr:postcode,description
other_tags=yes

[multilinestrings]
osm_id=yes
attributes=name
other_tags=yes

[other_relations]
osm_id=yes
attributes=name
other_tags=yes
EOF

export OSM_CONFIG_FILE="$(pwd)/$CONF"

# ---- 3. convert to GeoPackage (points, polygons, lines as separate layers) -
echo ">> Converting to GeoPackage: $OUT"
rm -f "$OUT"
ogr2ogr -f GPKG "$OUT" "$FILTERED" points        -nln care_points   --config OSM_CONFIG_FILE "$OSM_CONFIG_FILE"
ogr2ogr -f GPKG -update "$OUT" "$FILTERED" multipolygons -nln care_polygons --config OSM_CONFIG_FILE "$OSM_CONFIG_FILE"
ogr2ogr -f GPKG -update "$OUT" "$FILTERED" lines  -nln care_lines    --config OSM_CONFIG_FILE "$OSM_CONFIG_FILE" || true

# ---- 4. report -------------------------------------------------------------
echo ">> Done. Feature counts:"
for L in care_points care_polygons care_lines; do
  C=$(ogrinfo -so "$OUT" "$L" 2>/dev/null | awk -F': ' '/Feature Count/{print $2}')
  printf "   %-16s %s\n" "$L" "${C:-0}"
done

echo
echo "Next steps:"
echo "  - care_polygons are building footprints; centroid them to merge with points."
echo "  - Run the NAME-matching pass (tadika, taska, rumah warga emas, OKU, etc.)"
echo "    on the data to catch untagged/mistagged facilities tag-search misses."