import os
import copy
import random
import sys
import tempfile
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from global_land_mask import globe

from pcse.input import YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider, CSVWeatherDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

# 1. Define folder paths
base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)

if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from providers.elevation import fetch_batch_elevations
from providers.soil_matcher import load_soil_files, match_soil


crop_dir = os.path.join(parent_dir, "crops")
soil_dir = os.path.join(parent_dir, "soils")
openmeteo_dir = os.path.join(parent_dir, "providers")
daily_weather_dir = os.path.join(openmeteo_dir, "weather_data", "daily")
hourly_weather_dir = os.path.join(openmeteo_dir, "weather_data", "hourly")

dataset_output_dir = os.path.join(parent_dir, "output")
yearly_output_dir = os.path.join(dataset_output_dir, "yearly")
os.makedirs(yearly_output_dir, exist_ok=True)

progress_file = os.path.join(dataset_output_dir, "progress_multiyear.parquet")
errors_file = os.path.join(dataset_output_dir, "errors_multiyear.parquet")

# Load crop data
cropd = YAMLCropDataProvider(fpath=crop_dir, force_reload=True)
all_crops_varieties = cropd.get_crops_varieties()

available_agro_files = {
    f: os.path.join(parent_dir, "agro", f)
    for f in os.listdir(os.path.join(parent_dir, "agro"))
    if f.endswith(".agro")
}

# --- Coordinate based locations ---
LOCATION_MODE = "grid"
GRID_LAT_MIN = 36.0
GRID_LAT_MAX = 42.0
GRID_LON_MIN = 26.0
GRID_LON_MAX = 45.0
GRID_LAT_STEP = 1.0
GRID_LON_STEP = 1.5
RANDOM_LOCATION_COUNT = 24
RANDOM_SEED = 42

PARALLEL_WORKERS = 4


def is_land(lat: float, lon: float) -> bool:
    return bool(globe.is_land(lat, lon))


def build_grid_coordinates():
    latitudes = []
    current_lat = GRID_LAT_MIN
    while current_lat <= GRID_LAT_MAX + 1e-9:
        latitudes.append(round(current_lat, 6))
        current_lat += GRID_LAT_STEP

    longitudes = []
    current_lon = GRID_LON_MIN
    while current_lon <= GRID_LON_MAX + 1e-9:
        longitudes.append(round(current_lon, 6))
        current_lon += GRID_LON_STEP

    coords = [
        {"latitude": lat, "longitude": lon}
        for lat in latitudes
        for lon in longitudes
        if is_land(lat, lon)
    ]
    skipped = len(latitudes) * len(longitudes) - len(coords)
    if skipped:
        print(f"  [Land mask] {skipped} sea/lake points skipped, {len(coords)} land points remaining.")
    return coords


def build_random_coordinates():
    rng = random.Random(RANDOM_SEED)
    coords = []
    attempts = 0
    max_attempts = RANDOM_LOCATION_COUNT * 20
    while len(coords) < RANDOM_LOCATION_COUNT and attempts < max_attempts:
        lat = round(rng.uniform(GRID_LAT_MIN, GRID_LAT_MAX), 6)
        lon = round(rng.uniform(GRID_LON_MIN, GRID_LON_MAX), 6)
        attempts += 1
        if is_land(lat, lon):
            coords.append({"latitude": lat, "longitude": lon})
    if len(coords) < RANDOM_LOCATION_COUNT:
        print(f"  [Land mask] Warning: only {len(coords)} land points found out of "
              f"{RANDOM_LOCATION_COUNT} requested ({max_attempts} attempts).")
    return coords


def build_locations():
    if LOCATION_MODE == "random":
        coordinates = build_random_coordinates()
    else:
        coordinates = build_grid_coordinates()

    if not coordinates:
        raise RuntimeError("Could not build coordinate list.")

    soil_files_data = load_soil_files(soil_dir)

    locations = []
    for index, coordinate in enumerate(coordinates):
        result = match_soil(coordinate["latitude"], coordinate["longitude"], soil_files_data)
        soil_file = result["best_match"]["filename"]

        combined = {
            "location_id": f"loc_{index + 1:04d}",
            "latitude": coordinate["latitude"],
            "longitude": coordinate["longitude"],
            "soil_file": soil_file,
            "site_name": "pcse_dynamic_site",
        }
        combined["soil_match_details"] = result
        locations.append(combined)

    elevations = fetch_batch_elevations(locations)
    for location, elevation in zip(locations, elevations):
        location["elevation"] = elevation if elevation is not None else 100.0

    return locations


