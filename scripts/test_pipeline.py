"""
Small-scale dry run test of the theta_multiyear.py pipeline.

Checks:
  - Soil matcher integration
  - Weather data fetching (daily + hourly)
  - WOFOST simulation (3 WAV scenarios)
  - Merge and output columns (TWSO, sim_success, wav_scenario...)

Usage:
    python scripts/dry_run_multiyear.py
"""

import os
import sys
import copy
import tempfile
import pandas as pd

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pcse.input import (
    YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader,
    WOFOST72SiteDataProvider, CSVWeatherDataProvider,
)
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

from providers.weather_daily import fetch_and_save_pcse_weather
from providers.weather_hourly import fetch_hourly_sensor_data
from providers.elevation import fetch_batch_elevations
from providers.soil_matcher import load_soil_files, match_soil

# ── Sabitler ──────────────────────────────────────────────────────────────────

YEAR       = 2022
TEST_CROPS = ["wheat", "maize", "rapeseed", "faba_bean"]

TEST_LOCATIONS = [
    {"latitude": 39.0, "longitude": 35.0},   # Central Anatolia
    {"latitude": 37.0, "longitude": 32.0},   # Konya
    {"latitude": 41.0, "longitude": 29.0},   # Marmara
]

WAV_SCENARIOS = {
    "dry":    10,
    "normal": 50,
    "wet":    100,
}

crop_dir         = os.path.join(project_root, "crops")
soil_dir         = os.path.join(project_root, "soils")
agro_dir         = os.path.join(project_root, "agro")
openmeteo_dir    = os.path.join(project_root, "providers")
daily_weather_dir  = os.path.join(openmeteo_dir, "weather_data", "daily")
hourly_weather_dir = os.path.join(openmeteo_dir, "weather_data", "hourly")

_tmp_files: list[str] = []


def _make_daily_wdp(location_id: str, sim_year: int, crop_end_year: int):
    y1_csv = os.path.join(daily_weather_dir, str(sim_year), f"{location_id}.csv")
    if crop_end_year == sim_year:
        return CSVWeatherDataProvider(y1_csv, dateformat="%Y%m%d", delimiter=",")

    y2_csv = os.path.join(daily_weather_dir, str(crop_end_year), f"{location_id}.csv")
    if not os.path.exists(y2_csv):
        raise FileNotFoundError(f"Sonraki yıl hava verisi yok: {y2_csv}")

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
    _tmp_files.append(tmp.name)
    return CSVWeatherDataProvider(tmp.name, dateformat="%Y%m%d", delimiter=",")

# ── Helper functions (taken from theta_multiyear) ────────────────────────────

def _shift_date_to_year(date_value, year):
    dt = pd.to_datetime(date_value)
    return dt.replace(year=year)


def patch_agromanagement_for_year(agromanagement, crop_name, variety_name, year):
    updated = []
    for campaign in agromanagement:
        campaign_start = next(iter(campaign.keys()))
        campaign_data  = copy.deepcopy(campaign[campaign_start])
        new_start      = _shift_date_to_year(campaign_start, year).date()

        if "CropCalendar" in campaign_data and campaign_data["CropCalendar"] is not None:
            cc = campaign_data["CropCalendar"]
            cc["crop_name"]    = crop_name
            cc["variety_name"] = variety_name

            if cc.get("crop_start_date"):
                cc["crop_start_date"] = _shift_date_to_year(cc["crop_start_date"], year).date()

            if cc.get("crop_end_date"):
                end   = _shift_date_to_year(cc["crop_end_date"], year).date()
                start = cc["crop_start_date"]
                if start and end <= start:
                    end = _shift_date_to_year(cc["crop_end_date"], year + 1).date()
                cc["crop_end_date"] = end

        if "TimedEvents" in campaign_data and campaign_data["TimedEvents"]:
            for event in (campaign_data["TimedEvents"] or []):
                if not isinstance(event, dict):
                    continue
                eb = event[next(iter(event))]
                table = eb.get("events_table")
                if isinstance(table, list):
                    eb["events_table"] = [
                        {_shift_date_to_year(next(iter(it)), year).date(): it[next(iter(it))]}
                        if isinstance(it, dict) else it
                        for it in table
                    ]

        updated.append({new_start: campaign_data})
    return updated


