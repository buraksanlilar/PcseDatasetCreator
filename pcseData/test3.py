import os
import json
import time
import copy
import pandas as pd
import datetime as dt
from pcse.input import OpenMeteoWeatherDataProvider, YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

# --- KRİTİK HATA ÇÖZÜMÜ ---
from pcse.base.weather import WeatherDataContainer
def custom_setattr(self, key, value):
    object.__setattr__(self, key, value)
WeatherDataContainer.__setattr__ = custom_setattr
# ---------------------------
# Site verisi içindeki gereksiz anahtarları temizleyen fonksiyon
def clean_site_config(cfg):
    # WOFOST72SiteDataProvider'ın kabul ettiği geçerli parametre listesi
    valid_keys = ["WAV", "SMLIM", "SSI", "SSMAX", "NOTINF"]
    return {k: v for k, v in cfg.items() if k in valid_keys}

# 1. Klasör Yolları Kurulumu
base_dir = os.getcwd()
parent_dir = os.path.dirname(base_dir)

dirs = {
    "crop": os.path.join(parent_dir, "cropTypes"),
    "soil": os.path.join(parent_dir, "soilTypes"),
    "site": os.path.join(parent_dir, "siteTypes"),
    "district": os.path.join(parent_dir, "districts"),
    "agro": os.path.join(parent_dir, "agroManagement")
}

# 2. Statik Verilerin Yüklenmesi
with open(os.path.join(dirs["district"], "district_izmir"), "r") as f:
    districts = json.load(f)

with open(os.path.join(dirs["site"], "sites"), "r") as f:
    site_list = json.load(f)

# Sadece geçerli veri dosyalarını al (Gizli dosyaları atla)
crop_files = [f for f in os.listdir(dirs["crop"]) if not f.startswith('.') and f.endswith(".yaml")]
soil_files = [f for f in os.listdir(dirs["soil"]) if not f.startswith('.') and f.endswith((".new", ".sol"))]

results = []

# YAMLCropDataProvider DÖNGÜ DIŞINDA BİR KEZ ÇALIŞTIRILMALIDIR.
crop_data = YAMLCropDataProvider(fpath=dirs["crop"])
all_crops_varieties = crop_data.get_crops_varieties()

# 3. Hava Durumu Önbelleği (18 ilçe için API limitine takılmamak ve hızı artırmak için)
weather_cache = {}

def get_weather(lat, lon):
    key = (lat, lon)
    if key not in weather_cache:
        max_retries = 10
        for i in range(max_retries):
            try:
                # İstekler arasına sabit 2 saniye boşluk bırak (Rate limit önleyici)
                time.sleep(2)
                print(f"Hava durumu çekiliyor: {lat}, {lon} (Deneme {i+1})")
                weather_cache[key] = OpenMeteoWeatherDataProvider(latitude=lat, longitude=lon)
                break 
            except Exception as e:
                if "429" in str(e):
                    # Hata aldıkça artan bekleme süresi (15s, 30s, 45s...)
                    wait_time = (i + 1) * 15
                    print(f"API Limiti! {wait_time} saniye bekleniyor...")
                    time.sleep(wait_time)
                else:
                    print(f"Beklenmedik Meteoroloji Hatası: {e}")
                    return None # Hata durumunda None dön ki simülasyon o koordinatı atlasın
    return weather_cache.get(key)

