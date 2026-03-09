"""
Step 1: Fetch historical weather from Open-Meteo for Menemen, Izmir
and save it as a PCSE-compatible CSV file.

Open-Meteo is free, requires no API key, and is based on ERA5 reanalysis.
Run this ONCE to generate menemen_weather.csv, then run the main simulation.

Requirements:
    pip install requests pandas
"""

import requests
import pandas as pd
import math
from datetime import date

# ── Location ─────────────────────────────────────────────────
LAT = 38.6087
LON = 27.0698
ELEVATION = 25          # Menemen plain ~25 m above sea level
START = "2022-01-01"
END   = "2023-12-31"
OUTPUT_FILE = "menemen_weather.csv"

# ── Open-Meteo ERA5 historical API ───────────────────────────
print(f"Fetching ERA5 weather for Menemen ({LAT}, {LON}) from {START} to {END}...")

url = "https://archive-api.open-meteo.com/v1/archive"
params = {
    "latitude": LAT,
    "longitude": LON,
    "start_date": START,
    "end_date": END,
    "daily": [
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "windspeed_10m_max",
        "shortwave_radiation_sum",   # MJ/m2/day
        "et0_fao_evapotranspiration",
        "dewpoint_2m_mean",          # not directly available; use vapour pressure approach
    ],
    "wind_speed_unit": "ms",
    "timezone": "Europe/Istanbul"
}

# Note: Open-Meteo doesn't expose dewpoint directly in archive,
# use relative_humidity_2m_mean to compute vapour pressure
params["daily"] = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "windspeed_10m_max",
    "shortwave_radiation_sum",
    "relative_humidity_2m_mean",
]

r = requests.get(url, params=params, timeout=60)
r.raise_for_status()
data = r.json()["daily"]

df = pd.DataFrame(data)
df["time"] = pd.to_datetime(df["time"])

# ── Compute vapour pressure from RH and Tmax/Tmin ────────────
# Saturated vapour pressure (kPa) using Buck equation
def sat_vap_pressure(T):
    return 0.61078 * math.exp(17.269 * T / (T + 237.3))

df["TMAX"]  = df["temperature_2m_max"]
df["TMIN"]  = df["temperature_2m_min"]
df["TMEAN"] = (df["TMAX"] + df["TMIN"]) / 2
df["VAP"]   = df.apply(
    lambda row: round(row["relative_humidity_2m_mean"] / 100.0
                      * sat_vap_pressure(row["TMEAN"]), 4),
    axis=1
)

# IRRAD: Open-Meteo gives MJ/m2/day → PCSE CSV expects kJ/m2/day
df["IRRAD"]     = (df["shortwave_radiation_sum"] * 1000).round(0)
df["WIND"]      = df["windspeed_10m_max"].round(2)   # m/s
df["RAIN"]      = df["precipitation_sum"].round(2)   # mm
df["SNOWDEPTH"] = "NaN"
df["DAY"]       = df["time"].dt.strftime("%Y%m%d")

# ── Write PCSE CSV ─────────────────────────────────────────────
header = f"""## Site Characteristics
Country = 'Turkey'
Station = 'Menemen, Izmir'
Description = 'ERA5 reanalysis data via Open-Meteo archive API'
Source = 'https://archive-api.open-meteo.com'
Contact = 'Generated for WOFOST simulation'
Longitude = {LON}; Latitude = {LAT}; Elevation = {ELEVATION}; AngstromA = 0.25; AngstromB = 0.50; HasSunshine = False
## Daily weather observations (missing values are NaN)
"""

out_df = df[["DAY", "IRRAD", "TMIN", "TMAX", "VAP", "WIND", "RAIN", "SNOWDEPTH"]]

with open(OUTPUT_FILE, "w") as f:
    f.write(header)
    out_df.to_csv(f, index=False)

print(f"Done! Saved {len(out_df)} days of weather to: {OUTPUT_FILE}")
print(f"Date range: {out_df['DAY'].iloc[0]} → {out_df['DAY'].iloc[-1]}")
print("\nSample rows:")
print(out_df.head(5).to_string(index=False))
