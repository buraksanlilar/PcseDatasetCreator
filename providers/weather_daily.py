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


class DailyLimitError(Exception):
    """Raised when the OpenMeteo daily API request limit is exceeded."""


def _is_daily_limit_error(error):
    message = str(error).lower()
    return "daily api request limit exceeded" in message or "please try again tomorrow" in message


def _is_minute_limit_error(error):
    message = str(error).lower()
    return "please try again in one minute" in message


def _request_daily_with_retry(url, params, max_attempts=5, wait_seconds=65):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return openmeteo.weather_api(url, params=params)
        except Exception as e:
            last_error = e
            if _is_daily_limit_error(e):
                raise DailyLimitError(str(e))
            if _is_minute_limit_error(e) and attempt < max_attempts:
                print(f"Per-minute limit reached. Waiting {wait_seconds}s (attempt {attempt}/{max_attempts})...")
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

    pending = [
        (idx, loc) for idx, loc in enumerate(locations)
        if not os.path.exists(os.path.join(output_dir, f"{_location_slug(loc, idx)}.csv"))
    ]

    for idx, loc in enumerate(locations):
        safe_name = _location_slug(loc, idx)
        file_path = os.path.join(output_dir, f"{safe_name}.csv")
        if os.path.exists(file_path):
            print(f"Skipped (cache): {safe_name}.csv")

    if not pending:
        return

    print(f"Fetching daily weather for {len(pending)} locations in one batch request...")

    params = {
        "latitude":  [loc["latitude"]  for _, loc in pending],
        "longitude": [loc["longitude"] for _, loc in pending],
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

    try:
        responses = _request_daily_with_retry(url, params)
    except DailyLimitError:
        print(f"\nDaily API limit reached. Stopping fetch — already-downloaded files are safe.")
        print("Restart tomorrow and the script will continue from where it left off.")
        return
    except Exception as e:
        print(f"Batch request failed: {e}")
        return

    for response, (idx, loc) in zip(responses, pending):
        safe_name = _location_slug(loc, idx)
        file_path = os.path.join(output_dir, f"{safe_name}.csv")
        try:
            daily = response.Daily()

            dates = pd.date_range(
                start=pd.to_datetime(daily.Time(), unit="s", utc=True),
                end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=daily.Interval()),
                inclusive="left"
            ).date

            tmax  = daily.Variables(0).ValuesAsNumpy()
            tmin  = daily.Variables(1).ValuesAsNumpy()
            irrad_mj = daily.Variables(2).ValuesAsNumpy()
            precip = daily.Variables(3).ValuesAsNumpy()
            wind  = daily.Variables(4).ValuesAsNumpy()
            tdew  = daily.Variables(5).ValuesAsNumpy()

            vap_hpa = 6.1078 * np.exp((17.27 * tdew) / (tdew + 237.3))
            vap_kpa = vap_hpa / 10
            irrad_kj = np.maximum(0, irrad_mj * 1000)

            data = {
                "DAY":       pd.to_datetime(dates).strftime('%Y%m%d'),
                "IRRAD":     irrad_kj.astype(float),
                "TMIN":      tmin.astype(float),
                "TMAX":      tmax.astype(float),
                "VAP":       vap_kpa.astype(float),
                "WIND":      wind.astype(float),
                "RAIN":      precip.astype(float),
                "SNOWDEPTH": 0.0
            }

            df = pd.DataFrame(data).ffill().bfill()

            elevation = loc.get("elevation") or response.Elevation() or 100.0

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
            print(f"Error processing {safe_name}: {e}")

# --- START PROCESS ---
if __name__ == "__main__":
    fetch_and_save_pcse_weather('districs_soil.json', '2024-01-01', '2024-12-31')