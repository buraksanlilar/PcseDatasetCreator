import os
import json
import random

import pandas as pd
from global_land_mask import globe

from pcse.input import YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider, CSVWeatherDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

from providers.weather_daily import fetch_and_save_pcse_weather
from providers.weather_hourly import fetch_hourly_sensor_data
from providers.elevation import fetch_batch_elevations
from providers.soilgrids import fetch_batch_soil_features
from providers.wav_provider import get_site_data

# 1. Klasör yollarını belirle
base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)

crop_dir = os.path.join(parent_dir, "crops")
soil_dir = os.path.join(parent_dir, "soils")
openmeteo_dir = os.path.join(parent_dir, "providers")
daily_weather_dir = os.path.join(openmeteo_dir, "weather_data", "daily")
hourly_weather_dir = os.path.join(openmeteo_dir, "weather_data", "hourly")

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

soil_file_cache = {}


def _load_soil_file_properties(soil_file_name):
    if soil_file_name in soil_file_cache:
        return soil_file_cache[soil_file_name]

    soil_path = os.path.join(soil_dir, soil_file_name)
    try:
        cb = CABOFileReader(soil_path)
        props = {
            "RDMSOL": cb.get("RDMSOL"),
            "SMFCF": cb.get("SMFCF"),
            "SMW": cb.get("SMW"),
            "KS": cb.get("KS"),
            "CRAIRC": cb.get("CRAIRC"),
        }
    except Exception:
        props = {}

    soil_file_cache[soil_file_name] = props
    return props


def _soil_match_score(cabo_props, sg_features):
    """Compute a normalized mismatch score (lower is better).

    Strategy (industry-aligned): prefer texture match, then normalized weighted diffs.
    Returns (score, details).
    """
    details = {
        "awc_diff": None,
        "bdod_diff": None,
        "sand_diff": None,
        "silt_diff": None,
        "clay_diff": None,
        "texture_bonus": 1.0,
    }

    total = 0.0
    weight_sum = 0.0

    if cabo_props.get("SMFCF") is not None and sg_features.get("awc") is not None:
        awc_c = float(cabo_props["SMFCF"])
        awc_sg = float(sg_features["awc"])
        d = abs(awc_c - awc_sg) / 0.5
        details["awc_diff"] = d
        w = 4.0
        total += d * w
        weight_sum += w

    if cabo_props.get("RDMSOL") is not None and sg_features.get("bdod") is not None:
        bd_c = float(cabo_props["RDMSOL"]) if cabo_props.get("RDMSOL") is not None else 0.0
        bd_sg = float(sg_features["bdod"])
        d = abs(bd_c - bd_sg) / 0.8
        details["bdod_diff"] = d
        w = 2.0
        total += d * w
        weight_sum += w

    for frac, key in (("sand", "sand_diff"), ("silt", "silt_diff"), ("clay", "clay_diff")):
        if cabo_props.get(frac.upper()) is not None and sg_features.get(frac) is not None:
            cval = float(cabo_props[frac.upper()])
            sval = float(sg_features[frac])
            d = abs(cval - sval) / 100.0
            details[key] = d
            w = 1.0
            total += d * w
            weight_sum += w

    texture_bonus = 1.0
    sg_texture = sg_features.get("texture") if isinstance(sg_features.get("texture"), str) else None
    if sg_texture:
        # no reliable CABO texture available; keep bonus neutral unless explicit mapping provided
        texture_bonus = 1.0
    details["texture_bonus"] = texture_bonus

    score = (total / weight_sum) * texture_bonus if weight_sum > 0 else 1.0
    return score, details


def choose_best_soil_file(sg_features):
    best_soil_file = None
    best_score = None
    best_details = None

    for soil_file in soil_files:
        cabo_props = _load_soil_file_properties(soil_file)
        score, details = _soil_match_score(cabo_props, sg_features)
        if best_score is None or score < best_score:
            best_score = score
            best_soil_file = soil_file
            best_details = details

    return best_soil_file, best_score, best_details

# Bitki verilerini yükle
cropd = YAMLCropDataProvider(fpath=crop_dir, force_reload=True)
all_crops_varieties = cropd.get_crops_varieties()

