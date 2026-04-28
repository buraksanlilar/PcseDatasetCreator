import pandas as pd
import dask.dataframe as dd

df = dd.read_csv("final_hourly_pcse_dataset_all_crops.csv",
                 dtype={'DATETIME':'str','date':'str',
                                    'location_id':'str','crop_name':'str','variety_name':'str'})

group_cols = [
      'latitude', 'longitude', 'elevation',
      'WAV', 'SMLIM', 'SSI', 'SSMAX', 'IFAM', 'NOTINF',
      'crop_name', 'variety_name', 'year'
]

# Sezon sonu TWSO: her kombinasyonun son dolu TWSO değeri
twso_final = (df[df['TWSO'].notnull()]
                    .groupby(group_cols)['TWSO']
              .last()
              .compute()
              .reset_index())

twso_final.columns = group_cols + ['twso_final']

print("=== TWSO = 0 olan kombinasyonlar ===")
sifir = twso_final[twso_final['twso_final'] == 0]
print(f"Toplam: {len(sifir)}")
print(sifir[['crop_name','latitude','longitude']].to_string())

print("\n=== TWSO < 1 olan kombinasyonlar (pratik sıfır) ===")
cok_dusuk = twso_final[twso_final['twso_final'] < 1]
print(f"Toplam: {len(cok_dusuk)}")
print(cok_dusuk.to_string())

print("\n=== Ürün bazında ortalama ve std TWSO ===")
print(twso_final.groupby('crop_name')['twso_final']
      .agg(['mean','std','min','max','count'])
      .round(1)
      .sort_values('mean', ascending=False)
      .to_string())