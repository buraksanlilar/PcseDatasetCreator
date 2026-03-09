import os
import datetime as dt
import pandas as pd
import matplotlib.pyplot as plt

from pcse.input import OpenMeteoWeatherDataProvider, YAMLCropDataProvider, CABOFileReader, YAMLAgroManagementReader, WOFOST72SiteDataProvider
from pcse.base import ParameterProvider
from pcse.models import Wofost72_WLP_CWB

# 1. Klasör yollarını belirle
base_dir = os.getcwd()

parent_dir = os.path.dirname(base_dir)

crop_dir = os.path.join(parent_dir, "cropTypes")
soil_dir = os.path.join(parent_dir, "soilTypes")

# Sadece nokta ile başlamayan dosyaları filtrele
# 2. cropTypes ve soilTypes klasörlerindeki gerçek dosyaları filtrele (Gizli dosyaları atla)
# İstenen spesifik soil dosyaları
target_soils = {
    'ec2.new', 'ec3.new', 'm02.awc', 'm03.awc', 
    'spg006.awc', 'spg007.awc', 'sr4.new'
}

# 1. cropTypes klasörü filtreleme
crop_files = [f for f in os.listdir(crop_dir) if not f.startswith('.') and f.endswith('.yaml')]

# 2. soilTypes klasörü filtreleme (Sadece listedeki dosyaları al)
soil_files = [f for f in os.listdir(soil_dir) if f in target_soils]

if not crop_files or not soil_files:
    raise FileNotFoundError("Belirtilen klasörlerde uygun veri dosyası bulunamadı.")

crop_file_name = sorted(crop_files)[0]
soil_file_name = sorted(soil_files)[0]

crop_path = os.path.join(crop_dir, crop_file_name)
soil_path = os.path.join(soil_dir, soil_file_name)

# 3. Veri Sağlayıcılarını (Data Providers) Hazırla
# Bitki verilerini yükle (barley.yaml)
cropd = YAMLCropDataProvider(fpath=crop_dir)
cropd.set_active_crop('barley', 'Spring_barley_301')

# Toprak verilerini yükle (ec1.new)
soild = CABOFileReader(soil_path)

if "RDMSOL" not in soild:
    soild["RDMSOL"] = 150.0  # Toprağın max kök derinliği (cm)

# Hava durumu verilerini OpenMeteo'dan çek (38.60819, 27.08609)
wdp = OpenMeteoWeatherDataProvider(latitude=38.60819, longitude=27.08609)

# Site parametreleri (varsayılan değerler)
custom_site = {"WAV": 100, "SMLIM": 0.36, "SSI": 0}
sited = WOFOST72SiteDataProvider(**custom_site)

# 4. Parametreleri birleştir
params = ParameterProvider(cropdata=cropd, soildata=soild, sitedata=sited)

# 5. Agromanagement (Tarım Yönetimi) Yapılandırması
# Simülasyonu 2023 başında başlatıp arpa için uygun bir tarihte ekim yapıyoruz
agromanagement_file = "../agroManagement/barley_calendar.agro"
agromanagement = YAMLAgroManagementReader(agromanagement_file)

# 6. WOFOST Motorunu Başlat ve Çalıştır
wofost = Wofost72_WLP_CWB(params, wdp, agromanagement)
wofost.run_till_terminate()

# 7. Sonuçları Al ve Görselleştir
output = wofost.get_output()
df = pd.DataFrame(output).set_index("day")

# Örnek çıktı: Yaprak Alan İndeksi (LAI) ve Toplam Biyokütle (TAGP)
df[['LAI', 'TAGP']].plot(secondary_y='TAGP', figsize=(10, 6), 
                         title=f"Simülasyon: {crop_file_name} & {soil_file_name}")
plt.show()

print(f"Simülasyon tamamlandı. Kullanılan bitki: {crop_file_name}, Toprak: {soil_file_name}")