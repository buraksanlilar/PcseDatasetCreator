import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry
import json
import os
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


def _request_hourly_with_retry(url, params, max_attempts=5, wait_seconds=65):
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


def fetch_hourly_sensor_data(locations_source, start_date, end_date):
    if isinstance(locations_source, str) and not os.path.exists(locations_source):
        print(f"Error: {locations_source} not found.")
        return

    locations = _load_locations(locations_source)

    # Create output directory
    year = _extract_single_year(start_date, end_date)
    output_dir = os.path.join(script_dir, "weather_data", "hourly", year)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    url = "https://archive-api.open-meteo.com/v1/archive"

    for idx, loc in enumerate(locations):
        safe_name = _location_slug(loc, idx)
        file_path = os.path.join(output_dir, f"{safe_name}_hourly.csv")

        if os.path.exists(file_path):
            print(f"Skipped (cache): {safe_name}_hourly.csv")
            continue

        # Spread requests slightly to avoid hitting the per-minute API limit.
        if idx > 0:
            time.sleep(1.2)

        label = loc.get("location_id", f"{loc['latitude']}, {loc['longitude']}")
        print(f"Fetching hourly data: {label}...")

        params = {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "start_date": start_date,
            "end_date": end_date,
            "hourly": [
                "temperature_2m",           # Air Temperature (°C)
                "relative_humidity_2m",    # Air Humidity (%)
                "precipitation",           # Precipitation (mm)
                "soil_temperature_0_to_7cm", # Soil Temperature (°C)
                "soil_moisture_0_to_7cm"    # Soil Moisture (m³/m³)
            ],
            "timezone": "auto"
        }

        if loc.get("elevation") is not None:
            params["elevation"] = loc["elevation"]

        try:
            response = _request_hourly_with_retry(url, params)
            hourly = response.Hourly()

            # Build time index (UTC)
            dates = pd.date_range(
                start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=hourly.Interval()),
                inclusive="left"
            )

            # Transfer data into dictionary structure
            data = {
                "DATETIME": dates,
                "AIR_TEMP": hourly.Variables(0).ValuesAsNumpy(),
                "AIR_HUMIDITY": hourly.Variables(1).ValuesAsNumpy(),
                "PRECIP": hourly.Variables(2).ValuesAsNumpy(),
                "SOIL_TEMP_0_7": hourly.Variables(3).ValuesAsNumpy(),
                "SOIL_MOISTURE_0_7": hourly.Variables(4).ValuesAsNumpy()
            }

            df = pd.DataFrame(data)
            
            # Missing data check and cleanup
            df = df.ffill().bfill()

            # Save CSV file to the relevant directory
            df.to_csv(file_path, index=False)
            print(f"Success: {safe_name}_hourly.csv created.")

        except Exception as e:
            print(f"Error: could not retrieve data for {label}. Details: {e}")

# --- RUN ---
if __name__ == "__main__":
    fetch_hourly_sensor_data('districs_soil.json', '2024-01-01', '2024-12-31')
    print("\nProcessing complete. Files saved to 'weather_data/hourly' directory.")