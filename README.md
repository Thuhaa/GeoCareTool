# GeoCareTool

GeoCareTool is a Python library developed by the **UNDP LAC Gender Team** as part of the *Care Georeferencing Tool* methodology. It helps researchers and policymakers locate care facilities in a city, evaluate accessibility on foot and by public transport, and identify **care deserts** — areas where vulnerable populations lack reasonable access to the care they need.

## Pipeline overview

```
   ┌────────────┐   ┌───────────┐   ┌─────────────┐   ┌─────────────────┐   ┌──────────────┐
   │  Scraping  │ → │  Cleaning │ → │   Extras    │ → │  Accessibility  │ → │ Care deserts │
   │ (Places)   │   │ (rules)   │   │ (Details)   │   │ (walk + PT)     │   │ (by group)   │
   └────────────┘   └───────────┘   └─────────────┘   └─────────────────┘   └──────────────┘
        §1               §2              §3                  §4                    §5
```

Each step is its own module that can be called on its own. The end-to-end demo is `thimphu_bhutan.ipynb`.

## Modules

| Module | Purpose |
| ------ | ------- |
| **`GPlaces_Scraping.scraper`** | Grid-based Google Places **Nearby Search** scrape with a pre-run **cost estimator** (`estimate_cost`). |
| **`GPlaces_Scraping.extra_details`** | Per-`place_id` **Place Details** enrichment (address, phone, website, opening hours) with cost estimator and runtime budget cap. |
| **`DataCleaning.preprocessing`** | Standalone rule-based cleaner — type whitelists, name-blacklist regex, deduplication. **No ML model required.** |
| **`Accessibility.Walking`** | Walking isochrones (concave-hull) on the OSM pedestrian network. |
| **`Accessibility.PublicTransport`** | Synthetic GTFS builder + `r5py` travel-time matrix. |
| **`CareDeserts`** | Walking-only and combined walking + PT desert classification per demographic group. |

## Installation

```bash
pip install git+https://github.com/Thuhaa/GeoCareTool.git
```

You'll also need a Google Cloud project with the **Places API** enabled and an API key. Either put it in a `.env` file next to your notebook:

```
GOOGLE_API_KEY=AIza...
```

or export it in your shell:

```bash
export GOOGLE_API_KEY="AIza..."
```

## Quick start

```python
from GeoCareTool.google.scraper import (
    generate_grid, fetch_places, estimate_cost, print_cost_estimate,
)
from GeoCareTool.DataCleaning.preprocessing import preprocess
from GeoCareTool.google.extra_details import fetch_extra_details

# §1 — Grid + cost estimate + scrape
grid = generate_grid(min_lat, max_lat, min_lng, max_lng, step=1500)
print_cost_estimate(estimate_cost(len(grid), keywords=KEYWORDS, pagination_factor=1.5))
# ... fetch_places loop ...

# §2 — Clean
cleaned = preprocess(facilities, population="general_care", verbose=True)

# §3 — Enrich
extras = fetch_extra_details(cleaned["place_id"], key=GOOGLE_API_KEY, budget_usd=25.0)
```

See `thimphu_bhutan.ipynb` for the full pipeline including accessibility and care-desert classification.

## Cost transparency

Every module that hits the Google Places API has a **pre-run cost estimator** so you know what a run will cost before pressing play, plus a **runtime budget cap** that stops the loop if cumulative spend exceeds a limit. Current rates (2026, base tier):

| API call | Cost / 1,000 |
| --- | --- |
| Nearby Search (Basic) | $32 |
| Place Details (Basic) | $17 |
| Place Details (+ Contact) | $20 |
| Place Details (+ Atmosphere) | $25 |

Plus 5,000 free events / SKU / month and a $200 / month Maps Platform credit.

## Documentation

See [`docs/`](docs/):

- [`GPlaces_Scraping.md`](docs/GPlaces_Scraping.md) — Nearby Search scrape + Place Details enrichment (with cost estimators).
- [`DataCleaning.md`](docs/DataCleaning.md) — the standalone preprocessing module.
- [`Accessibility.md`](docs/Accessibility.md) — walking isochrones, synthetic GTFS, and `r5py` travel-time matrices.
- [`CareDeserts.md`](docs/CareDeserts.md) — walking-only and combined walking + PT desert classification.
- [`DemandMapping.md`](docs/DemandMapping.md) — *placeholder, not yet implemented.*

## License

This project is licensed under the MIT License.
