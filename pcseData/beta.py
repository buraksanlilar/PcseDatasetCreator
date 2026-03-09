import os
import json
import pandas as pd
import datetime as dt

from pcse.input import YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider, CSVWeatherDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

# 1. Klasör yollarını belirle
base_dir = os.getcwd()
parent_dir = os.path.dirname(base_dir)

crop_dir = os.path.join(parent_dir, "cropTypes")
soil_dir = os.path.join(parent_dir, "soilTypes")
openmeteo_dir = os.path.join(parent_dir, "openmeteo")
daily_weather_dir = os.path.join(openmeteo_dir, "pcse_weather_data")
hourly_weather_dir = os.path.join(openmeteo_dir, "hourly_weather_data")

# JSON dosyasını oku
json_path = "districs_soil.json"
with open(json_path, 'r', encoding='utf-8') as f:
    districts_data = json.load(f)

# Bitki verilerini ve Tarım Yönetimini yükle
cropd = YAMLCropDataProvider(fpath=crop_dir)
cropd.set_active_crop('barley', 'Spring_barley_301')

agromanagement_file = os.path.join(parent_dir, "agroManagement", "barley_calendar.agro")
agromanagement = YAMLAgroManagementReader(agromanagement_file)

# Site parametreleri
custom_site = {"WAV": 100, "SMLIM": 0.36, "SSI": 0}
sited = WOFOST72SiteDataProvider(**custom_site)

all_merged_data = []

# 2. Her bir ilçe için modeli çalıştır
for item in districts_data:
    district = item["district"]
    soil_raw = item["soilType"]
    
    formatted_district = district.replace(", ", "_").replace(" (", "_").replace(" ", "_").replace(")", "")
    soil_file_name = soil_raw.split(" ")[0]
    soil_path = os.path.join(soil_dir, soil_file_name)
    
    hourly_csv_path = os.path.join(hourly_weather_dir, f"{formatted_district}_hourly.csv")
    daily_csv_path = os.path.join(daily_weather_dir, f"{formatted_district}.csv")
    
    if not all(os.path.exists(p) for p in [soil_path, daily_csv_path, hourly_csv_path]):
        print(f"Hata: {district} için eksik dosya var. Atlanıyor.")
        continue

    # Toprak verilerini yükle
    soild = CABOFileReader(soil_path)
    if "RDMSOL" not in soild:
        soild["RDMSOL"] = 150.0

    # --- CSVWeatherDataProvider DÜZELTİLMİŞ KULLANIM ---
    try:
        # Hata mesajına göre 'dformat' yerine 'dateformat' kullanıyoruz
        wdp = CSVWeatherDataProvider(daily_csv_path, dateformat='%Y%m%d',delimiter=',')
    except Exception as e:
        print(f"[!] DİKKAT: {district} hava durumu yüklenemedi. Hata: {e}")
        continue

    # WOFOST Motorunu Başlat
    params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)
    wofost = Wofost72_WLP_CWB(params, wdp, agromanagement)
    wofost.run_till_terminate()

    # Çıktıları Al
    output = wofost.get_output()
    df_pcse = pd.DataFrame(output)
    if df_pcse.empty:
        print(f"Uyarı: {district} simülasyon çıktısı boş.")
        continue
    df_pcse['day'] = pd.to_datetime(df_pcse['day'])
    
    # 3. Saatlik Veri Birleştirme
    df_hourly = pd.read_csv(hourly_csv_path)
    time_column = 'DATETIME'

    if time_column in df_hourly.columns:
        df_hourly[time_column] = pd.to_datetime(df_hourly[time_column])
        df_hourly[time_column] = df_hourly[time_column].dt.tz_localize(None) 
        df_hourly['merge_date'] = df_hourly[time_column].dt.normalize()
        
        merged_df = pd.merge(df_hourly, df_pcse, left_on='merge_date', right_on='day', how='left')
        merged_df['district_name'] = district
        
        all_merged_data.append(merged_df)
        print(f"{district} için simülasyon ve veri birleştirme başarılı.")
    else:
        print(f"Hata: {district} saatlik dosyasında {time_column} bulunamadı.")

# 4. Final Kayıt
if all_merged_data:
    final_dataset = pd.concat(all_merged_data, ignore_index=True)
    final_dataset.to_csv("final_hourly_pcse_dataset.csv", index=False)
    print(f"\nİşlem tamamlandı. 'final_hourly_pcse_dataset.csv' oluşturuldu.")
else:
    print("\nHiçbir veri birleştirilemedi.")