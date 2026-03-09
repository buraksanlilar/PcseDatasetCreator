import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry
import json
import os

# 1. API İstemcisi ve Cache Yapılandırması
cache_session = requests_cache.CachedSession('.cache', expire_after=-1)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

def fetch_hourly_sensor_data(json_file, start_date, end_date):
    if not os.path.exists(json_file):
        print(f"Hata: {json_file} bulunamadı.")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        locations = json.load(f)

    # Çıktı klasörünü oluştur
    output_dir = "hourly_weather_data"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    url = "https://archive-api.open-meteo.com/v1/archive"

    for loc in locations:
        district = loc["district"]
        safe_name = district.replace(", ", "_").replace(" ", "_").replace("(", "").replace(")", "")
        
        print(f"Saatlik veriler çekiliyor: {district}...")

        params = {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "start_date": start_date,
            "end_date": end_date,
            "hourly": [
                "temperature_2m",           # Hava Sıcaklığı (°C)
                "relative_humidity_2m",    # Hava Nemi (%)
                "precipitation",           # Yağış (mm)
                "soil_temperature_0_to_7cm", # Toprak Sıcaklığı (°C)
                "soil_moisture_0_to_7cm"    # Toprak Nemi (m³/m³)
            ],
            "timezone": "auto"
        }

        try:
            responses = openmeteo.weather_api(url, params=params)
            response = responses[0]
            hourly = response.Hourly()

            # Zaman dizinini oluştur (UTC)
            dates = pd.date_range(
                start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=hourly.Interval()),
                inclusive="left"
            )

            # Verileri sözlük yapısına aktar
            data = {
                "DATETIME": dates,
                "AIR_TEMP": hourly.Variables(0).ValuesAsNumpy(),
                "AIR_HUMIDITY": hourly.Variables(1).ValuesAsNumpy(),
                "PRECIP": hourly.Variables(2).ValuesAsNumpy(),
                "SOIL_TEMP_0_7": hourly.Variables(3).ValuesAsNumpy(),
                "SOIL_MOISTURE_0_7": hourly.Variables(4).ValuesAsNumpy()
            }

            df = pd.DataFrame(data)
            
            # Eksik veri kontrolü ve temizliği
            df = df.ffill().bfill()

            # CSV dosyasını ilgili klasöre kaydet
            file_path = os.path.join(output_dir, f"{safe_name}_hourly.csv")
            df.to_csv(file_path, index=False)
            
        except Exception as e:
            print(f"Hata: {district} verisi alınamadı. Detay: {e}")

# --- ÇALIŞTIRMA ---
# 2023 başından 2024 sonuna kadar olan saatlik veriler
fetch_hourly_sensor_data('districs_soil.json', '2023-01-01', '2024-12-31')

print("\nİşlem tamamlandı. Dosyalar 'hourly_weather_data' klasörüne kaydedildi.")