def _make_daily_wdp(location_id: str, sim_year: int, crop_end_year: int):
    """Returns (CSVWeatherDataProvider, tmp_path_or_None).

    Winter crops need data from two years merged into a temp file.
    Caller is responsible for deleting tmp_path when done.
    """
    y1_csv = os.path.join(daily_weather_dir, str(sim_year), f"{location_id}.csv")

    if crop_end_year == sim_year:
        return CSVWeatherDataProvider(y1_csv, dateformat="%Y%m%d", delimiter=",", force_reload=True), None

    y2_csv = os.path.join(daily_weather_dir, str(crop_end_year), f"{location_id}.csv")
    if not os.path.exists(y2_csv):
        raise FileNotFoundError(f"Next-year weather data not found: {y2_csv}")

    header, data_rows = [], []
    for csv_path in (y1_csv, y2_csv):
        is_first = csv_path == y1_csv
        past_header = False
        with open(csv_path, encoding="utf-8") as f:
            for line in f:
                if not past_header:
                    if line.startswith("DAY,"):
                        past_header = True
                        if is_first:
                            header.append(line)
                    elif is_first:
                        header.append(line)
                else:
                    data_rows.append(line)

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
    tmp.writelines(header)
    tmp.writelines(data_rows)
    tmp.close()
    return CSVWeatherDataProvider(tmp.name, dateformat="%Y%m%d", delimiter=",", force_reload=True), tmp.name


WAV_SCENARIOS = {
    "dry":    10,
    "normal": 50,
    "wet":    100,
}


def normalize_name(value):
    return value.replace("_", "").replace("-", "").lower()


def find_agro_file_for_crop(crop_name):
    expected_name = f"{crop_name}_calendar.agro"
    if expected_name in available_agro_files:
        return available_agro_files[expected_name]

    normalized_crop = normalize_name(crop_name)
    for agro_filename, agro_path in available_agro_files.items():
        agro_base = agro_filename.replace("_calendar.agro", "")
        if normalize_name(agro_base) == normalized_crop:
            return agro_path
    return None


def choose_valid_variety(crop_name, varieties):
    for variety_name in sorted(list(varieties)):
        try:
            cropd.set_active_crop(crop_name, variety_name)
            return variety_name
        except Exception:
            continue
    return None


def _shift_date_to_year(date_value, year):
    dt = pd.to_datetime(date_value)
    return dt.replace(year=year)


def patch_agromanagement_for_year(agromanagement, crop_name, variety_name, year):
    updated = []
    for campaign in agromanagement:
        campaign_start = next(iter(campaign.keys()))
        campaign_data = copy.deepcopy(campaign[campaign_start])

        new_campaign_start = _shift_date_to_year(campaign_start, year).date()

        if "CropCalendar" in campaign_data and campaign_data["CropCalendar"] is not None:
            crop_calendar = campaign_data["CropCalendar"]
            crop_calendar["crop_name"] = crop_name
            crop_calendar["variety_name"] = variety_name

            if crop_calendar.get("crop_start_date"):
                crop_calendar["crop_start_date"] = _shift_date_to_year(crop_calendar["crop_start_date"], year).date()

            if crop_calendar.get("crop_end_date"):
                end = _shift_date_to_year(crop_calendar["crop_end_date"], year).date()
                start = crop_calendar["crop_start_date"]
                if start and end <= start:
                    end = _shift_date_to_year(crop_calendar["crop_end_date"], year + 1).date()
                crop_calendar["crop_end_date"] = end

        if "TimedEvents" in campaign_data and campaign_data["TimedEvents"]:
            timed_events = campaign_data["TimedEvents"]
            if isinstance(timed_events, list):
                for event in timed_events:
                    if not isinstance(event, dict):
                        continue
                    event_name = next(iter(event.keys()))
                    event_block = event[event_name]
                    events_table = event_block.get("events_table")
                    if isinstance(events_table, list):
                        new_table = []
                        for item in events_table:
                            if isinstance(item, dict):
                                old_date = next(iter(item.keys()))
                                val = item[old_date]
                                new_date = _shift_date_to_year(old_date, year).date()
                                new_table.append({new_date: val})
                            else:
                                new_table.append(item)
                        event_block["events_table"] = new_table

        updated.append({new_campaign_start: campaign_data})

    return updated


