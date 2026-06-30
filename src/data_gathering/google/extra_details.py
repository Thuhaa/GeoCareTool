"""Extra-downloads module — Google Places *Place Details* enrichment.

Python port of the legacy R script ``4 - Scrapeo datos extra.R``.

After §1 (Nearby Search) gives us a list of candidate facilities and a manual /
automated cleaning step has filtered down to the place_ids we actually want,
this module fetches **per-place detail fields** that Nearby Search doesn't
return: formatted address, phone, website, and opening hours.

Typical use::

    from src.google.extra_details import (
        estimate_details_cost,
        fetch_extra_details,
        print_details_cost_estimate,
    )

    keep = pd.read_csv("facilities_after_manual_review.csv")

    est = estimate_details_cost(len(keep), include_contact=True, include_hours=True)
    print_details_cost_estimate(est)

    extras = fetch_extra_details(keep["place_id"], key=GOOGLE_API_KEY)
    full   = keep.merge(extras, on="place_id", how="left")
    full.to_csv("thimphu_outputs/scraping/facilities_with_extras.csv", index=False)
"""
from __future__ import annotations

import time
from typing import Iterable, Optional, Sequence

import pandas as pd

try:  # keep import-light so estimate_details_cost works in test envs
    import googlemaps
except ImportError:  # pragma: no cover
    googlemaps = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday")

#: Default field set. These map cleanly to the R script's outputs:
#:   dire  -> formatted_address
#:   tel   -> international_phone_number
#:   web   -> website
#:   horas -> opening_hours.weekday_text
DEFAULT_FIELDS: tuple[str, ...] = (
    "place_id",
    "name",
    "formatted_address",
    "international_phone_number",
    "website",
    "opening_hours",
)

# Pricing — Google Places Place Details, 2026 reference rates.
# Place Details billing stacks SKUs by which fields you request:
#   - Basic Data (place_id, name, address, types, geometry...)  : $17 / 1000
#   - Contact Data (phone, opening_hours, website...)           : +$3 / 1000
#   - Atmosphere Data (rating, reviews, price_level...)         : +$5 / 1000
# The new Places API is field-tiered; verify against your billing console.
COST_BASIC       = 0.017   # USD / request, Basic Data SKU
COST_CONTACT_ADD = 0.003   # USD / request, added when Contact fields requested
COST_ATMOS_ADD   = 0.005   # USD / request, added when Atmosphere fields requested


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate_details_cost(
    n_place_ids: int,
    include_contact: bool = True,
    include_atmosphere: bool = False,
    free_tier: int = 0,
) -> dict:
    """Estimate cost of a Place Details enrichment pass.

    Parameters
    ----------
    n_place_ids : int
        Number of place_ids you intend to enrich. One Details call per id.
    include_contact : bool, default True
        Set ``False`` only if you trim fields down to basic data
        (``place_id``, ``name``, ``formatted_address`` etc. — no phone, no
        website, no opening_hours).
    include_atmosphere : bool, default False
        Set ``True`` if you also request ``rating``, ``reviews``,
        ``price_level``, ``user_ratings_total``, etc.
    free_tier : int, default 0
        Number of free requests to subtract before billing. Google grants
        a Pro-SKU monthly free tier — check your billing console for the
        current Place Details allowance.

    Returns
    -------
    dict
        ``{requests, cost_per_request, cost}``.
    """
    cpr = COST_BASIC
    if include_contact:
        cpr += COST_CONTACT_ADD
    if include_atmosphere:
        cpr += COST_ATMOS_ADD

    billable = max(0, n_place_ids - free_tier)
    return {
        "requests": n_place_ids,
        "billable_requests": billable,
        "cost_per_request": cpr,
        "cost": billable * cpr,
        "include_contact": include_contact,
        "include_atmosphere": include_atmosphere,
    }


