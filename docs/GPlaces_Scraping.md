# Google Places Scraping Module

`GeoCareTool.GPlaces_Scraping` — two sub-modules covering the two Google Places endpoints we use:

| Sub-module | Endpoint | What it gives you |
| ---------- | -------- | ----------------- |
| `scraper` | **Nearby Search** | A grid-based inventory of places matching one or more keywords across a bounding box. |
| `extra_details` | **Place Details** | Per-`place_id` enrichment with address, phone, website, opening hours. |

Both have **pre-run cost estimators** and (in the case of `extra_details`) a **runtime budget cap** so you can never overspend by accident.

---

## `scraper` — Nearby Search

### `generate_grid(min_lat, max_lat, min_lng, max_lng, step)`

Returns a list of `(lat, lng)` tuples tiling the bounding box at `step`-metre spacing. Converts the metric step to degrees using the Earth's radius and the latitude-dependent longitude scaling, so cells stay roughly square in metric space.

```python
grid = generate_grid(27.40, 27.55, 89.58, 89.72, step=1500)
# 1.5 km spacing → ~120 points covering central Thimphu
```

### `plot_grid_with_basemap(city_name, grid, bounding_box)`

Sanity-check plot — grid points over a contextily basemap. Use it to confirm your bounding box actually covers the city.

### `fetch_places(lat, lng, keyword, key, step_size=1000)`

Thin wrapper around `googlemaps.Client.places_nearby`. For one grid point and one keyword:

- Sets `radius = step_size` (metres) around `(lat, lng)`.
- Filters by `keyword` (free-text match against name + types).
- Follows `next_page_token` up to 2 more times (Google paginates 20 results × 3 pages = 60 max). Includes `time.sleep(2)` between pages.
- Returns a `pd.DataFrame` of `place_id, name, lat, lng, types, ...`.
- On any exception, returns an empty DataFrame so an outer loop can continue.

### `estimate_cost(num_grid_points, keywords, cost_per_request=0.032, pagination_factor=1.0, free_tier=0)`

Pre-run cost estimate. Returns a dict with `requests_min / requests_expected / requests_max` and matching `cost_*` figures.

**Parameters**

| Parameter | Default | Meaning |
| --- | --- | --- |
| `num_grid_points` | — | `len(generate_grid(...))`. |
| `keywords` | — | `int`, `list`, or `dict` of `category -> [keywords]`. Total is computed for you. |
| `cost_per_request` | `0.032` | USD/request. As of 2026, Google charges $32/1000 for base-tier Nearby Search returning Basic Data. |
| `pagination_factor` | `1.0` | Expected average requests per (grid × keyword). Sparse: 1.0, dense urban: 1.5–2.0, worst case: 3.0. |
| `free_tier` | `0` | Subtract before billing. Set to your monthly free allowance if any. |

**Example**

```python
from GeoCareTool.GPlaces_Scraping.scraper import estimate_cost, print_cost_estimate

est = estimate_cost(
    num_grid_points=len(grid),
    keywords=KEYWORDS,            # dict-of-lists is accepted
    cost_per_request=0.032,
    pagination_factor=1.5,
    free_tier=5000,
)
print_cost_estimate(est)
# Google Places Nearby Search — cost estimate (@ $0.0320/request)
#   Grid points x keywords:   120 x 13
#   Requests (min/exp/max):   1,560 / 2,340 / 4,680
#   Cost USD (min/exp/max):   $0.00 / $0.00 / $0.00     ← under the free tier

# Defensive: assert worst case is within your budget
assert est["cost_max"] <= 50, "Reduce grid or keywords."
```

### `load_and_concatenate_files(file_pattern)`

Globs `*.pkl` files matching the pattern and concatenates them into one DataFrame. Used after the scrape loop to merge per-category outputs.

---

## `extra_details` — Place Details

### `fetch_extra_details(place_ids, key, ...)`

For each `place_id`, calls `gmaps.place(place_id, fields=[...])` and returns a DataFrame with:

- `place_id`, `name`, `address`, `phone`, `website`
- `hours_monday` … `hours_sunday` (one column per day, free-text like `"9:00 AM – 5:00 PM"` or `"Closed"`)
- `hours_summary` — all seven days joined by `" | "` (matches the legacy R script's `variable_horas` shape)

**Parameters**

| Parameter | Default | Meaning |
| --- | --- | --- |
| `place_ids` | — | Iterable of `place_id` strings. |
| `key` | — | Google API key. |
| `fields` | `DEFAULT_FIELDS` | Which fields to request. Affects cost (see SKU table below). |
| `sleep_seconds` | `0.2` | Delay between calls. Current Google QPS allows this. |
| `language` | `None` | BCP-47 code, e.g. `"es"`, `"dz"`. Affects address formatting and `weekday_text` language. |
| `budget_usd` | `None` | Runtime cap — loop stops once estimated spend exceeds this. |
| `cost_per_request` | `$0.020` | Used by the runtime cap. Defaults to Basic + Contact SKUs. |
| `verbose` | `False` | Print skipped places and budget-stop reason. |

**Error handling:** failed requests append a row with just `place_id` so nothing silently disappears from the output schema.

### `estimate_details_cost(n_place_ids, include_contact=True, include_atmosphere=False, free_tier=0)`

Pre-run cost estimate. Returns a dict with `requests`, `billable_requests`, `cost_per_request`, `cost`, and which SKUs were assumed.

**Place Details SKU pricing (2026)**

| SKU | What it includes | Rate / 1000 |
| --- | --- | --- |
| **Basic Data** | `place_id`, `name`, `formatted_address`, `geometry`, `types` | $17 |
| **+ Contact Data** | `international_phone_number`, `opening_hours`, `website` | +$3 |
| **+ Atmosphere Data** | `rating`, `user_ratings_total`, `price_level`, `reviews` | +$5 |

Default fetch (Basic + Contact) = **$0.020/request**.

**Example**

```python
from GeoCareTool.GPlaces_Scraping.extra_details import (
    fetch_extra_details, estimate_details_cost, print_details_cost_estimate,
)

est = estimate_details_cost(n_place_ids=len(kept), include_contact=True)
print_details_cost_estimate(est)
# Google Places Place Details — cost estimate
#   SKUs:                  Basic + Contact
#   Cost per request:      $0.0200
#   Requests (billable):   250 (250)
#   Total cost:            $5.00

assert est["cost"] <= 25, "Reduce kept list or raise cap."

extras = fetch_extra_details(
    place_ids=kept["place_id"],
    key=GOOGLE_API_KEY,
    language="en",
    budget_usd=25.0,
    verbose=True,
)
```

### Constants

- `DEFAULT_FIELDS` — the seven fields requested by default (Basic + Contact).
- `DAYS` — the seven `weekday_text` day labels (English; pass `language=` to fetch in another locale and the order is preserved).
- `COST_BASIC`, `COST_CONTACT_ADD`, `COST_ATMOS_ADD` — the per-request rates used by the estimator. Update if Google changes pricing.

---

## Workflow

```
generate_grid ──┐
                ├──► fetch_places (per cell × keyword) ──► raw DataFrame
estimate_cost ──┘                                              │
                                                               ▼
                                              DataCleaning.preprocess
                                                               │
                                                               ▼
                                              fetch_extra_details (Place Details)
                                                               │
                                                               ▼
                                              merge → enriched DataFrame
```

See `thimphu_bhutan.ipynb` for the full pipeline.

## License

This project is licensed under the MIT License.
