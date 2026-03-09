# PCSE Dataset Creator

A comprehensive tool for creating agricultural simulation datasets by integrating weather, soil, and crop management data from various regions. This tool generates datasets for **PCSE (Python Crop Simulation Environment)** simulations.

## 📋 About the Project

This project uses the WOFOST 7.2 (World Food Studies) crop simulation model to:

- 📊 **Multi-crop simulations**: Barley, wheat, maize, rice, and 20+ crop types
- 🌍 **Regional coverage**: Real weather data from Turkish districts via OpenMeteo API
- 🌱 **Comprehensive data integration**: Weather, soil, crop parameters, and agronomic management data
- 📈 **Hourly and daily data**: Detailed time series analysis

## 🚀 Installation

### Required Packages

```bash
pip install pcse pandas matplotlib numpy openmeteo-requests requests-cache retry-requests pyyaml
```

### Python Version

- Python 3.8 or higher

## 📁 Project Structure

```
PcseDatasetCreator/
├── cropTypes/              # Crop parameters (YAML format)
│   ├── crops.yaml          # Main crop definitions
│   ├── wheat.yaml
│   ├── maize.yaml
│   └── ... (23+ crop types)
│
├── agroManagement/         # Agricultural management calendars
│   ├── wheat_calendar.agro
│   ├── maize_calendar.agro
│   └── ... (23+ calendars)
│
├── soilTypes/              # Soil parameters
│   ├── *.new               # Soil type files
│   ├── *.awc               # Available water capacity
│   └── *.sol               # Soil properties
│
├── openmeteo/              # Weather data fetching and processing
│   ├── daily.py            # Daily weather data fetcher
│   ├── hourly.py           # Hourly weather data fetcher
│   ├── pcse_weather_data/  # Processed daily weather data
│   └── hourly_weather_data/# Raw hourly weather data
│
├── pcseData/               # Main simulation scripts
│   ├── theta_final.py      # Main simulation for all crops (recommended)
│   ├── test5.py            # Alternative simulation script
│   ├── beta.py
│   ├── alpha.py
│   └── districs_soil.json  # District-soil type mapping
│
└── dataset_output/         # Output datasets
    └── final_hourly_pcse_dataset_all_crops.csv
```

## 📖 How to Use

### Step 1: Prepare Districts and Soil Configuration

Before fetching weather data, configure your districts and their soil types.

**Edit**: `pcseData/districs_soil.json`

```json
[
  {
    "district": "Izmir, Menemen",
    "latitude": 38.60819,
    "longitude": 27.08609,
    "soilType": "ec3.new"
  },
  {
    "district": "Ankara, Polatlı",
    "latitude": 38.7528,
    "longitude": 33.0038,
    "soilType": "m02.awc"
  }
]
```

**Parameters Explanation**:

- `district`: Region name (format: "Province, District")
- `latitude`: Decimal latitude coordinate (find on Google Maps)
- `longitude`: Decimal longitude coordinate (find on Google Maps)
- `soilType`: Soil type filename from `soilTypes/` directory

**How to find coordinates**:

1. Go to [Google Maps](https://maps.google.com)
2. Right-click on your location
3. Click the coordinates at the top
4. Coordinates appear at the bottom in decimal format

### Step 2: Fetch Weather Data

Download and process weather data from OpenMeteo API for your configured districts.

#### 2a. Daily Weather Data

**Edit**: `openmeteo/daily.py` (Optional - only if customizing)

You can customize the weather parameters fetched:

```python
# Line ~35 - Available weather parameters:
# temperature_2m_mean, precipitation_sum, windspeed_10m_max,
# relative_humidity_2m, et0_fao_evapotranspiration, soil_moisture_0_to_10cm

url_params = {
    "latitude": lat,
    "longitude": lon,
    "start_date": start_date,
    "end_date": end_date,
    "daily": "temperature_2m_mean,precipitation_sum,windspeed_10m_max,relative_humidity_2m,et0_fao_evapotranspiration",
    "timezone": "auto"
}
```

**Run** to fetch daily data:

```bash
cd openmeteo/
python daily.py
```

This creates: `pcse_weather_data/{district}_daily.csv`

#### 2b. Hourly Weather Data

**Edit**: `openmeteo/hourly.py` (Optional - only if customizing)

You can customize hourly parameters:

```python
# Line ~35 - Available hourly parameters:
# temperature_2m, relative_humidity_2m, precipitation, windspeed_10m,
# cloudcover, direct_radiation, sunshine_duration

url_params = {
    "latitude": lat,
    "longitude": lon,
    "start_date": start_date,
    "end_date": end_date,
    "hourly": "temperature_2m,relative_humidity_2m,precipitation,windspeed_10m,direct_radiation",
    "timezone": "auto"
}
```

**Run** to fetch hourly data:

```bash
cd openmeteo/
python hourly.py
```

This creates: `hourly_weather_data/{district}_hourly.csv`

**Note**: Fetching hourly data may take several minutes depending on the date range and number of districts.

### Step 3: Convert Weather Data to PCSE Format

OpenMeteo data needs to be converted to PCSE-compatible format:

```bash
cd openmeteo/
# The conversion is done automatically by daily.py and hourly.py
# Output: pcse_weather_data/ directory
```

### Step 4: Run PCSE Simulations

Run the main simulation for all configured crops and districts:

```bash
cd pcseData/

# Main simulation (All crops × All districts)
python theta_final.py
```

This script:

1. Loads crop parameters from YAML files
2. Reads weather data from CSV files
3. Runs WOFOST 7.2 model for each crop-district combination
4. Merges hourly weather data with simulation outputs
5. Creates `final_hourly_pcse_dataset_all_crops.csv`

**Processing time**: 5-30 minutes depending on crops and districts count

### Step 5: Examine Output

```python
import pandas as pd

df = pd.read_csv('pcseData/final_hourly_pcse_dataset_all_crops.csv')
print(df.head())
print(df.info())
print(df.columns)
```

## 🔧 Customization Guide (Contribution Points)

### 1. **Add New Crop Type**

**Location**: `cropTypes/` directory  
**File type**: `*.yaml`  
**Source**: [WOFOST Crop Parameters](https://github.com/ajwdewit/WOFOST_crop_parameters)

**Steps**:

1. Download or create a new crop YAML file (e.g., `new_crop.yaml`)
2. Place it in `cropTypes/`
3. Create corresponding agro file in `agroManagement/new_crop_calendar.agro`

**Example**:

```yaml
# cropTypes/sunflower.yaml
Version: 1.0.0
Metadata:
  Creator: "Your Name"
  Description: "Sunflower crop parameters"
  Sowing_date: 2024-04-01
  Harvesting_date: 2024-09-15

PARVALDATA:
  EMAXFL: 0.80
  FRNX: 50
  RDRSHM: 0.03
  RUE: 3.9
  # ... add more parameters
```

### 2. **Add New Agronomic Management Calendar**

**Location**: `agroManagement/` directory  
**File type**: `*_calendar.agro` (YAML format)  
**Content**: Sowing date, fertilizer application, harvest date, irrigation schedule

**Steps**:

1. Create a new file named `{crop_name}_calendar.agro`
2. Define planting and management events
3. Ensure `crop_name` matches the crop in `cropTypes/`

**Example**:

```yaml
# agroManagement/sunflower_calendar.agro
version: 1.0
AgroManagement:
  - 2024-01-01:
      CropCalendar:
        crop_name: sunflower
        variety_name: Sunflower_1401
        crop_start_date: 2024-04-01
        crop_end_date: 2024-09-15
        max_duration: 200
      Events:
        - event_signal: sowing
          name: Sowing
          date: 2024-04-01
        - event_signal: harvest
          name: Harvest
          date: 2024-09-15
```

### 3. **Add New Districts and Soil Types**

**Location**: `pcseData/districs_soil.json`  
**How to customize district configuration**:

#### 3a. Finding Regional Coordinates

```json
{
  "district": "Province Name, District Name",
  "latitude": 38.7128,
  "longitude": 27.0866,
  "soilType": "ec3.new"
}
```

**To find coordinates**:

1. Open [Google Maps](https://maps.google.com)
2. Search for your district/city center
3. Right-click on the location
4. Coordinates appear as: `38.7128, -75.0866`
5. Convert to decimal format for JSON

#### 3b. Selecting Soil Types

Available soil types in `soilTypes/`:

- **`.new` files**: Generic soil types (ec1.new, ec2.new, ec3.new, ec4.new, ec5.new, ec6.new, sr1.new, sr2.new, sr3.new, sr4.new)
- **`.awc` files**: Available water capacity files (m01.awc, m02.awc, m03.awc, m04.awc, m05.awc, spg002.awc, spg003.awc, spg004.awc, spg005.awc, spg006.awc, spg007.awc)
- **`.sol` files**: Soil solution files (soil_5.sol)

**Match soil to region**:

```json
[
  {
    "district": "Ankara, Polatlı",
    "latitude": 38.7528,
    "longitude": 33.0038,
    "soilType": "m02.awc" // Medium loam soil
  },
  {
    "district": "Izmir, Menemen",
    "latitude": 38.60819,
    "longitude": 27.08609,
    "soilType": "ec3.new" // Clay loam soil
  }
]
```

#### 3c. Full Example with Multiple Districts

```json
[
  {
    "district": "Ankara, Polatlı",
    "latitude": 38.7528,
    "longitude": 33.0038,
    "soilType": "m02.awc"
  },
  {
    "district": "Bursa, Karacabey",
    "latitude": 40.2081,
    "longitude": 28.4689,
    "soilType": "ec2.new"
  },
  {
    "district": "Konya, Karapınar",
    "latitude": 37.6689,
    "longitude": 33.6378,
    "soilType": "m03.awc"
  }
]
```

### 4. **Add New Soil Type**

**Location**: `soilTypes/` directory  
**File format**: CABO format (`.new`, `.awc`, `.sol` extensions)

**Steps**:

1. Obtain soil parameters from local soil research institutes
2. Convert to WOFOST CABO format
3. Place file in `soilTypes/`
4. Reference in `districs_soil.json`

**Typical CABO format** (WOFOST soil file):

```
SOLNAM=Clay loam soil from Ankara
CRAIRC=0.06  ! Critical soil air content
SMFCF=0.35   ! Soil moisture at field capacity
SM0=0.50     ! Soil porosity
RDMSOL=150   ! Maximum rooting depth
```

### 5. **Customize Weather Data Fetching**

**Location**: `openmeteo/daily.py` and `openmeteo/hourly.py`

#### Available Weather Parameters

**Daily parameters**:

- `temperature_2m_mean` - Mean daily temperature
- `precipitation_sum` - Total precipitation
- `windspeed_10m_max` - Maximum wind speed
- `relative_humidity_2m` - Relative humidity
- `et0_fao_evapotranspiration` - Reference evapotranspiration
- `soil_moisture_0_to_10cm` - Soil moisture

**Hourly parameters**:

- `temperature_2m` - Air temperature
- `relative_humidity_2m` - Relative humidity
- `precipitation` - Precipitation amount
- `windspeed_10m` - Wind speed
- `cloudcover` - Cloud coverage
- `direct_radiation` - Direct solar radiation
- `sunshine_duration` - Hours of sunshine

#### Example: Modify daily.py

```python
# openmeteo/daily.py - Around line 35
url_params = {
    "latitude": lat,
    "longitude": lon,
    "start_date": start_date,
    "end_date": end_date,
    # Add or remove parameters as needed
    "daily": "temperature_2m_mean,precipitation_sum,windspeed_10m_max,relative_humidity_2m,et0_fao_evapotranspiration,soil_moisture_0_to_10cm",
    "timezone": "auto"
}
```

#### Example: Modify hourly.py

```python
# openmeteo/hourly.py - Around line 35
url_params = {
    "latitude": lat,
    "longitude": lon,
    "start_date": start_date,
    "end_date": end_date,
    # Custom hourly parameters
    "hourly": "temperature_2m,relative_humidity_2m,precipitation,windspeed_10m,direct_radiation,sunshine_duration",
    "timezone": "auto"
}
```

### 6. **Customize Simulation Parameters**

**Location**: `pcseData/theta_final.py` (Line ~80)

These parameters control groundwater and water availability:

```python
# WOFOST Site Configuration
custom_site = {
    "WAV": 100,      # Initial available water (mm) - Range: 0-200
    "SMLIM": 0.36,   # Soil moisture limit (-) - Range: 0.0-1.0
    "SSI": 0         # Initial soil moisture fraction (-) - Range: 0.0-1.0
}
```

**Parameter Details**:

| Parameter | Meaning              | Range    | Default | Adjustment                          |
| --------- | -------------------- | -------- | ------- | ----------------------------------- |
| `WAV`     | Available soil water | 0-200 mm | 100     | ⬆️ for wet climate, ⬇️ for dry      |
| `SMLIM`   | Soil moisture limit  | 0.0-1.0  | 0.36    | ⬆️ increases water stress tolerance |
| `SSI`     | Initial soil water   | 0.0-1.0  | 0       | ⬆️ simulates pre-watered soil       |

**Example: Adjust for Dry Region**:

```python
custom_site = {
    "WAV": 60,       # Less available water
    "SMLIM": 0.40,   # Higher moisture limit
    "SSI": 0.3       # Start with some moisture
}
```

**Example: Adjust for Wet Region**:

```python
custom_site = {
    "WAV": 150,      # More available water
    "SMLIM": 0.30,   # Lower moisture limit
    "SSI": 0.6       # Start fully watered
}
```

### 7. **Filter Specific Crops or Districts**

**Location**: `pcseData/theta_final.py`

**To run only specific crops** (Line ~75):

```python
# Filter crops
all_crops_varieties = cropd.get_crops_varieties()
selected_crops = {k: v for k, v in all_crops_varieties.items() if k in ['wheat', 'maize']}

for crop_name, varieties in selected_crops.items():
    # ... simulation runs only for wheat and maize
```

**To run only specific districts** (Line ~100):

```python
# Filter districts
selected_districts = [item for item in districts_data if 'Ankara' in item['district']]

for item in selected_districts:
    # ... simulation runs only for Ankara districts
```

## 📊 Output Dataset

The generated CSV file contains the following columns:

- `DATETIME` - Date and time
- `day` - Simulation day
- `district_name` - District name
- `crop_name` - Crop type
- `variety_name` - Crop variety
- `LAI` - Leaf Area Index
- `TAGP` - Total Harvestable Dry Matter
- `TSUM` - Thermal Time Sum (°C days)
- `Temperature` - Air temperature (°C)
- `Precipitation` - Precipitation (mm)
- `Relative_Humidity` - Relative humidity (%)
- `Windspeed` - Wind speed (m/s)
- `Direct_Radiation` - Solar radiation (J/m²)
- _And other PCSE simulation outputs..._

## 🐛 Troubleshooting

| Error                  | Solution                                                                          |
| ---------------------- | --------------------------------------------------------------------------------- |
| "Crop file not found"  | Check that `{crop_name}.yaml` exists in `cropTypes/` directory                    |
| "Agro file not found"  | Ensure `{crop_name}_calendar.agro` exists in `agroManagement/` directory          |
| "Weather data missing" | Run `python openmeteo/daily.py` and `python openmeteo/hourly.py` first            |
| "District not found"   | Verify district spelling in `districs_soil.json` matches weather data filename    |
| "Soil file not found"  | Check that soil file referenced in `districs_soil.json` exists in `soilTypes/`    |
| Simulation very slow   | Filter to fewer crops/districts for testing: see section 7 in Customization Guide |
| Memory error           | Reduce number of districts or use smaller date ranges                             |

## 📝 License

MIT License - See LICENSE file for details

## 👨‍💻 Libraries Used

- **PCSE**: Python Crop Simulation Environment
- **WOFOST**: World Food Studies crop simulation model (from Wageningen University)
- **Pandas**: Data manipulation and analysis
- **OpenMeteo API**: Free weather data service

## 🔗 Useful Resources

- [PCSE Documentation](https://pcse.readthedocs.io/)
- [WOFOST Crop Parameters GitHub](https://github.com/ajwdewit/WOFOST_crop_parameters)
- [OpenMeteo Weather API](https://open-meteo.com/)
- [WOFOST in Wageningen](https://www.wur.nl/)
- [CABO Soil File Format](https://www.eustafor.eu/)

## 📞 Support & Contributing

**To contribute**:

1. Follow the Customization Guide sections above
2. Test your changes with sample data
3. Submit improvements via pull requests

**For questions**:

- Check the Troubleshooting section
- Review PCSE documentation
- Examine example scripts (`test2.py`, `test3.py`, etc.)

---

**Last Updated**: March 2026  
**Version**: 1.0.0  
**Status**: Active Development