def _simulate_year(args):
    year, locations, valid_crop_variety_pairs = args

    yearly_file = os.path.join(yearly_output_dir, f"pcse_{year}.parquet")
    if os.path.exists(yearly_file):
        print(f"[{year}] Skipping — already exists.")
        return yearly_file

    print(f"[{year}] Starting simulation...")

    all_merged_data = []
    progress_records = []
    error_records = []
    tmp_files = []

    for crop_name, variety_name in valid_crop_variety_pairs:
        agro_file_path = find_agro_file_for_crop(crop_name)
        if agro_file_path is None:
            for location in locations:
                progress_records.append({
                    "year": year, "location_id": location["location_id"],
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "crop": crop_name, "variety": variety_name, "status": "agro_file_missing"
                })
                error_records.append({
                    "year": year, "location_id": location["location_id"],
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "crop": crop_name, "variety": variety_name, "error": "agro_file_missing"
                })
            continue

        agromanagement_raw = YAMLAgroManagementReader(agro_file_path)
        agromanagement = patch_agromanagement_for_year(agromanagement_raw, crop_name, variety_name, year)

        try:
            last_camp = agromanagement[-1]
            cc = last_camp[next(iter(last_camp))].get("CropCalendar") or {}
            crop_end_year = cc.get("crop_end_date").year if cc.get("crop_end_date") else year
        except Exception:
            crop_end_year = year

        for location in locations:
            location_id = location["location_id"]
            soil_path = os.path.join(soil_dir, location["soil_file"])
            hourly_csv_path = os.path.join(hourly_weather_dir, str(year), f"{location_id}_hourly.csv")
            daily_csv_path = os.path.join(daily_weather_dir, str(year), f"{location_id}.csv")

            if not all(os.path.exists(p) for p in [soil_path, daily_csv_path, hourly_csv_path]):
                error_records.append({
                    "year": year, "location_id": location_id,
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "crop": crop_name, "variety": variety_name, "error": "missing_input_file"
                })
                continue

            soild = CABOFileReader(soil_path)
            if "RDMSOL" not in soild:
                soild["RDMSOL"] = 150.0

            try:
                wdp, tmp_path = _make_daily_wdp(location_id, year, crop_end_year)
                if tmp_path:
                    tmp_files.append(tmp_path)
            except Exception as e:
                error_records.append({
                    "year": year, "location_id": location_id,
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "crop": crop_name, "variety": variety_name, "error": str(e)
                })
                continue

            df_hourly = pd.read_csv(hourly_csv_path)
            if "DATETIME" not in df_hourly.columns:
                error_records.append({
                    "year": year, "location_id": location_id,
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "crop": crop_name, "variety": variety_name, "error": "DATETIME_missing"
                })
                continue

            df_hourly["DATETIME"] = pd.to_datetime(df_hourly["DATETIME"]).dt.tz_localize(None)
            df_hourly["_merge_key"] = df_hourly["DATETIME"].dt.normalize()

            for wav_scenario, wav_fraction in WAV_SCENARIOS.items():
                sited = WOFOST72SiteDataProvider(WAV=wav_fraction)
                params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)

                try:
                    wofost = Wofost72_WLP_CWB(params, wdp, agromanagement)
                    wofost.run_till_terminate()
                except Exception as e:
                    error_records.append({
                        "year": year, "location_id": location_id,
                        "latitude": location["latitude"], "longitude": location["longitude"],
                        "crop": crop_name, "variety": variety_name,
                        "wav_scenario": wav_scenario, "wav_fraction": wav_fraction,
                        "error": str(e)
                    })
                    continue

                output = wofost.get_output()
                df_pcse = pd.DataFrame(output)
                if df_pcse.empty:
                    error_records.append({
                        "year": year, "location_id": location_id,
                        "latitude": location["latitude"], "longitude": location["longitude"],
                        "crop": crop_name, "variety": variety_name,
                        "wav_scenario": wav_scenario, "wav_fraction": wav_fraction,
                        "error": "empty_simulation_output"
                    })
                    continue

                twso_series = df_pcse["TWSO"].dropna() if "TWSO" in df_pcse.columns else pd.Series([], dtype=float)
                harvest_twso = float(twso_series.iloc[-1]) if not twso_series.empty else 0.0

                df_pcse["day"] = pd.to_datetime(df_pcse["day"])
                merged_df = pd.merge(df_hourly, df_pcse, left_on="_merge_key", right_on="day", how="left")
                merged_df.drop(columns=["day", "_merge_key"], errors="ignore", inplace=True)

                merged_df["latitude"]     = location["latitude"]
                merged_df["longitude"]    = location["longitude"]
                merged_df["elevation"]    = location["elevation"]
                merged_df["WAV"]          = wav_fraction
                merged_df["wav_scenario"] = wav_scenario
                merged_df["crop_name"]    = crop_name
                merged_df["variety_name"] = variety_name
                merged_df["year"]         = year
                merged_df["harvest_twso"] = harvest_twso
                merged_df["sim_success"]  = int(harvest_twso > 0)
                merged_df["TWSO"]         = merged_df["TWSO"].fillna(0)

                all_merged_data.append(merged_df)
                progress_records.append({
                    "year": year, "location_id": location_id,
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "crop": crop_name, "variety": variety_name,
                    "wav_scenario": wav_scenario, "wav_fraction": wav_fraction,
                    "status": "ok"
                })

    # Cleanup temp files created by this process
    for tmp_path in tmp_files:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    if all_merged_data:
        pd.concat(all_merged_data, ignore_index=True).to_parquet(yearly_file, index=False)
        print(f"[{year}] Done — {yearly_file}")
    else:
        print(f"[{year}] Warning: no data produced.")
        yearly_file = None

    if progress_records:
        pd.DataFrame(progress_records).to_parquet(
            os.path.join(dataset_output_dir, f"progress_{year}.parquet"), index=False)
    if error_records:
        pd.DataFrame(error_records).to_parquet(
            os.path.join(dataset_output_dir, f"errors_{year}.parquet"), index=False)

    return yearly_file


