#!/usr/bin/env python3
"""Test script: fetch SoilGrids features for a single location.

Usage: python3 scripts/test_soilgrids_single_location.py [lat lon]

If SoilGrids returns no data, the script will probe nearby offsets.
"""
import sys
import math
from pprint import pprint

try:
    from providers import soilgrids
except Exception:
    try:
        import providers.soilgrids as soilgrids
    except Exception:
        soilgrids = None


def fetch_for_coord(lat, lon):
    if soilgrids is None:
        print("openmeteo.soilgrids module not available")
        return None
    try:
        res = soilgrids.fetch_batch_soil_features([{"latitude": lat, "longitude": lon}])
        if not res:
            return None
        return res[0]
    except Exception as e:
        print("fetch error:", e)
        return None


def probe_nearby(lat, lon, max_radius_km=20, steps=(0.01, 0.02, 0.05, 0.1)):
    # steps approximate degrees; try combinations of lat/lon offsets
    for step in steps:
        # try ring around origin
        deltas = [(-step, 0), (step, 0), (0, -step), (0, step), (-step, -step), (step, step)]
        for dlat, dlon in deltas:
            nlat = lat + dlat
            nlon = lon + dlon
            print(f"Probing nearby {nlat:.6f},{nlon:.6f} (offset {dlat},{dlon})")
            f = fetch_for_coord(nlat, nlon)
            if f is not None:
                return (nlat, nlon, f)
    return None


def main():
    if len(sys.argv) >= 3:
        lat = float(sys.argv[1])
        lon = float(sys.argv[2])
    else:
        lat = 36.5
        lon = 27.0

    print("Testing SoilGrids fetch for:", lat, lon)
    feat = fetch_for_coord(lat, lon)
    if feat is None:
        print("No SoilGrids features at exact location. Trying nearby probes...")
        nearby = probe_nearby(lat, lon)
        if nearby is None:
            print("No SoilGrids data found nearby. Returning None.")
            return
        else:
            nlat, nlon, f = nearby
            print(f"Found SoilGrids features at nearby location {nlat},{nlon}:")
            pprint(f)
            return

    print("SoilGrids features at exact location:")
    pprint(feat)


if __name__ == "__main__":
    main()
