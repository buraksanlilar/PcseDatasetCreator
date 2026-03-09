import os
import json
import pandas as pd

from pcse.input import YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider, CSVWeatherDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

# 1. Klasör yollarını belirle
base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)

crop_dir = os.path.join(parent_dir, "cropTypes")
soil_dir = os.path.join(parent_dir, "soilTypes")
openmeteo_dir = os.path.join(parent_dir, "openmeteo")
daily_weather_dir = os.path.join(openmeteo_dir, "pcse_weather_data")
hourly_weather_dir = os.path.join(openmeteo_dir, "hourly_weather_data")

# JSON dosyasını oku
json_path = os.path.join(base_dir, "districs_soil.json")
with open(json_path, 'r', encoding='utf-8') as f:
    districts_data = json.load(f)

# Bitki verilerini yükle
cropd = YAMLCropDataProvider(fpath=crop_dir, force_reload=True)
all_crops_varieties = cropd.get_crops_varieties()

available_agro_files = {
    f: os.path.join(parent_dir, "agroManagement", f)
    for f in os.listdir(os.path.join(parent_dir, "agroManagement"))
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

# Site parametreleri
custom_site = {"WAV": 100, "SMLIM": 0.36, "SSI": 0}
sited = WOFOST72SiteDataProvider(**custom_site)

all_merged_data = []

# 2. Her bir crop ve ilçe için modeli çalıştır
for crop_name, varieties in all_crops_varieties.items():
    if not list(varieties):
        print(f"Uyarı: {crop_name} için variety bulunamadı, atlanıyor.")
        continue

    variety_name = choose_valid_variety(crop_name, varieties)
    if variety_name is None:
        print(f"Uyarı: {crop_name} için kullanılabilir variety bulunamadı, atlanıyor.")
        continue

    agro_file_path = find_agro_file_for_crop(crop_name)
    if agro_file_path is None:
        print(f"Uyarı: {crop_name} için agro dosyası bulunamadı, atlanıyor.")
        continue

    agromanagement = YAMLAgroManagementReader(agro_file_path)
    patch_agromanagement_for_crop(agromanagement, crop_name, variety_name)

    print(f"\n[{crop_name}] variety={variety_name} - simülasyon başlıyor.")

    for item in districts_data:
        district = item["district"]
        soil_raw = item["soilType"]

        formatted_district = district.replace(", ", "_").replace(" (", "_").replace(" ", "_").replace(")", "")
        soil_file_name = soil_raw.split(" ")[0]
        soil_path = os.path.join(soil_dir, soil_file_name)

        hourly_csv_path = os.path.join(hourly_weather_dir, f"{formatted_district}_hourly.csv")
        daily_csv_path = os.path.join(daily_weather_dir, f"{formatted_district}.csv")

        if not all(os.path.exists(p) for p in [soil_path, daily_csv_path, hourly_csv_path]):
            print(f"Hata: {crop_name} - {district} için eksik dosya var. Atlanıyor.")
            continue

        # Toprak verilerini yükle
        soild = CABOFileReader(soil_path)
        if "RDMSOL" not in soild:
            soild["RDMSOL"] = 150.0

        # Günlük hava verisini yükle
        try:
            wdp = CSVWeatherDataProvider(daily_csv_path, dateformat='%Y%m%d', delimiter=',')
        except Exception as e:
            print(f"[!] DİKKAT: {crop_name} - {district} hava durumu yüklenemedi. Hata: {e}")
            continue

        # WOFOST Motorunu Başlat
        params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)
        try:
            wofost = Wofost72_WLP_CWB(params, wdp, agromanagement)
            wofost.run_till_terminate()
        except Exception as e:
            print(f"[!] DİKKAT: {crop_name} - {district} simülasyon hatası: {e}")
            continue

        # Çıktıları Al
        output = wofost.get_output()
        df_pcse = pd.DataFrame(output)
        if df_pcse.empty:
            print(f"Uyarı: {crop_name} - {district} simülasyon çıktısı boş.")
            continue
        df_pcse['day'] = pd.to_datetime(df_pcse['day'])

        # 3. Saatlik Veri Birleştirme
        df_hourly = pd.read_csv(hourly_csv_path)
        time_column = 'DATETIME'

        if time_column in df_hourly.columns:
            df_hourly[time_column] = pd.to_datetime(df_hourly[time_column])
            df_hourly[time_column] = df_hourly[time_column].dt.tz_localize(None)
            
            # Tarih ve saat kolonlarını ayır
            df_hourly['date'] = df_hourly[time_column].dt.date
            df_hourly['hour'] = df_hourly[time_column].dt.hour
            
            # Merge için geçici kolon (sadece merge için)
            df_hourly['_merge_key'] = df_hourly[time_column].dt.normalize()

            merged_df = pd.merge(df_hourly, df_pcse, left_on='_merge_key', right_on='day', how='left')
            
            # Gereksiz kolonları temizle
            merged_df.drop(columns=['day', '_merge_key'], errors='ignore', inplace=True)
            
            # Meta bilgileri ekle
            merged_df['district_name'] = district
            merged_df['crop_name'] = crop_name
            merged_df['variety_name'] = variety_name

            all_merged_data.append(merged_df)
            print(f"{crop_name} - {district} için simülasyon ve veri birleştirme başarılı.")
        else:
            print(f"Hata: {crop_name} - {district} saatlik dosyasında {time_column} bulunamadı.")

# 4. Final Kayıt
if all_merged_data:
    final_dataset = pd.concat(all_merged_data, ignore_index=True)
    output_file = "final_hourly_pcse_dataset_all_crops.csv"
    final_dataset.to_csv(output_file, index=False)
    print(f"\nİşlem tamamlandı. '{output_file}' oluşturuldu.")
else:
    print("\nHiçbir veri birleştirilemedi.")