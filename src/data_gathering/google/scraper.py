import pandas as pd
import googlemaps
import time
import unidecode
from googlemaps import places
from geopy.geocoders import Nominatim
import math
import numpy as np
import matplotlib.pyplot as plt
from geopy.distance import geodesic
import contextily as ctx
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
import glob
from urllib3 import Retry


# Function to generate a grid of coordinates within the bounding box
def generate_grid(min_lat, max_lat, min_lng, max_lng, step):
    r_earth = 6371000  # Radius of Earth in meters
    lat_step = step / r_earth * (180 / math.pi)
    lng_step = step / (r_earth * math.cos(math.pi * (min_lat + max_lat) / 2 / 180)) * (180 / math.pi)
    
    lat_range = np.arange(min_lat, max_lat, lat_step)
    lng_range = np.arange(min_lng, max_lng, lng_step)
    
    grid = [(lat, lng) for lat in lat_range for lng in lng_range]
    return grid



def plot_grid_with_basemap(city_name, grid_coordinates, bounding_box):
    """Plot the bounding box and grid points with a basemap."""
    min_lat, max_lat, min_lng, max_lng = bounding_box
    
    # Plot the bounding box and grid points
    fig, ax = plt.subplots()

    # Bounding box coordinates
    bbox_coords = [(min_lat, min_lng), (min_lat, max_lng), (max_lat, max_lng), (max_lat, min_lng), (min_lat, min_lng)]
    bbox_lats, bbox_lngs = zip(*bbox_coords)

    # Plot bounding box
    ax.plot(bbox_lngs, bbox_lats, 'r-', label='Bounding Box')

    # Plot grid points
    grid_lats, grid_lngs = zip(*grid_coordinates)
    ax.plot(grid_lngs, grid_lats, 'bo', label='Grid Points', markersize=2)

    # Add basemap
    ctx.add_basemap(ax, crs='EPSG:4326', source=ctx.providers.OpenStreetMap.Mapnik)

    # Labels and legend
    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')
    ax.set_title(f'Grid for {city_name}')
    ax.legend()

    plt.show()




# Function to process Google Places API results
def base_chica(results):
    data = []
    for result in results:
        entry = {
            'name': result.get('name'),
            'place_id': result.get('place_id'),
            'business_status': result.get('business_status'),
            'types': ','.join(result.get('types', [])),
            'lat': result['geometry']['location']['lat'],
            'lng': result['geometry']['location']['lng']
        }
        data.append(entry)
    return pd.DataFrame(data)

# Function to make API requests with retries
def fetch_places(lat, lng, keyword, key, step_size=1000):
    gmaps = googlemaps.Client(key=key)
    # TODO: Call Session higher up to avoid creating a new one for each request
    session = requests.Session()
    retry = Retry(total=5, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    
    try:
        response = gmaps.places_nearby(location=(lat, lng), radius=step_size, keyword=keyword)
        results = response.get('results', [])
        db = base_chica(results)
        
        # Check if there is a next page
        while 'next_page_token' in response:
            time.sleep(2)  # Shorter sleep time but keep within limits
            response = gmaps.places_nearby(location=(lat, lng), radius=step_size, keyword=keyword, page_token=response['next_page_token'])
            results = response.get('results', [])
            db = pd.concat([db, base_chica(results)], ignore_index=True)
        
        return db
    
    except Exception as e:
        print(f"Error processing coordinates ({lat}, {lng}): {e}")
        return pd.DataFrame()


def estimate_cost(
    num_grid_points,
    keywords,
    cost_per_request=0.032,
    pagination_factor=1.0,
    free_tier=0,
):
    """Estimate cost of a Google Places nearbysearch scrape before running it.

    Parameters
    ----------
    num_grid_points : int
        Number of grid points (e.g. ``len(generate_grid(...))``).
    keywords : int | list | dict
        Keywords to scrape. If a list/tuple/set is passed, its length is used.
        If a dict-of-lists (category -> [keywords]) is passed, the total across
        all categories is computed automatically.
    cost_per_request : float, default 0.032
        USD per Nearby Search request. As of 2026 Google charges $32/1000
        ($0.032/request) for base-tier Nearby Search returning Basic Data,
        which is what ``fetch_places`` uses. The rate drops to $0.0256 above
        100k requests/month on the legacy API, and the new Places API tiers
        further down at higher volume ($0.0192 / $0.0096 / $0.0024).
    pagination_factor : float, default 1.0
        Expected average number of API requests per (grid point x keyword).
        ``fetch_places`` follows ``next_page_token`` up to 2 extra times when
        an area has many results (60 max), so the worst case is 3.0. Use 1.0
        for sparse areas, ~1.5-2.0 for dense urban areas, 3.0 for worst case.
    free_tier : int, default 0
        Number of free requests to subtract before billing. Google currently
        grants 5,000 free Nearby Search events/month at the Pro tier.

    Returns
    -------
    dict
        Keys: ``n_grid_points``, ``n_keywords``, ``requests_min``,
        ``requests_expected``, ``requests_max``, ``cost_min``,
        ``cost_expected``, ``cost_max``, ``cost_per_request``.
        Min assumes 1 request per cell, max assumes 3 (full pagination).
    """
    if isinstance(keywords, dict):
        n_keywords = sum(len(v) for v in keywords.values())
    elif isinstance(keywords, (list, tuple, set)):
        n_keywords = len(keywords)
    else:
        n_keywords = int(keywords)

    base = num_grid_points * n_keywords
    req_min = base
    req_exp = int(round(base * pagination_factor))
    req_max = base * 3

    def _cost(n):
        return max(0, n - free_tier) * cost_per_request

    return {
        "n_grid_points": num_grid_points,
        "n_keywords": n_keywords,
        "requests_min": req_min,
        "requests_expected": req_exp,
        "requests_max": req_max,
        "cost_min": _cost(req_min),
        "cost_expected": _cost(req_exp),
        "cost_max": _cost(req_max),
        "cost_per_request": cost_per_request,
    }


def print_cost_estimate(estimate):
    """Pretty-print a dict returned by :func:`estimate_cost`."""
    print(
        f"Google Places Nearby Search — cost estimate "
        f"(@ ${estimate['cost_per_request']:.4f}/request)\n"
        f"  Grid points x keywords:   "
        f"{estimate['n_grid_points']} x {estimate['n_keywords']}\n"
        f"  Requests (min/exp/max):   "
        f"{estimate['requests_min']:,} / "
        f"{estimate['requests_expected']:,} / "
        f"{estimate['requests_max']:,}\n"
        f"  Cost USD (min/exp/max):   "
        f"${estimate['cost_min']:,.2f} / "
        f"${estimate['cost_expected']:,.2f} / "
        f"${estimate['cost_max']:,.2f}"
    )


def load_and_concatenate_files(file_pattern):
    """Load all files matching the given pattern and concatenate them into a single DataFrame."""
    all_files = glob.glob(file_pattern)
    dataframes = []
    
    for file in all_files:
        df = pd.read_pickle(file)
        dataframes.append(df)
    
    combined_df = pd.concat(dataframes, ignore_index=True)
    df = combined_df.drop_duplicates()
    return df

if __name__ == "__main__":
    pass