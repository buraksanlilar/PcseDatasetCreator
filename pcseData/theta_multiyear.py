import os
import copy
import random
import sys
import pandas as pd
from tqdm import tqdm

from pcse.input import YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider, CSVWeatherDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

# 1. Klasor yollarini belirle
base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)

if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from openmeteo.daily import fetch_and_save_pcse_weather
from openmeteo.hourly import fetch_hourly_sensor_data
from openmeteo.elevation import fetch_batch_elevations
from openmeteo.wav_provider import get_site_data

crop_dir = os.path.join(parent_dir, "cropTypes")
soil_dir = os.path.join(parent_dir, "soilTypes")
openmeteo_dir = os.path.join(parent_dir, "openmeteo")
daily_weather_dir = os.path.join(openmeteo_dir, "pcse_weather_data")
hourly_weather_dir = os.path.join(openmeteo_dir, "hourly_weather_data")

dataset_output_dir = os.path.join(parent_dir, "dataset_output")
yearly_output_dir = os.path.join(dataset_output_dir, "yearly")
os.makedirs(yearly_output_dir, exist_ok=True)

progress_file = os.path.join(dataset_output_dir, "progress_multiyear.csv")
errors_file = os.path.join(dataset_output_dir, "errors_multiyear.csv")

# Bitki verilerini yukle
cropd = YAMLCropDataProvider(fpath=crop_dir, force_reload=True)
all_crops_varieties = cropd.get_crops_varieties()

available_agro_files = {
    f: os.path.join(parent_dir, "agroManagement", f)
    for f in os.listdir(os.path.join(parent_dir, "agroManagement"))
    if f.endswith(".agro")
}

# --- Coordinate based locations (replace district dependency) ---
LOCATION_MODE = "grid"
GRID_LAT_MIN = 36.0
GRID_LAT_MAX = 42.0
GRID_LON_MIN = 26.0
GRID_LON_MAX = 45.0
GRID_LAT_STEP = 1.0
GRID_LON_STEP = 1.5
RANDOM_LOCATION_COUNT = 24
RANDOM_SEED = 42

soil_files = [
    f for f in os.listdir(soil_dir)
    if not f.startswith(".") and f.endswith((".new", ".sol", ".awc"))
]


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

    return [{"latitude": lat, "longitude": lon} for lat in latitudes for lon in longitudes]


def build_random_coordinates():
    rng = random.Random(RANDOM_SEED)
    return [
        {
            "latitude": round(rng.uniform(GRID_LAT_MIN, GRID_LAT_MAX), 6),
            "longitude": round(rng.uniform(GRID_LON_MIN, GRID_LON_MAX), 6),
        }
        for _ in range(RANDOM_LOCATION_COUNT)
    ]


def clean_site_config(cfg):
    valid_keys = ["WAV", "SMLIM", "SSI", "SSMAX", "IFUNRN", "NOTINF"]
    return {k: v for k, v in cfg.items() if k in valid_keys}


def build_locations(reference_date=None):
    if LOCATION_MODE == "random":
        coordinates = build_random_coordinates()
    else:
        coordinates = build_grid_coordinates()

    if not coordinates:
        raise RuntimeError("Koordinat listesi olusturulamadi.")

    if not soil_files:
        raise RuntimeError("Soil dosya listesi bos.")

    locations = []
    for index, coordinate in enumerate(coordinates):
        soil_file = soil_files[index % len(soil_files)]
        site_cfg = get_site_data(
            coordinate["latitude"],
            coordinate["longitude"],
            reference_date=reference_date,
        )

        combined = {
            "location_id": f"loc_{index + 1:04d}",
            "latitude": coordinate["latitude"],
            "longitude": coordinate["longitude"],
            "soil_file": soil_file,
            "site_name": "pcse_dynamic_site",
        }
        combined.update(site_cfg)
        locations.append(combined)

    elevations = fetch_batch_elevations(locations)
    for location, elevation in zip(locations, elevations):
        location["elevation"] = elevation if elevation is not None else 100.0

    return locations

site_columns = ["WAV", "SMLIM", "SSI", "SSMAX", "IFUNRN", "NOTINF"]

