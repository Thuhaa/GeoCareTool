# overpass_engine.py
#
# Stage 3: configurable Overpass extraction engine for CGT care supply.
# Loops over all categories in a config, runs tag-based and name-based
# searches for each, de-duplicates, and returns clean records carrying
# their match method, raw amenity, source and a geocoding flag.

import requests
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("cgt.overpass")

# Primary endpoint plus mirrors, tried in order if one is busy/unavailable.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

USER_AGENT = "CGT-care-supply-mapping/0.1 (UNDP CARE SOCIETIES pilot)"

# Politeness / robustness settings
REQUEST_TIMEOUT = 120     # seconds to wait for a single response
PAUSE_BETWEEN = 2         # seconds between successive queries
MAX_RETRIES = 3           # attempts per query before giving up
BACKOFF_BASE = 5          # seconds; grows with each retry


# --------------------------------------------------------------------------
# Low-level: run a single Overpass query with retries, backoff and mirrors
# --------------------------------------------------------------------------
def run_query(query: str) -> dict:
    """Execute one Overpass QL query, returning parsed JSON.

    Tries each endpoint with retries and exponential backoff. Raises
    RuntimeError only if every endpoint and retry is exhausted.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        endpoint = OVERPASS_ENDPOINTS[(attempt - 1) % len(OVERPASS_ENDPOINTS)]
        try:
            resp = requests.post(
                endpoint,
                data={"data": query},
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            # 429 = too many requests, 504 = gateway timeout: both retryable
            if resp.status_code in (429, 504):
                raise requests.HTTPError(f"{resp.status_code} from {endpoint}")
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
            wait = BACKOFF_BASE * attempt
            log.warning(f"Attempt {attempt} failed ({e}). Retrying in {wait}s.")
            time.sleep(wait)
    raise RuntimeError(f"All Overpass attempts failed. Last error: {last_error}")


# --------------------------------------------------------------------------
# Query builders
# --------------------------------------------------------------------------
def _bbox_str(bbox) -> str:
    s, w, n, e = bbox
    return f"{s},{w},{n},{e}"


def build_tag_query(bbox, tag_selectors) -> str:
    """Build a query matching ANY of the given (key, value) tag selectors,
    across node/way/relation."""
    box = _bbox_str(bbox)
    lines = []
    for key, value in tag_selectors:
        selector = f'["{key}"="{value}"]' if value is not None else f'["{key}"]'
        for el in ("node", "way", "relation"):
            lines.append(f"  {el}{selector}({box});")
    body = "\n".join(lines)
    return f"[out:json][timeout:90];\n(\n{body}\n);\nout center tags;"


def build_name_query(bbox, name_patterns) -> str:
    """Build a query matching the name against ANY of the regex patterns
    (case-insensitive), across node/way/relation."""
    if not name_patterns:
        return ""
    box = _bbox_str(bbox)
    pattern = "|".join(name_patterns)
    lines = []
    for el in ("node", "way", "relation"):
        lines.append(f'  {el}["name"~"{pattern}",i]({box});')
    body = "\n".join(lines)
    return f"[out:json][timeout:90];\n(\n{body}\n);\nout center tags;"


# --------------------------------------------------------------------------
# Result handling
# --------------------------------------------------------------------------
def get_coord(element: dict):
    """Return (lat, lon) for a node (direct) or way/relation (center)."""
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    if "center" in element:
        return element["center"]["lat"], element["center"]["lon"]
    return None, None


def make_record(element: dict, category: str, match_method: str,
                fields_of_interest: list) -> dict:
    """Turn a raw OSM element into a clean, flat record for the supply DB."""
    tags = element.get("tags", {})
    lat, lon = get_coord(element)

    record = {
        "cgt_category": category,
        "match_method": match_method,          # 'tag' or 'name' (confidence)
        "source": "OSM",
        "osm_type": element["type"],
        "osm_id": element["id"],
        "name": tags.get("name") or tags.get("name:en") or "",
        "latitude": lat,
        "longitude": lon,
        # OSM node/way/relation centroids are precise placements -> good geocode
        "geocode_accuracy": "osm_placement",
        "raw_amenity": tags.get("amenity", ""),  # keep, to expose mistagging
    }
    # Pull through any fields of interest that are present
    for field in fields_of_interest:
        if field in tags:
            record[f"tag_{field}"] = tags[field]
    return record


# --------------------------------------------------------------------------
# Top-level: extract one category, then the whole config
# --------------------------------------------------------------------------
def extract_category(bbox, category_name, category_def, fields_of_interest):
    """Run tag + name searches for one category, de-duplicate, return records.

    De-dup rule: a facility is unique by (osm_type, osm_id). If it matches
    both tag and name, we keep ONE record and mark its method as 'tag'
    (the higher-confidence signal).
    """
    found = {}  # (osm_type, osm_id) -> record

    # 1. Tag-based search
    tag_selectors = category_def.get("tags", [])
    if tag_selectors:
        log.info(f"[{category_name}] tag search ({len(tag_selectors)} selectors)")
        data = run_query(build_tag_query(bbox, tag_selectors))
        for el in data.get("elements", []):
            key = (el["type"], el["id"])
            found[key] = make_record(el, category_name, "tag", fields_of_interest)
        time.sleep(PAUSE_BETWEEN)

    # 2. Name-based search
    name_patterns = category_def.get("name_patterns", [])
    if name_patterns:
        log.info(f"[{category_name}] name search ({len(name_patterns)} patterns)")
        data = run_query(build_name_query(bbox, name_patterns))
        for el in data.get("elements", []):
            key = (el["type"], el["id"])
            if key in found:
                continue  # already found by tag (higher confidence) -> keep that
            found[key] = make_record(el, category_name, "name", fields_of_interest)
        time.sleep(PAUSE_BETWEEN)

    records = list(found.values())
    n_tag = sum(1 for r in records if r["match_method"] == "tag")
    n_name = sum(1 for r in records if r["match_method"] == "name")
    log.info(f"[{category_name}] {len(records)} unique "
             f"({n_tag} by tag, {n_name} by name)")
    return records


def extract_all(config: dict) -> list:
    """Run extraction for every category in the config; return all records."""
    bbox = config["bbox"]
    fields = config.get("fields_of_interest", [])
    all_records = []

    log.info(f"Extracting care supply for {config['pilot_area']}, "
             f"{config['country']}")
    for category_name, category_def in config["categories"].items():
        records = extract_category(bbox, category_name, category_def, fields)
        all_records.extend(records)

    log.info(f"TOTAL: {len(all_records)} care facilities across "
             f"{len(config['categories'])} categories")
    return all_records


# --------------------------------------------------------------------------
# Run directly for a quick check
# --------------------------------------------------------------------------
if __name__ == "__main__":
    from bhutan_config import CONFIG
    #
    # records = extract_all(CONFIG)
    #
    # # Quick summary by category and match method
    # print("\nSummary by category:")
    # from collections import Counter
    # by_cat = Counter(r["cgt_category"] for r in records)
    # for cat, count in by_cat.items():
    #     print(f"  {cat}: {count}")
    #
    # # Show a few sample records
    # print("\nSample records:")
    # import json
    # for r in records[:3]:
    #     print(json.dumps(r, indent=2, ensure_ascii=False))
    from writers import write_outputs

    records = extract_all(CONFIG)

    print(records)
    # paths = write_outputs(records, CONFIG, out_dir="out")