# 4. Ana Simülasyon Döngüsü
for crop_f in crop_files:
    crop_base_name = crop_f.replace(".yaml", "")
    
    # Gerçek bitki adını bul (Büyük/küçük harf eşleşmesi)
    actual_crop_name = next((k for k in all_crops_varieties.keys() if k.lower() == crop_base_name.lower()), None)
    if actual_crop_name is None:
        continue
        
    # dict_keys hatasını önlemek için listeye çevirip ilk varyeteyi al
    variety_name = list(all_crops_varieties[actual_crop_name])[0]
    crop_data.set_active_crop(actual_crop_name, variety_name)

    # Ekran görüntüsündeki standarda göre agroManagement dosyasını bul
    agro_path = os.path.join(dirs["agro"], f"{crop_base_name}_calendar.agro")
    if not os.path.exists(agro_path): 
        print(f"Uyarı: {agro_path} bulunamadı, {crop_base_name} atlanıyor.")
        continue
        
    base_agro = YAMLAgroManagementReader(agro_path)

    for soil_f in soil_files:
        soil_path = os.path.join(dirs["soil"], soil_f)
        soild = CABOFileReader(soil_path)
        if "RDMSOL" not in soild: soild["RDMSOL"] = 150.0

        for dist in districts:
            wdp = get_weather(dist["latitude"], dist["longitude"])

            for site_cfg in site_list:
                cleaned_cfg = clean_site_config(site_cfg)
                sited = WOFOST72SiteDataProvider(**cleaned_cfg)
                params = ParameterProvider(cropdata=crop_data, soildata=soild, sitedata=sited)

                for year in range(2000, 2021):
                    try:
                        # Dinamik Tarih Kaydırma Algoritması (Kışlık ve yazlık ürünler için güvenli)
                        current_agro = []
                        for campaign in base_agro:
                            orig_start = list(campaign.keys())[0]
                            # Kampanyanın başlangıç yılı ile hedef yıl arasındaki farkı hesapla
                            offset = dt.date(year, orig_start.month, orig_start.day) - orig_start

                            new_campaign = copy.deepcopy(campaign[orig_start])
                            
                            if 'CropCalendar' in new_campaign and new_campaign['CropCalendar'] is not None:
                                cc = new_campaign['CropCalendar']
                                if cc.get('crop_start_date'): cc['crop_start_date'] += offset
                                if cc.get('crop_end_date'): cc['crop_end_date'] += offset

                            if 'TimedEvents' in new_campaign and new_campaign['TimedEvents'] is not None:
                                new_te = []
                                for event in new_campaign['TimedEvents']:
                                    new_event = copy.deepcopy(event)
                                    if 'event_date' in new_event:
                                        new_event['event_date'] += offset
                                    new_te.append(new_event)
                                new_campaign['TimedEvents'] = new_te

                            current_agro.append({orig_start + offset: new_campaign})

                        # Simülasyonu Çalıştır
                        wofost = Wofost72_WLP_CWB(params, wdp, current_agro)
                        wofost.run_till_terminate()
                        output = wofost.get_output()
                        df_out = pd.DataFrame(output)

                        if not df_out.empty:
                            final_val = df_out.iloc[-1]
                            avg_temp = df_out['TEMP'].mean() if 'TEMP' in df_out.columns else 0
                            total_rain = df_out['RAIN'].sum() if 'RAIN' in df_out.columns else 0
                            
                            results.append({
                                "Crop": actual_crop_name,
                                "District": dist["district"],
                                "Site": site_cfg["name"],
                                "SoilType": soil_f,
                                "Year": year,
                                "Avg_Temp": round(avg_temp, 2),
                                "Total_Rain": round(total_rain, 2),
                                "TWSO": round(final_val.get("TWSO", 0), 2),
                                "TAGP": round(final_val.get("TAGP", 0), 2),
                                "LAI": round(df_out["LAI"].max(), 2) if "LAI" in df_out.columns else 0
                            })
                    except Exception as e:
                        # Sadece başarısız olan spesifik kombinasyonu konsola yazdır, döngü devam etsin.
                        pass

# 5. Parquet Formatında Kaydet
final_df = pd.DataFrame(results)
output_path = os.path.join(base_dir, "wofost_dataset.parquet")
final_df.to_parquet(output_path, index=False)
print(f"İşlem tamamlandı. Toplam {len(final_df)} satır veri '{output_path}' konumuna kaydedildi.")