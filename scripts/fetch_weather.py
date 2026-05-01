"""
Pre-fetches all daily and hourly weather data for all locations and years.

Run this before simulation/simulation.py to ensure all weather data is on disk.
The simulation will then run without any API calls.

Usage:
    python scripts/fetch_weather.py

Resume behaviour:
    Already-downloaded files are skipped automatically. Safe to interrupt and restart.

Daily API limit:
    SoilGrids is queried during location building (~71 requests, ~13s apart).
    OpenMeteo weather fetch is fast but also rate-limited per minute.
    If the daily limit is hit, restart tomorrow — completed files are preserved.
"""

import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from simulation.simulation import build_locations
from providers.weather_daily import fetch_and_save_pcse_weather
from providers.weather_hourly import fetch_hourly_sensor_data

YEARS = list(range(2014, 2027))  # 2026 included for winter crops sown in 2025

def main():
    print("Building locations (SoilGrids + elevation)...")
    locations = build_locations()
    print(f"{len(locations)} locations ready.\n")

    total = len(YEARS)
    for i, year in enumerate(YEARS, 1):
        print(f"[{i}/{total}] Year {year}")

        print(f"  Fetching daily weather...")
        fetch_and_save_pcse_weather(locations, f"{year}-01-01", f"{year}-12-31")

        print(f"  Fetching hourly weather...")
        fetch_hourly_sensor_data(locations, f"{year}-01-01", f"{year}-12-31")

        print(f"  Year {year} done.\n")

    print("All weather data fetched. You can now run simulation/simulation.py.")

if __name__ == "__main__":
    main()
