#!/usr/bin/env bash
# Step 2a: extract ALL named nodes/ways as candidates for the name-matching pass.
# The tag filter (extract_care_osm.sh) cannot see untagged/name-only facilities,
# so we pull every named feature here and let the Python pass regex the names.
set -euo pipefail
INPUT="${1:-malaysia-singapore-brunei-latest.osm.pbf}"

echo ">> Extracting all named features (candidates for name matching)..."
osmium tags-filter "$INPUT" n/name w/name --overwrite -o named_all.osm.pbf

echo ">> Converting candidates to GeoPackage..."
rm -f all_named.gpkg
ogr2ogr -f GPKG all_named.gpkg named_all.osm.pbf points        -nln named_points
ogr2ogr -f GPKG -update all_named.gpkg named_all.osm.pbf multipolygons -nln named_polys

echo ">> Done -> all_named.gpkg (named_points, named_polys)"