def choose_valid_variety(cropd, crop_name, varieties):
    for v in sorted(varieties):
        try:
            cropd.set_active_crop(crop_name, v)
            return v
        except Exception:
            continue
    return None

# ── Main test ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("DRY RUN — theta_multiyear pipeline test")
    print(f"Year: {YEAR}  |  Crops: {TEST_CROPS}  |  Locations: {len(TEST_LOCATIONS)}")
    print("=" * 60)

    # 1. Soil matcher
    print("\n[1] Loading soil matcher...")
    soil_files_data = load_soil_files(soil_dir)
    print(f"    {len(soil_files_data)} WOFOST files loaded.")

    locations = []
    for i, coord in enumerate(TEST_LOCATIONS):
        result    = match_soil(coord["latitude"], coord["longitude"], soil_files_data)
        soil_file = result["best_match"]["filename"]
        locations.append({
            "location_id": f"loc_{i+1:04d}",
            "latitude":    coord["latitude"],
            "longitude":   coord["longitude"],
            "soil_file":   soil_file,
        })

    # 2. Elevation
    print("\n[2] Fetching elevation...")
    elevations = fetch_batch_elevations(locations)
    for loc, elev in zip(locations, elevations):
        loc["elevation"] = elev if elev is not None else 100.0
        print(f"    {loc['location_id']}  ({loc['latitude']}, {loc['longitude']})  "
              f"elev={loc['elevation']:.0f}m  soil={loc['soil_file']}")

    # 3. Weather data
    start_date = f"{YEAR}-01-01"
    end_date   = f"{YEAR}-12-31"

    print(f"\n[3] Fetching daily weather data ({YEAR})...")
    fetch_and_save_pcse_weather(locations, start_date, end_date)

    print(f"\n[4] Fetching hourly weather data ({YEAR})...")
    fetch_hourly_sensor_data(locations, start_date, end_date)

    # 4. Crop and agro
    cropd = YAMLCropDataProvider(fpath=crop_dir, force_reload=True)
    all_crops_varieties = cropd.get_crops_varieties()

    valid_pairs = []
    for crop_name in TEST_CROPS:
        varieties = all_crops_varieties.get(crop_name, set())
        variety   = choose_valid_variety(cropd, crop_name, varieties)
        if variety is None:
            print(f"    [WARNING] No valid variety found for {crop_name}.")
            continue
        agro_path = os.path.join(agro_dir, f"{crop_name}_calendar.agro")
        if not os.path.exists(agro_path):
            print(f"    [WARNING] {crop_name}_calendar.agro not found.")
            continue
        valid_pairs.append((crop_name, variety, agro_path))

    # 5. Simulation
    print(f"\n[5] WOFOST simulations ({len(valid_pairs)} crops × "
          f"{len(locations)} locations × {len(WAV_SCENARIOS)} scenarios)...")

    all_rows = []
    errors   = []

    for crop_name, variety_name, agro_path in valid_pairs:
        agro_raw = YAMLAgroManagementReader(agro_path)
        agro     = patch_agromanagement_for_year(agro_raw, crop_name, variety_name, YEAR)

        # Determine harvest year for winter crops
        try:
            last_camp = agro[-1]
            cc = last_camp[next(iter(last_camp))].get("CropCalendar") or {}
            crop_end_year = cc.get("crop_end_date").year if cc.get("crop_end_date") else YEAR
        except Exception:
            crop_end_year = YEAR

        if crop_end_year > YEAR:
            print(f"    [{crop_name}] Winter crop → fetching {crop_end_year} weather data...")
            fetch_and_save_pcse_weather(locations, f"{crop_end_year}-01-01", f"{crop_end_year}-12-31")

        for loc in locations:
            loc_id     = loc["location_id"]
            soil_path  = os.path.join(soil_dir, loc["soil_file"])
            daily_csv  = os.path.join(daily_weather_dir, str(YEAR), f"{loc_id}.csv")
            hourly_csv = os.path.join(hourly_weather_dir, str(YEAR), f"{loc_id}_hourly.csv")

            if not all(os.path.exists(p) for p in [soil_path, daily_csv, hourly_csv]):
                errors.append(f"Missing file: {loc_id} / {crop_name}")
                continue

            soild = CABOFileReader(soil_path)
            if "RDMSOL" not in soild:
                soild["RDMSOL"] = 150.0

            try:
                wdp = _make_daily_wdp(loc_id, YEAR, crop_end_year)
            except Exception as e:
                errors.append(f"Weather load error {loc_id}/{crop_name}: {e}")
                continue

            df_hourly = pd.read_csv(hourly_csv)
            df_hourly["DATETIME"]   = pd.to_datetime(df_hourly["DATETIME"]).dt.tz_localize(None)
            df_hourly["_merge_key"] = df_hourly["DATETIME"].dt.normalize()

            for wav_scenario, wav_cm in WAV_SCENARIOS.items():
                sited  = WOFOST72SiteDataProvider(WAV=wav_cm)
                params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)

                try:
                    wofost = Wofost72_WLP_CWB(params, wdp, agro)
                    wofost.run_till_terminate()
                except Exception as e:
                    errors.append(f"Sim error {loc_id}/{crop_name}/{wav_scenario}: {e}")
                    continue

                output  = wofost.get_output()
                df_pcse = pd.DataFrame(output)
                if df_pcse.empty:
                    errors.append(f"Empty output: {loc_id}/{crop_name}/{wav_scenario}")
                    continue

                # Harvest yield: last TWSO value from WOFOST output (including winter crops)
                twso_series  = df_pcse["TWSO"].dropna() if "TWSO" in df_pcse.columns else pd.Series([], dtype=float)
                harvest_twso = float(twso_series.iloc[-1]) if not twso_series.empty else 0.0

                df_pcse["day"] = pd.to_datetime(df_pcse["day"])
                merged = pd.merge(df_hourly, df_pcse, left_on="_merge_key", right_on="day", how="left")
                merged.drop(columns=["day", "_merge_key"], errors="ignore", inplace=True)

                merged["location_id"]  = loc_id
                merged["latitude"]     = loc["latitude"]
                merged["longitude"]    = loc["longitude"]
                merged["elevation"]    = loc["elevation"]
                merged["soil_file"]    = loc["soil_file"]
                merged["crop_name"]    = crop_name
                merged["variety_name"] = variety_name
                merged["year"]         = YEAR
                merged["wav_scenario"] = wav_scenario
                merged["WAV"]          = wav_cm
                merged["season_id"]    = f"{loc_id}_{crop_name}_{variety_name}_{YEAR}_{wav_scenario}"
                merged["harvest_twso"] = harvest_twso
                merged["sim_success"]  = int(harvest_twso > 0)
                merged["TWSO"]         = merged["TWSO"].fillna(0)

                all_rows.append(merged)
                print(f"    ✓ {loc_id} | {crop_name} | {wav_scenario:6s} | "
                      f"harvest={'yes' if harvest_twso > 0 else 'no'} | harvest_twso={harvest_twso:.1f}")

    # 6. Results
    print("\n" + "=" * 60)
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")

    if all_rows:
        df_out = pd.concat(all_rows, ignore_index=True)
        out_path = os.path.join(project_root, "output", "dry_run_multiyear.csv")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df_out.to_csv(out_path, index=False)
        print(f"\nRow count : {len(df_out):,}")
        print(f"Columns   : {list(df_out.columns)}")
        print(f"Output    : {out_path}")
    else:
        print("No simulation could be completed.")

    for tmp_path in _tmp_files:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    print("=" * 60)


if __name__ == "__main__":
    main()
