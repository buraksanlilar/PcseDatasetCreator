# Small dry-run to validate build_locations and batch elevation
import json
import pandas as pd
import traceback

try:
    from pcseData import theta_multiyear as tm
except Exception:
    import sys
    sys.path.insert(0, '.')
    from pcseData import theta_multiyear as tm

# Use random small sample
tm.LOCATION_MODE = 'random'
vm = 5
try:
    tm.RANDOM_LOCATION_COUNT = vm
except Exception:
    pass

print('Building locations (count=', vm, ')...')
locs = tm.build_locations()
print('Built', len(locs), 'locations')

df = pd.DataFrame(locs)
print(df.head().to_dict(orient='records'))

# Print expected merged columns sample
sample_columns = [
    'DATETIME','AIR_TEMP','AIR_HUMIDITY','PRECIP','SOIL_TEMP_0_7','SOIL_MOISTURE_0_7',
    'date','hour','DVS','LAI','TAGP','TWSO','TWLV','TWST','TWRT','TRA','RD','SM','WWLOW','RFTRA',
    'latitude','longitude','elevation','WAV','SMLIM','SSI','SSMAX','IFUNRN','NOTINF','crop_name','variety_name','year','season_id'
]
print('\nSample expected merged columns:')
print(sample_columns)

print('\nDone')
