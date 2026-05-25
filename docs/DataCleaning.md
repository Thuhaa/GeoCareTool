# Data Cleaning Module

`GeoCareTool.DataCleaning.preprocessing` — **standalone** rule-based cleaner for the output of `GPlaces_Scraping.scraper.fetch_places`. No ML model, no vectorizers, no model files required. Every step is deterministic and inspectable.

## Why a standalone cleaner?

A raw Google Places `nearbysearch` scrape contains many **false positives** — pharmacies, hotels, restaurants and other places picked up because their name or category brushed against your keyword. Before paying for Place Details enrichment (`extra_details`) or running expensive accessibility analyses, the list needs to be filtered.

The previous version of this module relied on a TF-IDF + classifier pipeline with persisted vectorizers and feature-order files. That has been **replaced** with a sequence of small, transparent rules. If you do need ML-based cleaning later, run it on top of these outputs — but most cities don't need it.

## Pipeline

`preprocess(df, ...)` runs these in order:

| Step | Function | What it does |
| ---- | -------- | ------------ |
| 1 | `parse_types` | Coerce the `types` column to a real `list[str]` regardless of how it was serialised (list / `"['a','b']"` / `'a, b'`). |
| 2 | `normalize_name` | Add a `name_clean` column: lowercase, accent-stripped (NFKD), whitespace-collapsed. |
| 3 | `filter_by_types` | Keep only rows whose `types` intersect a whitelist. |
| 4 | `filter_by_name` | Drop rows whose `name` matches a blacklist regex. |
| 5 | `deduplicate` | Keep one row per `place_id` — the row with the fewest NaN values. |

Each step is exported separately so you can compose your own pipeline.

## `preprocess(df, ...)`

Orchestrator. Recommended entry point.

**Parameters**

| Parameter | Default | Meaning |
| --- | --- | --- |
| `df` | — | Raw scrape output. Must include `place_id`, `name`, `types`. |
| `population` | `None` | One of `"children"`, `"disability"`, `"older_adults"`, `"general_care"`. Picks a starter type whitelist from `TYPE_WHITELISTS`. Ignored if `keep_types` is given. |
| `keep_types` | `None` | Custom type whitelist. Overrides `population`. |
| `drop_types` | `None` | Type blacklist applied *after* the whitelist. |
| `name_blacklist` | `DEFAULT_NAME_BLACKLIST` | Regex of name substrings to drop. Pass `None` to disable. |
| `types_column` | `"types"` | Source column name. |
| `name_column` | `"name"` | Source column name. |
| `key_column` | `"place_id"` | Deduplication key. |
| `add_type_dummies` | `False` | If `True`, append one 0/1 column per Google type. |
| `verbose` | `False` | Print row counts after each step. |

**Returns:** a `pandas.DataFrame` with all original columns preserved, plus `name_clean` and (optionally) `type_*` columns. Index is reset.

**Example**

```python
from GeoCareTool.DataCleaning.preprocessing import preprocess

cleaned = preprocess(
    facilities,                 # output of §1
    population="general_care",
    verbose=True,
)
# preprocess(): 6,420 → parse/normalize 6,420 → types-filter 1,180 → name-filter 940 → dedup 812
```

## Type whitelists

`TYPE_WHITELISTS` ships with four populations. **They are starting points** — review your scrape and extend them. `establishment` is deliberately excluded from every whitelist because almost every Google Place has it (it would make the filter a no-op).

```python
TYPE_WHITELISTS = {
    "children":     {"school", "primary_school", "secondary_school", "preschool",
                     "kindergarten", "day_care", "child_care"},
    "disability":   {"physiotherapist", "health", "hospital", "doctor",
                     "physical_therapist", "rehabilitation_center"},
    "older_adults": {"hospital", "doctor", "health", "nursing_home",
                     "elderly_care", "assisted_living_facility"},
    "general_care": {"hospital", "doctor", "health", "clinic", "school",
                     "physiotherapist"},
}
```

## Name blacklist

`DEFAULT_NAME_BLACKLIST` is a bilingual EN/ES regex catching common false positives:

```
\b(pharmacy|farmacia|veterin|vet clinic|gas station|gasolinera|
   restaurant|restaurante|hotel|bank|banco|atm|cajero)\b
```

Disable with `name_blacklist=None`, or extend:

```python
import re
from GeoCareTool.DataCleaning.preprocessing import DEFAULT_NAME_BLACKLIST

my_blacklist = re.compile(
    DEFAULT_NAME_BLACKLIST.pattern + r"|\b(spa|salon|tienda)\b",
    flags=re.IGNORECASE,
)
cleaned = preprocess(facilities, population="children", name_blacklist=my_blacklist)
```

## Individual helpers

### `parse_types(df, column="types")`

Returns a copy of `df` with `column` guaranteed to contain a list of strings. Accepts lists, JSON-ish strings (`"['a','b']"`), and comma-separated strings (`"a, b"`).

### `normalize_name(df, column="name", out_column="name_clean")`

Adds `out_column`. Lowercase, accent-stripped via NFKD decomposition, whitespace collapsed to single spaces.

### `filter_by_types(df, keep_types=None, drop_types=None, population=None)`

Keep rows whose `types` list intersects `keep_types` (or the whitelist for `population`). Drop rows whose `types` list contains any of `drop_types`. The `types` column must already be a list — call `parse_types` first, or use the `preprocess` orchestrator.

### `filter_by_name(df, pattern, column="name", invert=True)`

Drop rows whose `column` matches `pattern`. Set `invert=False` to *keep only* matches instead. Pattern may be a string (compiled case-insensitively) or a precompiled `re.Pattern`. Pass `pattern=None` to no-op.

### `deduplicate(df, key="place_id", prefer_columns=None)`

For each `key`, keep the row with the highest count of non-NaN values across `prefer_columns` (or all columns). Ties broken by first occurrence. Returns rows in original order.

### `extract_types_and_create_dummies(df, types_column="types", prefix="type_", drop_types_column=False)`

Optional one-hot expansion of the types column. Useful for pivot tables that count how many places had each Google type. Not required by anything downstream.

## Recommended workflow

1. Run `preprocess(..., verbose=True)` with a starter `population`. Note the per-step row counts.
2. Save `facilities_kept.csv` and open it in QGIS or a spreadsheet.
3. Spot-check. If false positives remain, extend the name blacklist or tighten `keep_types` and re-run.
4. Once the kept list looks right, proceed to `extra_details.fetch_extra_details` — that's where Place Details API charges start.

## License

This project is licensed under the MIT License.
