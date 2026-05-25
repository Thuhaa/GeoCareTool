"""Standalone preprocessing for §1 (Nearby Search) scrape output.

No model loading, no vectorizers — just deterministic cleaning steps that
turn the raw concatenated pickle from `scraper.fetch_places` into a tidy
DataFrame ready for §3 enrichment or §4 accessibility.

The pipeline is composed of small, independently-testable steps. Use
:func:`preprocess` to run them all in order, or call the individual helpers.

Steps
-----
1. :func:`parse_types` — turn the ``types`` column (sometimes a list,
   sometimes a JSON-ish string) into a Python ``list[str]``.
2. :func:`normalize_name` — lowercase, strip accents, collapse whitespace.
3. :func:`filter_by_types` — keep rows whose parsed types intersect a
   whitelist (and/or drop rows that match a blacklist).
4. :func:`filter_by_name` — drop rows whose name matches a regex (e.g.
   pharmacies, vets) to remove false positives.
5. :func:`deduplicate` — drop duplicate ``place_id`` keeping the row with
   the most information.
6. :func:`extract_types_and_create_dummies` — *optional* one-hot expansion
   of the ``types`` column for downstream analysis or manual review.

Recommended type whitelists (Google Places ``types``) per population are
exported as :data:`TYPE_WHITELISTS`. They are starting points — every city
needs a manual review.
"""
from __future__ import annotations

import ast
import re
import unicodedata
from typing import Iterable, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Default type whitelists per population
# These are starting points — extend / prune after a manual look at your data.
# ---------------------------------------------------------------------------

#: Google Places ``types`` worth keeping per population. Starting points only —
#: review against your scrape and extend. ``establishment`` is deliberately
#: excluded because almost every place has it (it would make the filter a no-op).
TYPE_WHITELISTS: dict[str, set[str]] = {
    "children": {
        "school", "primary_school", "secondary_school", "preschool",
        "kindergarten", "day_care", "child_care",
    },
    "disability": {
        "physiotherapist", "health", "hospital", "doctor",
        "physical_therapist", "rehabilitation_center",
    },
    "older_adults": {
        "hospital", "doctor", "health", "nursing_home",
        "elderly_care", "assisted_living_facility",
    },
    "general_care": {
        "hospital", "doctor", "health", "clinic", "school",
        "physiotherapist",
    },
}

