# PCSE Dataset Creator

Agricultural simulation dataset generator for training machine learning models on crop yield prediction. Combines WOFOST 7.2 crop simulation outputs with real-world weather and soil data to produce hourly time-series datasets.

## What it does

Runs the WOFOST 7.2 (World Food Studies) crop model across a grid of locations in Turkey (36–42°N, 26–45°E), multiple years, and multiple crops. For each combination, it simulates how the crop grows day by day and merges the result with hourly sensor-style weather data. The final dataset is suitable for training models that predict seasonal yield (`harvest_twso`) from weather and crop state observations.

Three water availability scenarios (`dry/normal/wet`) are simulated per location-crop-year combination to expose the model to varying moisture conditions.

## Installation

```bash
pip install pcse pandas numpy openmeteo-requests requests-cache retry-requests pyyaml tqdm global-land-mask pyarrow
```

Python 3.10 or higher recommended.

## Project structure

```
PcseDatasetCreator/
│
├── simulation/
│   ├── simulation.py              # Main script — parallel multi-year dataset generation
│   └── simulation_single_year.py # Single-year variant (kept for reference)
│
├── providers/                     # Data fetching modules
│   ├── weather_daily.py           # Fetches daily weather in PCSE format from OpenMeteo (batch)
│   ├── weather_hourly.py          # Fetches hourly sensor data from OpenMeteo (batch)
│   ├── elevation.py               # Fetches elevation per coordinate
│   ├── soilgrids.py               # Queries SoilGrids API for sand/silt/clay percentages
│   ├── soil_matcher.py            # Matches SoilGrids texture to local WOFOST soil files
│   ├── openlandmap.py             # Fallback soil data source (used internally by soil_matcher)
│   ├── wav_provider.py            # Site parameter helper (used by simulation_single_year)
│   └── weather_data/
│       ├── daily/{year}/*.csv     # Cached daily weather, one file per location per year
│       └── hourly/{year}/*.csv    # Cached hourly weather, one file per location per year
│
├── crops/                         # WOFOST crop parameter files (YAML, 25+ crops)
├── agro/                          # Agromanagement calendars (sowing/harvest dates per crop)
├── soils/                         # WOFOST soil parameter files (CABO format)
│
├── scripts/
│   ├── fetch_weather.py           # Pre-fetches all weather data before simulation (resume-safe)
│   ├── merge_dataset.py           # Merges yearly parquet files into a single dataset
│   ├── test_pipeline.py           # Full pipeline dry run (small scale)
│   ├── test_locations.py          # Tests location grid building and soil matching
│   ├── test_single_location.py    # Tests one location end-to-end
│   ├── test_soil_matcher.py       # Tests SoilGrids → WOFOST file matching
│   ├── test_soil_match.py         # Prints detailed soil match report for sample coords
│   ├── test_soilgrids.py          # Tests raw SoilGrids API queries
│   └── soil_site_validation.py    # Validates soil matches across all grid locations
│
└── output/
    ├── yearly/                    # Per-year simulation outputs (parquet)
    ├── progress_multiyear.parquet # Per-combination simulation status
    └── errors_multiyear.parquet   # Simulation errors log
```

## How to run

### Step 1 — Pre-fetch all weather data

Run this once before simulation. It downloads daily and hourly weather for all locations and years (2014–2026). Already-downloaded files are skipped automatically — safe to interrupt and restart.

```bash
python scripts/fetch_weather.py
```

Weather is fetched as **batch requests** (all 71 locations in one API call per year), so the total number of API requests is very low (2 per year × 13 years = 26 requests).

OpenMeteo has a per-hour rate limit on the free tier. If you hit it, wait an hour and re-run — completed files are preserved.

### Step 2 — Run the simulation

```bash
python simulation/simulation.py
```

This will:
1. Build a grid of ~71 land points across Turkey
2. Fetch elevation and match each point to a WOFOST soil file via SoilGrids
3. Run WOFOST for every crop × location × year × WAV scenario combination in **parallel** (4 workers by default)
4. Merge hourly weather with daily WOFOST outputs
5. Save yearly parquet files to `output/yearly/`

**Estimated runtime:** 5–7 hours on an M3 MacBook Air (4 workers). Adjust `PARALLEL_WORKERS` in `simulation.py` to trade speed for thermal headroom.

Already-completed years are skipped automatically — safe to interrupt and resume.

### Step 3 — Merge into a single dataset

Run this on a machine with sufficient RAM (16 GB+ recommended for the full dataset):

```bash
python scripts/merge_dataset.py
```

Produces `output/final_hourly_pcse_dataset_multiyear.parquet` (~5–8 GB).

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
| `PARALLEL_WORKERS` | `4` | Number of parallel year-workers |
| `years` | `range(2014, 2025)` | Years to simulate |
| `WAV_SCENARIOS` | `dry=10, normal=50, wet=100` | Initial soil water (cm) |

