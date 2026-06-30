# writers.py
#
# Stage 4: turn clean extraction records into merge-ready output files.
# Writes GeoJSON (for QGIS / PostGIS / spatial tools) and CSV (for review),
# both as UTF-8 so Dzongkha and accented names survive intact.

#
# The input is the list of records produced by overpass_engine.extract_all().
# Each record is already flat; Stage 4 standardises the schema and geometry.

import csv
import json
import logging

log = logging.getLogger("cgt.writers")

# Fixed core columns, in the order they should appear. Every output row has
# these, in this order, regardless of which optional tags were present.
CORE_COLUMNS = [
    "facility_id",       # stable id: "OSM:node:12345"
    "name",
    "cgt_category",
    "latitude",
    "longitude",
    "source",            # "OSM"
    "match_method",      # "tag" (higher confidence) or "name" (review these)
    "geocode_accuracy",  # "osm_placement"
    "osm_type",
    "osm_id",
    "raw_amenity",       # exposes mistagging (e.g. an ECCD tagged amenity=school)
]


def facility_id(record: dict) -> str:
    """Stable, source-prefixed identifier, useful for later de-dup and merge."""
    return f"{record['source']}:{record['osm_type']}:{record['osm_id']}"


def ordered_columns(records: list, fields_of_interest: list) -> list:
    """Build a deterministic column order: core columns, then the configured
    tag_ fields, then any extra tag_ keys that turned up but were not listed.

    Deterministic columns matter so every export has the same shape and merges
    cleanly with administrative and field-survey data later.
    """
    configured = [f"tag_{f}" for f in fields_of_interest]

    # Catch any tag_ keys present in the data but not in fields_of_interest,
    # so nothing is silently dropped.
    seen_extra = []
    for r in records:
        for key in r:
            if key.startswith("tag_") and key not in configured and key not in seen_extra:
                seen_extra.append(key)

    return CORE_COLUMNS + configured + seen_extra


def to_row(record: dict, columns: list) -> dict:
    """Flatten one record to a full row with every column present.
    Missing values become empty strings so the CSV is rectangular."""
    row = {col: "" for col in columns}
    row["facility_id"] = facility_id(record)
    for key, value in record.items():
        if key in row:
            row[key] = value if value is not None else ""
    return row


# --------------------------------------------------------------------------
# GeoJSON
# --------------------------------------------------------------------------
def to_feature(record: dict):
    """Convert one record to a GeoJSON Point feature, or None if it lacks
    coordinates (which should not happen, but we guard against it)."""
    lat, lon = record.get("latitude"), record.get("longitude")
    if lat is None or lon is None:
        log.warning(f"Skipping record with no coordinates: "
                    f"{record.get('name') or record.get('osm_id')}")
        return None

    # GeoJSON geometry is [longitude, latitude] (x, y), not lat/lon.
    properties = {"facility_id": facility_id(record)}
    for key, value in record.items():
        if key not in ("latitude", "longitude"):
            properties[key] = value

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties,
    }


def write_geojson(records: list, path: str):
    """Write records to a UTF-8 GeoJSON FeatureCollection."""
    features = [f for f in (to_feature(r) for r in records) if f is not None]
    collection = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(collection, fh, ensure_ascii=False, indent=2)
    log.info(f"Wrote {len(features)} features to {path}")
    return len(features)


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def write_csv(records: list, path: str, fields_of_interest: list,
              excel_friendly: bool = False):
    """Write records to a UTF-8 CSV with a deterministic column order.

    excel_friendly=True writes a UTF-8 BOM (utf-8-sig) so Microsoft Excel
    opens Dzongkha and accented characters correctly. Leave False for
    pandas/GeoPandas/PostGIS ingestion.
    """
    columns = ordered_columns(records, fields_of_interest)
    encoding = "utf-8-sig" if excel_friendly else "utf-8"
    with open(path, "w", encoding=encoding, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for record in records:
            writer.writerow(to_row(record, columns))
    log.info(f"Wrote {len(records)} rows to {path}")
    return len(records)


# --------------------------------------------------------------------------
# Convenience: write both, named by country/area
# --------------------------------------------------------------------------
def write_outputs(records: list, config: dict, out_dir: str = "."):
    """Write both GeoJSON and CSV, named from the config, and return paths."""
    import os
    os.makedirs(out_dir, exist_ok=True)

    area = config.get("pilot_area", "area").lower().replace(" ", "_")
    fields = config.get("fields_of_interest", [])

    geojson_path = os.path.join(out_dir, f"care_supply_osm_{area}.geojson")
    csv_path = os.path.join(out_dir, f"care_supply_osm_{area}.csv")

    write_geojson(records, geojson_path)
    write_csv(records, csv_path, fields, excel_friendly=True)

    return {"geojson": geojson_path, "csv": csv_path}