available_agro_files = {
    f: os.path.join(parent_dir, "agro", f)
    for f in os.listdir(os.path.join(parent_dir, "agro"))
    if f.endswith(".agro")
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
    # Bazı dosyalarda provider listesi içinde aktif edilemeyen değerler bulunabiliyor.
    # Bu nedenle sırayla deneyip gerçekten set_active_crop kabul eden variety seçiliyor.
    for variety_name in sorted(list(varieties)):
        try:
            cropd.set_active_crop(crop_name, variety_name)
            return variety_name
        except Exception:
            continue
    return None


def patch_agromanagement_for_crop(agromanagement, crop_name, variety_name):
    for campaign in agromanagement:
        campaign_start = next(iter(campaign.keys()))
        campaign_data = campaign[campaign_start]
        crop_calendar = campaign_data.get("CropCalendar")
        if crop_calendar is not None:
            crop_calendar["crop_name"] = crop_name
            crop_calendar["variety_name"] = variety_name


def clean_site_config(cfg):
    valid_keys = ["WAV", "SMLIM", "SSI", "SSMAX", "IFUNRN", "NOTINF"]
    return {k: v for k, v in cfg.items() if k in valid_keys}


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
        print(f"  [Kara maskesi] {skipped} deniz/göl noktası atlandı, {len(coords)} kara noktası kaldı.")
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
        print(f"  [Kara maskesi] Uyarı: {RANDOM_LOCATION_COUNT} kara noktası isteğine karşı "
              f"yalnızca {len(coords)} bulunabildi ({max_attempts} denemede).")
    return coords


def build_locations():
    if LOCATION_MODE == "random":
        coordinates = build_random_coordinates()
    else:
        coordinates = build_grid_coordinates()

    if not coordinates:
        raise RuntimeError("Koordinat listesi olusturulamadi.")

    if not soil_files:
        raise RuntimeError("Soil dosya listesi bos.")

    soil_features_list = fetch_batch_soil_features(coordinates)

    locations = []
    for index, coordinate in enumerate(coordinates):
        sg_features = soil_features_list[index] if index < len(soil_features_list) else None
        if sg_features is None:
            soil_file = soil_files[index % len(soil_files)]
            match_details = None
        else:
            chosen = choose_best_soil_file(sg_features)
            if chosen is None:
                soil_file = soil_files[index % len(soil_files)]
                match_details = None
            else:
                soil_file, score, match_details = chosen
                if soil_file is None:
                    soil_file = soil_files[index % len(soil_files)]

        site_cfg = get_site_data(coordinate["latitude"], coordinate["longitude"])

        combined = {
            "location_id": f"loc_{index + 1:04d}",
            "latitude": coordinate["latitude"],
            "longitude": coordinate["longitude"],
            "soil_file": soil_file,
            "site_name": "pcse_dynamic_site",
        }
        combined.update(site_cfg)
        if match_details is not None:
            combined["soil_match_details"] = match_details
        locations.append(combined)

    elevations = fetch_batch_elevations(locations)
    for location, elevation in zip(locations, elevations):
        location["elevation"] = elevation if elevation is not None else 100.0

    return locations


site_columns = ["WAV", "SMLIM", "SSI", "SSMAX", "IFUNRN", "NOTINF"]

WAV_SCENARIOS = {
    "kuru":   10,    # cm — kuru başlangıç
    "normal": 50,    # cm — normal başlangıç
    "islak":  100,   # cm — ıslak başlangıç
}


if __name__ == "__main__":
    locations = build_locations()
    all_merged_data = []
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

    total_combinations = len(years) * len(valid_crop_variety_pairs) * len(locations) * len(WAV_SCENARIOS)
    estimated_minutes = max(1, total_combinations // 12)

    print(f"Toplam kombinasyon sayisi: {total_combinations}  "
          f"({len(WAV_SCENARIOS)} WAV senaryosu: {list(WAV_SCENARIOS.keys())})")
    print(f"Tahmini sure: yaklasik {estimated_minutes} dakika")

    progress_records = []
    error_records = []
    processed_counter = 0
    yearly_output_dir = os.path.join(parent_dir, "output", "yearly")
    os.makedirs(yearly_output_dir, exist_ok=True)
    dataset_output_dir = os.path.join(parent_dir, "output")
    progress_file = os.path.join(dataset_output_dir, "progress_multiyear.csv")
    errors_file = os.path.join(dataset_output_dir, "errors_multiyear.csv")

    from tqdm import tqdm

    with tqdm(total=total_combinations, desc="Multiyear PCSE", unit="komb") as pbar:
        all_yearly_paths = []

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
                agromanagement = [camp for camp in agromanagement_raw]
                patch_agromanagement_for_crop(agromanagement, crop_name, variety_name)

                for location in locations:
                    location_id = location["location_id"]
                    soil_file_name = location["soil_file"]
                    soil_path = os.path.join(soil_dir, soil_file_name)

                    hourly_csv_path = os.path.join(hourly_weather_dir, str(year), f"{location_id}_hourly.csv")
                    daily_csv_path = os.path.join(daily_weather_dir, str(year), f"{location_id}.csv")

                    # Eksik dosya kontrolü — WAV döngüsünden önce yap
                    if not all(os.path.exists(p) for p in [soil_path, daily_csv_path, hourly_csv_path]):
                        for _ in WAV_SCENARIOS:
                            processed_counter += 1
                            pbar.update(1)
                        error_records.append({
                            "year": year, "location_id": location_id,
                            "latitude": location["latitude"], "longitude": location["longitude"],
                            "crop": crop_name, "variety": variety_name,
                            "error": "missing_input_file"
                        })
                        continue

                    # Toprak ve hava verisi — lokasyon başına bir kez yükle
                    soild = CABOFileReader(soil_path)
                    if "RDMSOL" not in soild:
                        soild["RDMSOL"] = 150.0

                    try:
                        wdp = CSVWeatherDataProvider(daily_csv_path, dateformat="%Y%m%d", delimiter=",")
                    except Exception as e:
                        for _ in WAV_SCENARIOS:
                            processed_counter += 1
                            pbar.update(1)
                        error_records.append({
                            "year": year, "location_id": location_id,
                            "latitude": location["latitude"], "longitude": location["longitude"],
                            "crop": crop_name, "variety": variety_name,
                            "error": str(e)
                        })
                        continue

                    df_hourly = pd.read_csv(hourly_csv_path)
                    time_column = "DATETIME"
                    if time_column not in df_hourly.columns:
                        for _ in WAV_SCENARIOS:
                            processed_counter += 1
                            pbar.update(1)
                        error_records.append({
                            "year": year, "location_id": location_id,
                            "latitude": location["latitude"], "longitude": location["longitude"],
                            "crop": crop_name, "variety": variety_name,
                            "error": f"{time_column}_missing"
                        })
                        continue

                    df_hourly[time_column] = pd.to_datetime(df_hourly[time_column]).dt.tz_localize(None)
                    df_hourly["_merge_key"] = df_hourly[time_column].dt.normalize()

                    # WAV senaryoları — toprak/hava verisi paylaşılır, sadece site değişir
                    for wav_scenario, wav_fraction in WAV_SCENARIOS.items():
                        processed_counter += 1
                        status = "ok"

                        sited = WOFOST72SiteDataProvider(WAV=wav_fraction)

                        params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)
                        try:
                            wofost = Wofost72_WLP_CWB(params, wdp, agromanagement)
                            wofost.run_till_terminate()
                        except Exception as e:
                            status = "simulation_error"
                            error_records.append({
                                "year": year, "location_id": location_id,
                                "latitude": location["latitude"], "longitude": location["longitude"],
                                "crop": crop_name, "variety": variety_name,
                                "wav_scenario": wav_scenario, "wav_fraction": wav_fraction,
                                "error": str(e)
                            })
                            pbar.update(1)
                            if processed_counter % 50 == 0:
                                pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                                pd.DataFrame(error_records).to_csv(errors_file, index=False)
                            continue

                        output = wofost.get_output()
                        df_pcse = pd.DataFrame(output)
                        if df_pcse.empty:
                            status = "empty_simulation"
                            error_records.append({
                                "year": year, "location_id": location_id,
                                "latitude": location["latitude"], "longitude": location["longitude"],
                                "crop": crop_name, "variety": variety_name,
                                "wav_scenario": wav_scenario, "wav_fraction": wav_fraction,
                                "error": "empty_simulation_output"
                            })
                            pbar.update(1)
                            if processed_counter % 50 == 0:
                                pd.DataFrame(progress_records).to_csv(progress_file, index=False)
                                pd.DataFrame(error_records).to_csv(errors_file, index=False)
                            continue

                        df_pcse["day"] = pd.to_datetime(df_pcse["day"])
                        merged_df = pd.merge(df_hourly, df_pcse, left_on="_merge_key", right_on="day", how="left")
                        merged_df.drop(columns=["day", "_merge_key"], errors="ignore", inplace=True)

                        merged_df["latitude"]     = location["latitude"]
                        merged_df["longitude"]    = location["longitude"]
                        merged_df["elevation"]    = location["elevation"]
                        for key in site_columns:
                            merged_df[key] = location.get(key)
                        merged_df["WAV"]          = wav_fraction          # senaryo WAV'ını yaz
                        merged_df["wav_scenario"] = wav_scenario
                        merged_df["crop_name"]    = crop_name
                        merged_df["variety_name"] = variety_name
                        merged_df["year"]         = year
                        merged_df["season_id"]    = f"{location_id}_{crop_name}_{variety_name}_{year}_{wav_scenario}"

                        all_merged_data.append(merged_df)
                        progress_records.append({
                            "year": year, "location_id": location_id,
                            "latitude": location["latitude"], "longitude": location["longitude"],
                            "crop": crop_name, "variety": variety_name,
                            "wav_scenario": wav_scenario, "wav_fraction": wav_fraction,
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
        final_output_file = os.path.join(dataset_output_dir, "final_hourly_pcse_dataset_all_crops.csv")
        final_dataset.to_csv(final_output_file, index=False)
        print(f"\nIslem tamamlandi. '{final_output_file}' olusturuldu.")
    else:
        print("\nHicbir yilda veri birlestirilemedi.")