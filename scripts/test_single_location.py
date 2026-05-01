"""Single-location dry-run to verify `build_locations` and soil matching.

Run: python3 scripts/dry_run_single_location.py
"""
from __future__ import annotations

import sys
import json

sys.path.insert(0, '.')
from simulation import simulation as tm

# Configure a single deterministic location
tm.LOCATION_MODE = 'grid'
tm.GRID_LAT_MIN = 39.836561
tm.GRID_LAT_MAX = 39.836561
tm.GRID_LON_MIN = 26.475204
tm.GRID_LON_MAX = 26.475204
tm.GRID_LAT_STEP = 1.0
tm.GRID_LON_STEP = 1.0

try:
    locs = tm.build_locations()
except Exception as e:
    print('Error building locations:', e)
    raise

if not locs:
    print('No locations returned')
else:
    loc = locs[0]
    print(json.dumps(loc, indent=2, ensure_ascii=False))