#: Names that almost always indicate a false positive in a care-facility scrape.
DEFAULT_NAME_BLACKLIST = re.compile(
    r"\b(pharmacy|farmacia|veterin|vet clinic|gas station|gasolinera|"
    r"restaurant|restaurante|hotel|bank|banco|atm|cajero)\b",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Step 1 — parse types
# ---------------------------------------------------------------------------

def parse_types(df: pd.DataFrame, column: str = "types") -> pd.DataFrame:
    """Ensure ``df[column]`` is a list of strings (never NaN, never a raw string)."""
    df = df.copy()

    def _coerce(v):
        if isinstance(v, list):
            return [str(t) for t in v]
        if pd.isna(v):
            return []
        if isinstance(v, str):
            s = v.strip()
            # Try literal_eval for list-like strings: "['school', 'establishment']"
            if s.startswith("[") and s.endswith("]"):
                try:
                    parsed = ast.literal_eval(s)
                    if isinstance(parsed, (list, tuple)):
                        return [str(t) for t in parsed]
                except (ValueError, SyntaxError):
                    pass
            # Fallback: anything quoted in the string
            quoted = re.findall(r'"([^"]+)"', s) or re.findall(r"'([^']+)'", s)
            if quoted:
                return quoted
            # Last resort: comma-split
            return [t.strip() for t in s.split(",") if t.strip()]
        return []

    df[column] = df[column].map(_coerce)
    return df


# ---------------------------------------------------------------------------
# Step 2 — normalize name
# ---------------------------------------------------------------------------

def normalize_name(
    df: pd.DataFrame,
    column: str = "name",
    out_column: str = "name_clean",
) -> pd.DataFrame:
    """Add a lowercase, accent-stripped, whitespace-collapsed copy of ``column``.

    The original column is left untouched. Useful for fuzzy matching and
    regex-based filters that shouldn't care about ``Café`` vs ``cafe``.
    """
    df = df.copy()

    def _norm(s):
        if pd.isna(s):
            return ""
        s = str(s).lower()
        # NFKD: decompose accents into base char + combining mark, then drop marks
        s = "".join(
            ch for ch in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(ch)
        )
        s = re.sub(r"\s+", " ", s).strip()
        return s

    df[out_column] = df[column].map(_norm)
    return df


# ---------------------------------------------------------------------------
# Step 3 — filter by types
# ---------------------------------------------------------------------------

def filter_by_types(
    df: pd.DataFrame,
    keep_types: Optional[Iterable[str]] = None,
    drop_types: Optional[Iterable[str]] = None,
    types_column: str = "types",
    population: Optional[str] = None,
) -> pd.DataFrame:
    """Keep rows whose parsed types intersect ``keep_types``.

    Either pass an explicit ``keep_types`` (recommended once you've reviewed
    your data) or pass ``population`` to use one of :data:`TYPE_WHITELISTS`.

    ``drop_types`` is applied *after* ``keep_types`` and removes any row that
    has a blacklisted type — useful for trimming ``establishment``-only rows.

    The ``types`` column must already be a list (call :func:`parse_types` first).
    """
    if keep_types is None and population is not None:
        if population not in TYPE_WHITELISTS:
            raise ValueError(
                f"Unknown population {population!r}. "
                f"Options: {sorted(TYPE_WHITELISTS)}"
            )
        keep_types = TYPE_WHITELISTS[population]

    if keep_types is None and drop_types is None:
        return df.copy()

    keep_set = set(keep_types) if keep_types else None
    drop_set = set(drop_types) if drop_types else None

    def _ok(types_list):
        if not isinstance(types_list, list):
            return False
        t = set(types_list)
        if keep_set is not None and t.isdisjoint(keep_set):
            return False
        if drop_set is not None and not t.isdisjoint(drop_set):
            return False
        return True

    mask = df[types_column].map(_ok)
    return df.loc[mask].copy()


# ---------------------------------------------------------------------------
# Step 4 — filter by name
# ---------------------------------------------------------------------------

def filter_by_name(
    df: pd.DataFrame,
    pattern: Optional[re.Pattern | str] = DEFAULT_NAME_BLACKLIST,
    column: str = "name",
    invert: bool = True,
) -> pd.DataFrame:
    """Drop rows whose ``column`` matches ``pattern`` (default: a blacklist).

    Set ``invert=False`` to *keep only* rows matching the pattern instead.
    Set ``pattern=None`` to no-op.
    """
    if pattern is None:
        return df.copy()
    if isinstance(pattern, str):
        pattern = re.compile(pattern, flags=re.IGNORECASE)

    mask = df[column].fillna("").map(lambda s: bool(pattern.search(str(s))))
    if invert:
        mask = ~mask
    return df.loc[mask].copy()


# ---------------------------------------------------------------------------
# Step 5 — deduplicate
# ---------------------------------------------------------------------------

def deduplicate(
    df: pd.DataFrame,
    key: str = "place_id",
    prefer_columns: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Drop duplicate rows by ``key``, keeping the most-informative copy.

    "Most informative" = the row with the fewest NaN values across
    ``prefer_columns`` (or all columns if not specified). Ties broken by
    first occurrence.
    """
    if key not in df.columns:
        return df.copy()

    score_cols = list(prefer_columns) if prefer_columns else list(df.columns)
    score_cols = [c for c in score_cols if c in df.columns]

    df = df.copy()
    df["_completeness"] = df[score_cols].notna().sum(axis=1)
    df = (
        df.sort_values("_completeness", ascending=False, kind="stable")
          .drop_duplicates(subset=[key], keep="first")
          .drop(columns="_completeness")
          .sort_index()
    )
    return df


# ---------------------------------------------------------------------------
# Optional — type dummies (kept from old version, no model dependency)
# ---------------------------------------------------------------------------

def extract_types_and_create_dummies(
    df: pd.DataFrame,
    types_column: str = "types",
    prefix: str = "type_",
    drop_types_column: bool = False,
) -> pd.DataFrame:
    """One-hot expand the ``types`` column.

    ``types_column`` may be a list (preferred — call :func:`parse_types` first)
    or a string of comma-separated / quoted types — this function will coerce.
    Useful for quick pivot tables of which Google ``types`` your scrape hit.
    """
    df = parse_types(df, column=types_column)

    exploded = df[types_column].explode()
    dummies = pd.get_dummies(exploded, prefix=prefix.rstrip("_"), prefix_sep="_")
    dummies = dummies.groupby(level=0).sum()

    out = pd.concat([df, dummies], axis=1)
    if drop_types_column:
        out = out.drop(columns=[types_column])
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def preprocess(
    df: pd.DataFrame,
    population: Optional[str] = None,
    keep_types: Optional[Iterable[str]] = None,
    drop_types: Optional[Iterable[str]] = None,
    name_blacklist: Optional[re.Pattern | str] = DEFAULT_NAME_BLACKLIST,
    types_column: str = "types",
    name_column: str = "name",
    key_column: str = "place_id",
    add_type_dummies: bool = False,
    verbose: bool = False,
) -> pd.DataFrame:
    """Run the full standalone preprocessing pipeline.

    Parameters
    ----------
    df : DataFrame
        Raw output of :func:`scraper.fetch_places` (concatenated across
        keywords / grid cells). Must include ``place_id``, ``name``, ``types``,
        ``lat``, ``lng``.
    population : {'children', 'disability', 'older_adults', 'general_care'}, optional
        Picks a default type whitelist from :data:`TYPE_WHITELISTS`. Ignored if
        ``keep_types`` is given explicitly.
    keep_types, drop_types : iterable of str, optional
        Custom whitelist / blacklist of Google Places ``types`` strings.
    name_blacklist : regex or str, optional
        Drop rows whose ``name`` matches this. Default trims common false
        positives (pharmacies, vets, restaurants...). Pass ``None`` to disable.
    add_type_dummies : bool, default False
        If True, append one column per Google ``type`` as 0/1 indicator.
    verbose : bool, default False
        Print row-count diagnostics after each step.

    Returns
    -------
    pandas.DataFrame
        Cleaned dataset, original columns preserved, plus ``name_clean`` and
        (optionally) ``type_*`` indicator columns.
    """
    n0 = len(df)
    df = parse_types(df, column=types_column)
    df = normalize_name(df, column=name_column)
    n1 = len(df)

    df = filter_by_types(
        df,
        keep_types=keep_types,
        drop_types=drop_types,
        types_column=types_column,
        population=population if keep_types is None else None,
    )
    n2 = len(df)

    df = filter_by_name(df, pattern=name_blacklist, column=name_column)
    n3 = len(df)

    df = deduplicate(df, key=key_column)
    n4 = len(df)

    if add_type_dummies:
        df = extract_types_and_create_dummies(df, types_column=types_column)

    if verbose:
        print(
            f"preprocess(): {n0:,} → parse/normalize {n1:,} → "
            f"types-filter {n2:,} → name-filter {n3:,} → dedup {n4:,}"
        )

    return df.reset_index(drop=True)
