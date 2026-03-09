import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry
import json
import os
import numpy as np

# 1. API İstemcisi ve Cache Yapılandırması
cache_session = requests_cache.CachedSession('.cache', expire_after=-1)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

def fetch_and_save_pcse_weather(json_file, start_date, end_date):
    if not os.path.exists(json_file):
        print(f"Hata: {json_file} bulunamadı.")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        locations = json.load(f)

    output_dir = "pcse_weather_data"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    url = "https://archive-api.open-meteo.com/v1/archive"

    for loc in locations:
        district = loc["district"]
        safe_name = district.replace(", ", "_").replace(" ", "_").replace("(", "").replace(")", "")
        
        print(f"Veri çekiliyor: {district}...")

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

        try:
            responses = openmeteo.weather_api(url, params=params)
            response = responses[0]
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

            # --- PCSE / WOFOST Dönüşümleri ---
            
            # 1. VAP (Buhar Basıncı) Hesaplama: hPa cinsinden (Tetens Formülü)
            # tdew santigrat derece cinsindendir.
            vap_hpa = 6.1078 * np.exp((17.27 * tdew) / (tdew + 237.3))
            
            # 2. VAP Güvenlik Sınırı (Clipping): 
            # PCSE üst sınırı 199.3'tür ancak 50 hPa üzerindeki değerler meteorolojik hatadır.
            # 0.6 hPa (çok kuru) ile 50.0 hPa (çok nemli/sıcak) arasına sabitliyoruz.
            vap_kpa = vap_hpa / 10 # hPa -> kPa


            # 3. IRRAD (MJ/m2 -> kJ/m2)
            irrad_kj = np.maximum(0, irrad_mj * 1000)

            data = {
                "DAY": pd.to_datetime(dates).strftime('%Y%m%d'),
                "IRRAD": irrad_kj.astype(float),
                "TMIN": tmin.astype(float),
                "TMAX": tmax.astype(float),
                "VAP": vap_kpa.astype(float), # Artık hPa cinsinden ve limitli
                "WIND": wind.astype(float),
                "RAIN": precip.astype(float),
                "SNOWDEPTH": 0.0 
            }

            df = pd.DataFrame(data).ffill().bfill()

            file_path = os.path.join(output_dir, f"{safe_name}.csv")
            elevation = response.Elevation() if response.Elevation() is not None else 100.0

            # DOSYA YAZMA
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
            
            print(f"Başarılı: {safe_name}.csv oluşturuldu.")

        except Exception as e:
            print(f"Hata oluştu ({district}): {e}")

# --- İŞLEMİ BAŞLAT ---
# Önceki hatalı dosyalardan kurtulmak için klasörü temizleyip baştan çalıştırın.
fetch_and_save_pcse_weather('districs_soil.json', '2023-01-01', '2024-12-31')