if __name__ == "__main__":
    years = list(range(2014, 2025))

    valid_crop_variety_pairs = []
    for crop_name, varieties in all_crops_varieties.items():
        if not list(varieties):
            print(f"Warning: no variety found for {crop_name}, skipping.")
            continue
        variety_name = choose_valid_variety(crop_name, varieties)
        if variety_name is None:
            print(f"Warning: no usable variety found for {crop_name}, skipping.")
            continue
        valid_crop_variety_pairs.append((crop_name, variety_name))

    locations = build_locations()
    total_combinations = len(years) * len(valid_crop_variety_pairs) * len(locations) * len(WAV_SCENARIOS)
    print(f"Total combinations: {total_combinations}")
    print(f"Running {len(years)} years across {PARALLEL_WORKERS} workers...")

    args = [(y, locations, valid_crop_variety_pairs) for y in years]

    with ProcessPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        results = list(executor.map(_simulate_year, args))

    all_yearly_paths = [r for r in results if r is not None]

    # Merge per-year progress/error files
    progress_parts = [
        pd.read_parquet(os.path.join(dataset_output_dir, f"progress_{y}.parquet"))
        for y in years
        if os.path.exists(os.path.join(dataset_output_dir, f"progress_{y}.parquet"))
    ]
    if progress_parts:
        pd.concat(progress_parts, ignore_index=True).to_parquet(progress_file, index=False)

    error_parts = [
        pd.read_parquet(os.path.join(dataset_output_dir, f"errors_{y}.parquet"))
        for y in years
        if os.path.exists(os.path.join(dataset_output_dir, f"errors_{y}.parquet"))
    ]
    if error_parts:
        pd.concat(error_parts, ignore_index=True).to_parquet(errors_file, index=False)

    if all_yearly_paths:
        print(f"\nSimulation complete. {len(all_yearly_paths)} yearly files saved to '{yearly_output_dir}'.")
        print("Run scripts/merge_dataset.py to combine them into a single parquet file.")
    else:
        print("\nNo data could be produced for any year.")