# ---------------------------------------------------------------


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
    # Bazi dosyalarda provider listesi icinde aktif edilemeyen degerler bulunabiliyor.
    # Bu nedenle sirayla deneyip gercekten set_active_crop kabul eden variety seciliyor.
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

            for date_key in ["crop_start_date", "crop_end_date"]:
                if crop_calendar.get(date_key):
                    crop_calendar[date_key] = _shift_date_to_year(crop_calendar[date_key], year).date()

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


# Site parametreleri
custom_site = {"WAV": 100, "SMLIM": 0.36, "SSI": 0}
sited = WOFOST72SiteDataProvider(**custom_site)


if __name__ == "__main__":
    years = list(range(2014, 2025))

    # Sadece secilebilir variety olan crop'lari once filtreleyelim
    valid_crop_variety_pairs = []
    for crop_name, varieties in all_crops_varieties.items():
        if not list(varieties):
            print(f"Uyari: {crop_name} icin variety bulunamadi, atlaniyor.")
            continue

        variety_name = choose_valid_variety(crop_name, varieties)
        if variety_name is None:
            print(f"Uyari: {crop_name} icin kullanilabilir variety bulunamadi, atlaniyor.")
            continue

        valid_crop_variety_pairs.append((crop_name, variety_name))

    locations = build_locations()
    total_combinations = len(years) * len(valid_crop_variety_pairs) * len(locations)
    estimated_minutes = max(1, total_combinations // 12)

    print(f"Toplam kombinasyon sayisi: {total_combinations}")
    print(f"Tahmini sure: yaklasik {estimated_minutes} dakika")

    all_yearly_paths = []
    progress_records = []
    error_records = []
    processed_counter = 0

    with tqdm(total=total_combinations, desc="Multiyear PCSE", unit="komb") as pbar:
        for year in years:
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"

            # 2. O yilin hava verisini uret (cache mantigi daily/hourly icinde)
            fetch_and_save_pcse_weather(locations, start_date, end_date)
            fetch_hourly_sensor_data(locations, start_date, end_date)

            all_merged_data = []

            for crop_name, variety_name in valid_crop_variety_pairs:
                agro_file_path = find_agro_file_for_crop(crop_name)
                if agro_file_path is None:
                    print(f"Uyari: {crop_name} icin agro dosyasi bulunamadi, atlaniyor.")
                    for location in locations:
                        processed_counter += 1
                        pbar.update(1)
                        progress_records.append({
                            "year": year,
                            "location_id": location["location_id"],
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "status": "agro_file_missing"
                        })
                        error_records.append({
                            "year": year,
                            "location_id": location["location_id"],
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "error": "agro_file_missing"
                        })
                    continue

                agromanagement_raw = YAMLAgroManagementReader(agro_file_path)
                agromanagement = patch_agromanagement_for_year(agromanagement_raw, crop_name, variety_name, year)

                for location in locations:
                    location_id = location["location_id"]
                    soil_file_name = location["soil_file"]
                    site_input = clean_site_config(location)
                    soil_path = os.path.join(soil_dir, soil_file_name)

                    hourly_csv_path = os.path.join(hourly_weather_dir, str(year), f"{location_id}_hourly.csv")
                    daily_csv_path = os.path.join(daily_weather_dir, str(year), f"{location_id}.csv")

                    status = "ok"
                    processed_counter += 1

                    if not all(os.path.exists(p) for p in [soil_path, daily_csv_path, hourly_csv_path]):
                        status = "missing_input"
                        error_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "error": "missing_input_file"
                        })
                        progress_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "status": status
                        })
                        pbar.update(1)
                        if processed_counter % 50 == 0:
                            pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                            pd.DataFrame(error_records).to_csv(errors_file, index=False)
                        continue

                    # Toprak verilerini yukle
                    soild = CABOFileReader(soil_path)
                    if "RDMSOL" not in soild:
                        soild["RDMSOL"] = 150.0

                    sited = WOFOST72SiteDataProvider(**site_input)

                    # Gunluk hava verisini yukle
                    try:
                        wdp = CSVWeatherDataProvider(daily_csv_path, dateformat="%Y%m%d", delimiter=",")
                    except Exception as e:
                        status = "weather_load_error"
                        error_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "error": str(e)
                        })
                        progress_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "status": status
                        })
                        pbar.update(1)
                        if processed_counter % 50 == 0:
                            pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                            pd.DataFrame(error_records).to_csv(errors_file, index=False)
                        continue

                    # WOFOST Motorunu Baslat
                    params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)
                    try:
                        wofost = Wofost72_WLP_CWB(params, wdp, agromanagement)
                        wofost.run_till_terminate()
                    except Exception as e:
                        status = "simulation_error"
                        error_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "error": str(e)
                        })
                        progress_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "status": status
                        })
                        pbar.update(1)
                        if processed_counter % 50 == 0:
                            pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                            pd.DataFrame(error_records).to_csv(errors_file, index=False)
                        continue

                    # Ciktilari Al
                    output = wofost.get_output()
                    df_pcse = pd.DataFrame(output)
                    if df_pcse.empty:
                        status = "empty_simulation"
                        error_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "error": "empty_simulation_output"
                        })
                        progress_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "status": status
                        })
                        pbar.update(1)
                        if processed_counter % 50 == 0:
                            pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                            pd.DataFrame(error_records).to_csv(errors_file, index=False)
                        continue

                    df_pcse["day"] = pd.to_datetime(df_pcse["day"])

                    # 3. Saatlik Veri Birlestirme
                    df_hourly = pd.read_csv(hourly_csv_path)
                    time_column = "DATETIME"

                    if time_column not in df_hourly.columns:
                        status = "hourly_column_missing"
                        error_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "error": f"{time_column}_missing"
                        })
                        progress_records.append({
                            "year": year,
                            "location_id": location_id,
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "crop": crop_name,
                            "variety": variety_name,
                            "status": status
                        })
                        pbar.update(1)
                        if processed_counter % 50 == 0:
                            pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                            pd.DataFrame(error_records).to_csv(errors_file, index=False)
                        continue

                    df_hourly[time_column] = pd.to_datetime(df_hourly[time_column])
                    df_hourly[time_column] = df_hourly[time_column].dt.tz_localize(None)

                    # Tarih ve saat kolonlarini ayir
                    df_hourly["date"] = df_hourly[time_column].dt.date
                    df_hourly["hour"] = df_hourly[time_column].dt.hour

                    # Merge icin gecici kolon (sadece merge icin)
                    df_hourly["_merge_key"] = df_hourly[time_column].dt.normalize()

                    merged_df = pd.merge(df_hourly, df_pcse, left_on="_merge_key", right_on="day", how="left")

                    # Gereksiz kolonlari temizle
                    merged_df.drop(columns=["day", "_merge_key"], errors="ignore", inplace=True)

                    # Meta bilgileri ekle
                    merged_df["latitude"] = location["latitude"]
                    merged_df["longitude"] = location["longitude"]
                    merged_df["elevation"] = location["elevation"]
                    for key in site_columns:
                        merged_df[key] = location.get(key)
                    merged_df["crop_name"] = crop_name
                    merged_df["variety_name"] = variety_name
                    merged_df["year"] = year
                    merged_df["season_id"] = f"{location_id}_{crop_name}_{variety_name}_{year}"

                    all_merged_data.append(merged_df)
                    progress_records.append({
                        "year": year,
                        "location_id": location_id,
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                        "crop": crop_name,
                        "variety": variety_name,
                        "status": status
                    })

                    pbar.update(1)
                    if processed_counter % 50 == 0:
                        pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                        pd.DataFrame(error_records).to_csv(errors_file, index=False)

            # 4. Yillik kayit
            if all_merged_data:
                yearly_dataset = pd.concat(all_merged_data, ignore_index=True)
                yearly_file = os.path.join(yearly_output_dir, f"pcse_{year}.csv")
                yearly_dataset.to_csv(yearly_file, index=False)
                all_yearly_paths.append(yearly_file)
                print(f"Yillik dataset olusturuldu: {yearly_file}")
            else:
                print(f"Uyari: {year} icin birlestirilecek veri yok.")

    # Son progress/error kayitlari
    pd.DataFrame(progress_records).to_csv(progress_file, index=False)
    pd.DataFrame(error_records).to_csv(errors_file, index=False)

    # 5. Final cok yilli kayit
    if all_yearly_paths:
        frames = [pd.read_csv(path) for path in all_yearly_paths]
        final_dataset = pd.concat(frames, ignore_index=True)
        final_output_file = os.path.join(dataset_output_dir, "final_hourly_pcse_dataset_multiyear.csv")
        final_dataset.to_csv(final_output_file, index=False)
        print(f"\nIslem tamamlandi. '{final_output_file}' olusturuldu.")
    else:
        print("\nHicbir yilda veri birlestirilemedi.")