def print_details_cost_estimate(est: dict) -> None:
    """Pretty-print a dict from :func:`estimate_details_cost`."""
    skus = ["Basic"]
    if est["include_contact"]:
        skus.append("Contact")
    if est["include_atmosphere"]:
        skus.append("Atmosphere")
    print(
        f"Google Places Place Details — cost estimate\n"
        f"  SKUs:                  {' + '.join(skus)}\n"
        f"  Cost per request:      ${est['cost_per_request']:.4f}\n"
        f"  Requests (billable):   {est['requests']:,} ({est['billable_requests']:,})\n"
        f"  Total cost:            ${est['cost']:,.2f}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_weekday_text(weekday_text: Sequence[str]) -> dict:
    """Turn ``opening_hours.weekday_text`` (a list of 7 strings like
    ``"Monday: 9:00 AM – 5:00 PM"``) into 7 named columns + a one-cell summary
    matching the R script's ``variable_horas`` (days joined by ``" | "``)."""
    by_day: dict[str, str | None] = {f"hours_{d.lower()}": None for d in DAYS}
    for line in weekday_text or []:
        for d in DAYS:
            if line.startswith(d):
                # Keep the part after the first ":" — that's the hours string
                by_day[f"hours_{d.lower()}"] = (
                    line.split(":", 1)[1].strip() if ":" in line else line
                )
                break

    summary = " | ".join(by_day[f"hours_{d.lower()}"] or "" for d in DAYS)
    return {**by_day, "hours_summary": summary}


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

def fetch_extra_details(
    place_ids: Iterable[str],
    key: str,
    fields: Sequence[str] = DEFAULT_FIELDS,
    sleep_seconds: float = 0.2,
    language: Optional[str] = None,
    budget_usd: Optional[float] = None,
    cost_per_request: float = COST_BASIC + COST_CONTACT_ADD,
    verbose: bool = False,
) -> pd.DataFrame:
    """Fetch Place Details for every ``place_id``.

    Mirrors the loop in the R script (``google_place_details`` per row) with
    three improvements:

    1. **Field control** — only request the fields you need. Each extra SKU
       costs money (see :func:`estimate_details_cost`).
    2. **Runtime budget cap** — pass ``budget_usd`` to stop the loop if
       cumulative cost exceeds the cap. Mirrors the Villanueva-style budget
       guard used in §1.
    3. **No data loss on error** — failures append a row with just
       ``place_id`` so you can see what didn't come back.

    Parameters
    ----------
    place_ids : iterable of str
        The ``place_id`` values to enrich.
    key : str
        Google API key.
    fields : sequence of str
        Fields to request. Default = id + name + address + phone + website +
        opening_hours (Basic + Contact SKUs).
    sleep_seconds : float, default 0.2
        Delay between calls. The R script used 8s — that was a safety margin
        against the legacy QPS limits and is wildly conservative; 0.2 is
        plenty for the current (50 QPS) limit.
    language : str, optional
        BCP-47 code (e.g. ``"es"``, ``"dz"``). Affects address formatting and
        the language of ``weekday_text``.
    budget_usd : float, optional
        If set, abort the loop once estimated spend exceeds this.
    cost_per_request : float
        Used only for the runtime budget cap. Defaults to Basic+Contact.
    verbose : bool, default False
        Print one line per skipped place.

    Returns
    -------
    pandas.DataFrame
        One row per ``place_id``. Columns: ``place_id, name, address, phone,
        website, hours_monday … hours_sunday, hours_summary``.
        Missing values are ``None`` / ``NaN``.
    """
    if googlemaps is None:
        raise ImportError(
            "googlemaps is not installed. `pip install googlemaps`."
        )

    gmaps = googlemaps.Client(key=key)
    rows: list[dict] = []
    requests_made = 0

    for pid in place_ids:
        # Runtime budget guard
        if budget_usd is not None and requests_made * cost_per_request >= budget_usd:
            if verbose:
                print(
                    f"[budget] Stopped at {requests_made} requests "
                    f"(~${requests_made * cost_per_request:.2f}); "
                    f"{len(rows)} of inputs processed."
                )
            break

        try:
            time.sleep(sleep_seconds)
            resp = gmaps.place(place_id=pid, fields=list(fields), language=language)
            requests_made += 1
            r = resp.get("result", {}) or {}

            opening = r.get("opening_hours") or {}
            hours_cols = _parse_weekday_text(opening.get("weekday_text", []))

            rows.append({
                "place_id": pid,
                "name":     r.get("name"),
                "address":  r.get("formatted_address"),
                "phone":    r.get("international_phone_number"),
                "website":  r.get("website"),
                **hours_cols,
            })

        except Exception as e:
            if verbose:
                print(f"[skip] {pid}: {e}")
            rows.append({"place_id": pid})

    df = pd.DataFrame(rows)

    # Ensure all expected columns exist even if every row failed
    expected = ["place_id", "name", "address", "phone", "website",
                *(f"hours_{d.lower()}" for d in DAYS), "hours_summary"]
    for col in expected:
        if col not in df.columns:
            df[col] = None
    return df[expected]