Crops are loaded automatically from all YAML files in `crops/` that have a matching `.agro` file in `agro/` and a valid variety.

## Output format

All outputs are saved as **Parquet** files for efficient storage and fast loading.

```python
import pandas as pd

# Load a single year
df = pd.read_parquet("output/yearly/pcse_2020.parquet")

# Load all years lazily
import glob
df = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob("output/yearly/*.parquet"))])
```

### Output columns

The dataset contains one row per hour per simulated season.

| Column | Description |
|---|---|
| `DATETIME` | Hourly timestamp |
| `latitude`, `longitude`, `elevation` | Coordinates |
| `crop_name`, `variety_name` | Crop and variety |
| `year` | Simulation year |
| `wav_scenario` | Water scenario: `dry`, `normal`, or `wet` |
| `WAV` | Initial available water in cm (10, 50, or 100) |
| `AIR_TEMP`, `AIR_HUMIDITY`, `PRECIP` | Hourly weather observations |
| `SOIL_TEMP_0_7`, `SOIL_MOISTURE_0_7` | Hourly soil observations |
| `DVS` | Development stage (0=emergence, 1=flowering, 2=maturity) |
| `LAI` | Leaf area index |
| `TAGP` | Total above-ground dry matter (kg/ha) |
| `TWSO` | Daily dry weight of storage organs (kg/ha) |
| `harvest_twso` | **ML target** — final yield at harvest (constant per season) |
| `sim_success` | 1 if simulation produced yield > 0, else 0 |

### Estimated dataset size

| Scope | Rows | Parquet size |
|---|---|---|
| Single year | ~47M | ~400–600 MB |
| Full dataset (11 years) | ~514M | ~5–8 GB |

## Possible ML tasks

The dataset supports a wide range of supervised, unsupervised, and time-series tasks:

- **Yield prediction** — predict `harvest_twso` from weather and soil features (LightGBM / XGBoost)
- **Next-step prediction** — predict DVS/LAI/TAGP at the next timestep (LSTM / Temporal Fusion Transformer)
- **Early warning** — predict `sim_success` from early-season data
- **Crop recommendation** — classify best crop for a given location and climate
- **Anomaly detection** — identify stress events (drought, frost) from growth curve deviations
- **Yield mapping** — spatial interpolation of predicted yields across Turkey
- **Scenario analysis** — compare dry vs wet vs normal water scenarios

## How soil matching works

For each location, the pipeline:
1. Queries **SoilGrids v2.0** for sand/silt/clay percentages at that coordinate
2. Classifies the texture class (USDA triangle)
3. Scores all local WOFOST soil files by how closely their AWC matches the SoilGrids AWC
4. Falls back to **OpenLandMap** or neighboring pixels if SoilGrids returns null

The local soil files (`soils/`) are CABO-format files used directly by WOFOST.

## Adding a new crop

1. Place a WOFOST YAML file in `crops/` (e.g. `crops/lentil.yaml`)
2. Create an agromanagement calendar in `agro/lentil_calendar.agro`
3. The crop is automatically picked up on the next run

For winter crops (sown in autumn, harvested next year), ensure `crop_end_date` is in the following year — the pipeline handles multi-year weather data automatically.

## External APIs

| API | Purpose | Auth |
|---|---|---|
| OpenMeteo Archive | Daily + hourly weather (batch) | None |
| OpenMeteo Elevation | Elevation per coordinate (batch) | None |
| SoilGrids v2.0 REST | Sand/silt/clay per coordinate | None (rate-limited) |
| OpenLandMap | Fallback soil texture | None |

All API responses are cached in SQLite files inside `providers/` (`.cache.sqlite`, `.soilgrids_cache.sqlite`) to avoid repeated fetches on re-runs.

SoilGrids allows roughly 5 requests/minute. The script includes a 13-second sleep between requests. If you see 429 errors, increase the sleep interval in `providers/soilgrids.py`.

## Common issues

| Problem | Fix |
|---|---|
| Crop not found | Check that crop name in `crops/*.yaml` matches the filename and the `.agro` file in `agro/` |
| SoilGrids 429 errors | Increase sleep interval in `providers/soilgrids.py` or rely on the cache |
| OpenMeteo hourly limit | Wait one hour and re-run `fetch_weather.py` — completed files are preserved |
| Simulation very slow | Reduce `PARALLEL_WORKERS` or narrow the `years` range |
| Winter crop TWSO = 0 | Ensure next-year weather is available; `fetch_weather.py` fetches 2026 for this reason |
| merge_dataset.py OOM | Run on a machine with 16 GB+ RAM, or process years individually |

## License

MIT — see LICENSE file.
