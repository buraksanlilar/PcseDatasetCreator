import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry
import json
import os
import numpy as np
import time

script_dir = os.path.dirname(os.path.abspath(__file__))

# 1. API Client and Cache Configuration
cache_session = requests_cache.CachedSession(os.path.join(script_dir, '.cache'), expire_after=-1)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)


def _extract_single_year(start_date, end_date):
    start_year = pd.to_datetime(start_date).year
    end_year = pd.to_datetime(end_date).year
    if start_year != end_year:
        raise ValueError("start_date and end_date must be within the same year.")
    return str(start_year)


def _is_rate_limit_error(error):
    message = str(error).lower()
    return "request limit exceeded" in message or "please try again in one minute" in message


def _request_daily_with_retry(url, params, max_attempts=5, wait_seconds=65):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            responses = openmeteo.weather_api(url, params=params)
            return responses[0]
        except Exception as e:
            last_error = e
            if _is_rate_limit_error(e) and attempt < max_attempts:
                print(f"Rate limit reached. Waiting {wait_seconds}s (attempt {attempt}/{max_attempts})...")
                time.sleep(wait_seconds)
                continue
            raise
    raise last_error


def _load_locations(locations_source):
    if isinstance(locations_source, str):
        with open(locations_source, 'r', encoding='utf-8') as f:
            return json.load(f)
    return list(locations_source)


def _location_slug(location, index):
    if location.get("location_id"):
        return location["location_id"]

    latitude = str(location["latitude"]).replace("-", "m").replace(".", "p")
    longitude = str(location["longitude"]).replace("-", "m").replace(".", "p")
    return f"loc_{index:05d}_lat_{latitude}_lon_{longitude}"


def fetch_and_save_pcse_weather(locations_source, start_date, end_date):
    if isinstance(locations_source, str) and not os.path.exists(locations_source):
        print(f"Error: {locations_source} not found.")
        return

    locations = _load_locations(locations_source)

    year = _extract_single_year(start_date, end_date)
    output_dir = os.path.join(script_dir, "weather_data", "daily", year)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    url = "https://archive-api.open-meteo.com/v1/archive"

    for idx, loc in enumerate(locations):
        safe_name = _location_slug(loc, idx)
        file_path = os.path.join(output_dir, f"{safe_name}.csv")

        if os.path.exists(file_path):
            print(f"Skipped (cache): {safe_name}.csv")
            continue

        # Spread requests slightly to avoid hitting the per-minute API limit.
        if idx > 0:
            time.sleep(1.2)

        label = loc.get("location_id", f"{loc['latitude']}, {loc['longitude']}")
        print(f"Fetching data: {label}...")

        params = {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "start_date": start_date,
            "end_date": end_date,
            "daily": [
                "temperature_2m_max", 
                "temperature_2m_min", 
                "shortwave_radiation_sum", 
                "precipitation_sum", 
                "wind_speed_10m_max", 
                "dewpoint_2m_mean"
            ],
            "timezone": "auto",
            "wind_speed_unit": "ms"
        }

        if loc.get("elevation") is not None:
            params["elevation"] = loc["elevation"]

        try:
            response = _request_daily_with_retry(url, params)
            daily = response.Daily()

            dates = pd.date_range(
                start=pd.to_datetime(daily.Time(), unit="s", utc=True),
                end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=daily.Interval()),
                inclusive="left"
            ).date

            tmax = daily.Variables(0).ValuesAsNumpy()
            tmin = daily.Variables(1).ValuesAsNumpy()
            irrad_mj = daily.Variables(2).ValuesAsNumpy()
            precip = daily.Variables(3).ValuesAsNumpy()
            wind = daily.Variables(4).ValuesAsNumpy()
            tdew = daily.Variables(5).ValuesAsNumpy()

            # --- PCSE / WOFOST Conversions ---

            # 1. VAP (Vapour Pressure) calculation: in hPa (Tetens formula)
            # tdew is in degrees Celsius.
            vap_hpa = 6.1078 * np.exp((17.27 * tdew) / (tdew + 237.3))

            # 2. VAP safety clipping:
            # PCSE upper limit is 199.3 but values above 50 hPa are meteorological errors.
            # We clip to between 0.6 hPa (very dry) and 50.0 hPa (very humid/hot).
            vap_kpa = vap_hpa / 10  # hPa -> kPa

            # 3. IRRAD (MJ/m2 -> kJ/m2)
            irrad_kj = np.maximum(0, irrad_mj * 1000)

            data = {
                "DAY": pd.to_datetime(dates).strftime('%Y%m%d'),
                "IRRAD": irrad_kj.astype(float),
                "TMIN": tmin.astype(float),
                "TMAX": tmax.astype(float),
                "VAP": vap_kpa.astype(float), # Now in hPa and clipped
                "WIND": wind.astype(float),
                "RAIN": precip.astype(float),
                "SNOWDEPTH": 0.0 
            }

            df = pd.DataFrame(data).ffill().bfill()

            elevation = loc.get("elevation")
            if elevation is None:
                elevation = response.Elevation() if response.Elevation() is not None else 100.0

            # WRITE FILE
            with open(file_path, 'w', encoding='utf-8', newline='') as f_out:
                f_out.write("## Site Characteristics\n")
                f_out.write("Country='Turkey'\n")
                f_out.write(f"Station='{safe_name}'\n")
                f_out.write("Description='OpenMeteo Daily Data'\n")
                f_out.write("Source='OpenMeteo'\n")
                f_out.write("Contact='None'\n")
                f_out.write(f"Longitude={loc['longitude']}\n")
                f_out.write(f"Latitude={loc['latitude']}\n")
                f_out.write(f"Elevation={elevation}\n")
                f_out.write("AngstromA=0.18\n")
                f_out.write("AngstromB=0.55\n")
                f_out.write("HasSunshine=False\n")
                f_out.write("## Daily weather observations\n")
                
                df.to_csv(f_out, index=False, header=True, float_format='%.2f')
            
            print(f"Success: {safe_name}.csv created.")

        except Exception as e:
            print(f"Error occurred ({label}): {e}")

# --- START PROCESS ---
if __name__ == "__main__":
    fetch_and_save_pcse_weather('districs_soil.json', '2024-01-01', '2024-12-31')