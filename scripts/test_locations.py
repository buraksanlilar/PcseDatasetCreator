# Tiny dry-run to validate build_locations and soil matching without running big simulations
import json

try:
    from simulation import simulation as tm
except Exception:
    import sys
    sys.path.insert(0, '.')
    from simulation import simulation as tm

# Use a tiny deterministic sample
tm.LOCATION_MODE = 'grid'
tm.GRID_LAT_MIN = 36.0
tm.GRID_LAT_MAX = 36.0
tm.GRID_LON_MIN = 26.0
tm.GRID_LON_MAX = 27.5
tm.GRID_LAT_STEP = 1.0
tm.GRID_LON_STEP = 1.5
vm = 2

print('Building locations (count=', vm, ')...')
locs = tm.build_locations()
print('Built', len(locs), 'locations')

for loc in locs:
    print(json.dumps({
        'location_id': loc.get('location_id'),
        'latitude': loc.get('latitude'),
        'longitude': loc.get('longitude'),
        'soil_file': loc.get('soil_file'),
        'WAV': loc.get('WAV'),
        'elevation': loc.get('elevation'),
    }, ensure_ascii=False))

print('\nDone')
