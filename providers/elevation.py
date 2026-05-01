import json
import os
import time

import requests_cache

script_dir = os.path.dirname(os.path.abspath(__file__))
cache_session = requests_cache.CachedSession(os.path.join(script_dir, ".cache"), expire_after=-1)


def _load_locations(locations_source):
    if isinstance(locations_source, str):
        with open(locations_source, "r", encoding="utf-8") as f:
            return json.load(f)
    return list(locations_source)


def _request_elevation(params, max_attempts=5, wait_seconds=5):
    url = "https://api.open-meteo.com/v1/elevation"

    for attempt in range(1, max_attempts + 1):
        response = cache_session.get(url, params=params, timeout=60)
        if response.status_code == 429 and attempt < max_attempts:
            time.sleep(wait_seconds)
            continue

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "elevation" not in payload:
            raise ValueError("Elevation API response missing 'elevation' field.")
        return payload

    raise RuntimeError("Elevation API request failed after all attempts.")


def fetch_batch_elevations(locations_source, batch_size=100):
    locations = _load_locations(locations_source)
    if not locations:
        return []

    elevations = [None] * len(locations)

    for start in range(0, len(locations), batch_size):
        batch = locations[start:start + batch_size]
        latitudes = ",".join(str(float(loc["latitude"])) for loc in batch)
        longitudes = ",".join(str(float(loc["longitude"])) for loc in batch)

        payload = _request_elevation({"latitude": latitudes, "longitude": longitudes})
        batch_elevations = payload.get("elevation", [])

        if len(batch_elevations) != len(batch):
            raise ValueError("Elevation API beklenenden farkli sayida sonuc dondurdu.")

        for offset, elevation in enumerate(batch_elevations):
            elevations[start + offset] = float(elevation) if elevation is not None else None

    return elevations