# PCSE Dataset Creator

Agricultural simulation dataset generator for training machine learning models on crop yield prediction. Combines WOFOST 7.2 crop simulation outputs with real-world weather and soil data to produce hourly time-series datasets.

## What it does

Runs the WOFOST 7.2 (World Food Studies) crop model across a grid of locations in Turkey (36–42°N, 26–45°E), multiple years, and multiple crops. For each combination, it simulates how the crop grows day by day and merges the result with hourly sensor-style weather data. The final dataset is suitable for training models that predict seasonal yield (`harvest_twso`) from weather and crop state observations.

Three water availability scenarios (`kuru/normal/islak`) are simulated per location-crop-year combination to expose the model to varying moisture conditions.

## Installation

```bash
pip install pcse pandas numpy openmeteo-requests requests-cache retry-requests pyyaml tqdm global-land-mask
```

Python 3.10 or higher recommended.

## Project structure

```
PcseDatasetCreator/
│
├── simulation/
│   ├── simulation.py              # Main script — runs the full dataset generation
│   └── simulation_single_year.py # Single-year variant (kept for reference)
│
├── providers/                     # Data fetching modules
│   ├── weather_daily.py           # Fetches daily weather in PCSE format from OpenMeteo
│   ├── weather_hourly.py          # Fetches hourly sensor data from OpenMeteo
│   ├── elevation.py               # Fetches elevation per coordinate
│   ├── soilgrids.py               # Queries SoilGrids API for sand/silt/clay percentages
│   ├── soil_matcher.py            # Matches SoilGrids texture to local WOFOST soil files
│   ├── openlandmap.py             # Fallback soil data source (used internally by soil_matcher)
│   ├── wav_provider.py            # Site parameter helper (used by simulation_single_year)
│   └── weather_data/
│       ├── daily/{year}/*.csv     # Cached daily weather, one file per location per year
│       └── hourly/{year}/*.csv    # Cached hourly weather, one file per location per year
│
├── crops/                         # WOFOST crop parameter files (YAML, 23+ crops)
├── agro/                          # Agromanagement calendars (sowing/harvest dates per crop)
├── soils/                         # WOFOST soil parameter files (CABO format)
│
├── scripts/                       # Testing and validation scripts
│   ├── test_pipeline.py           # Full pipeline dry run (small scale)
│   ├── test_locations.py          # Tests location grid building and soil matching
│   ├── test_single_location.py    # Tests one location end-to-end
│   ├── test_soil_matcher.py       # Tests SoilGrids → WOFOST file matching
│   ├── test_soil_match.py         # Prints detailed soil match report for sample coords
│   ├── test_soilgrids.py          # Tests raw SoilGrids API queries
│   └── soil_site_validation.py    # Validates soil matches across all grid locations
│
└── output/                        # Generated datasets (CSV files, gitignored)
```

## How to run

### Full dataset generation

```bash
python simulation/simulation.py
```

This will:
1. Build a grid of ~71 land points across Turkey
2. Fetch elevation and match each point to a WOFOST soil file via SoilGrids
3. For each year (2014–2024), fetch daily and hourly weather per location
4. Run WOFOST for every crop × location × year × WAV scenario combination
5. Merge hourly weather with daily WOFOST outputs
6. Save yearly CSVs to `output/yearly/` and a merged final CSV to `output/`

**Estimated runtime:** several hours depending on the number of crops and API rate limits.

### Test scripts (run before the full run)

```bash
python scripts/test_pipeline.py       # Recommended first check — 4 crops, 3 locations, 1 year
python scripts/test_locations.py      # Verify grid generation and soil matching
python scripts/test_soil_match.py     # Print soil match details for sample coordinates
```

## Configuration

All key parameters are at the top of `simulation/simulation.py`:

| Parameter | Default | Description |
|---|---|---|
| `LOCATION_MODE` | `"grid"` | `"grid"` or `"random"` |
| `GRID_LAT_MIN/MAX` | `36.0 / 42.0` | Latitude bounds |
| `GRID_LON_MIN/MAX` | `26.0 / 45.0` | Longitude bounds |
| `GRID_LAT_STEP` | `1.0` | Grid step in degrees |
| `GRID_LON_STEP` | `1.5` | Grid step in degrees |
| `RANDOM_LOCATION_COUNT` | `24` | Number of random points (if `LOCATION_MODE="random"`) |
| `years` | `range(2014, 2025)` | Years to simulate |
| `WAV_SCENARIOS` | `kuru=10, normal=50, islak=100` | Initial soil water (cm) |

Crops are loaded automatically from all YAML files in `crops/` that have a matching `.agro` file in `agro/` and a valid variety.

## Output columns

The final dataset contains one row per hour per simulated season.

| Column | Description |
|---|---|
| `DATETIME` | Hourly timestamp |
| `location_id` | Location identifier (e.g. `loc_0001`) |
| `latitude`, `longitude`, `elevation` | Coordinates |
| `soil_file` | WOFOST soil file used for this location |
| `crop_name`, `variety_name` | Crop and variety |
| `year` | Simulation year |
| `wav_scenario` | Water scenario: `kuru`, `normal`, or `islak` |
| `WAV` | Initial available water in cm (10, 50, or 100) |
| `season_id` | Unique identifier for each simulated season |
| `AIR_TEMP`, `AIR_HUMIDITY`, `PRECIP` | Hourly weather observations |
| `SOIL_TEMP_0_7`, `SOIL_MOISTURE_0_7` | Hourly soil observations |
| `DVS` | Development stage (0=emergence, 1=flowering, 2=maturity) |
| `LAI` | Leaf area index |
| `TAGP` | Total above-ground dry matter (kg/ha) |
| `TWSO` | Daily dry weight of storage organs (kg/ha) |
| `harvest_twso` | **ML target** — final yield at harvest (constant per season) |
| `sim_success` | 1 if simulation produced yield > 0, else 0 |

## How soil matching works

For each location, the pipeline:
1. Queries **SoilGrids v2.0** for sand/silt/clay percentages at that coordinate
2. Classifies the texture class (USDA triangle)
3. Scores all 22 local WOFOST soil files by how closely their AWC matches the SoilGrids AWC
4. Falls back to **OpenLandMap** or neighboring pixels if SoilGrids returns null

The local soil files (`soils/`) are CABO-format files used directly by WOFOST. Texture classes are mapped to preferred files via `TEXTURE_PRIORITY` in `providers/soil_matcher.py`.

## Adding a new crop

1. Place a WOFOST YAML file in `crops/` (e.g. `crops/lentil.yaml`)
2. Create an agromanagement calendar in `agro/lentil_calendar.agro`
3. The crop is automatically picked up on the next run

The agro file must use `crop_name: lentil` and reference a variety that exists in the YAML. For winter crops (sown in autumn, harvested next year), ensure `crop_end_date` is in the following year — the pipeline handles multi-year weather data automatically.

## External APIs

| API | Purpose | Auth |
|---|---|---|
| OpenMeteo Archive | Daily + hourly weather | None |
| OpenMeteo Elevation | Elevation per coordinate | None |
| SoilGrids v2.0 REST | Sand/silt/clay per coordinate | None (rate-limited) |
| OpenLandMap | Fallback soil texture | None |

All API responses are cached in SQLite files inside `providers/` (`.cache.sqlite`, `.soilgrids_cache.sqlite`) to avoid repeated fetches on re-runs.

SoilGrids allows roughly 5 requests/minute. The script includes a 13-second sleep between requests. If you see 429 errors, increase the sleep interval in `providers/soilgrids.py`.

## Common issues

| Problem | Fix |
|---|---|
| Crop not found | Check that crop name in `crops/*.yaml` matches the filename and the `.agro` file in `agro/` |
| SoilGrids 429 errors | Increase sleep interval in `providers/soilgrids.py` or rely on the cache |
| Weather data missing | The script fetches it automatically; check API connectivity if it fails |
| Simulation very slow | Reduce `years` range or comment out crops in the valid pairs loop |
| Winter crop TWSO = 0 | Ensure next-year weather is available; the pipeline pre-fetches it automatically |

## License

MIT — see LICENSE file.
