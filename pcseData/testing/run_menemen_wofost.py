"""
WOFOST 7.2 Water-Limited Production Simulation
Location: Menemen district, Izmir Province, Turkey
Crop: Sugar Beet (Sugarbeet_601)
Soil: Medium loam Fluvisol - Gediz alluvial plain

Run fetch_menemen_weather.py first to generate menemen_weather.csv

Requirements:
    pip install pcse pandas matplotlib requests
"""

import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from pcse.models import Wofost72_WLP_FD
from pcse.base import ParameterProvider
from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider
from pcse.input import CABOFileReader
from pcse.input import CSVWeatherDataProvider
from pcse.input import YAMLAgroManagementReader

# ─────────────────────────────────────────────────────────────
# 1. WEATHER
# ─────────────────────────────────────────────────────────────
print("Loading weather data from menemen_weather.csv ...")
wdp = CSVWeatherDataProvider("menemen_weather.csv")
print(f"  Weather available: {wdp.first_date} → {wdp.last_date}")

# ─────────────────────────────────────────────────────────────
# 2. CROP DATA  –  download official WOFOST YAML files locally
# ─────────────────────────────────────────────────────────────
CROP_DIR = "wofost_crop_params"
BASE_URL  = "https://raw.githubusercontent.com/ajwdewit/WOFOST_crop_parameters/master/"
CROP_FILES = ["crops.yaml", "sugarbeet.yaml"]

os.makedirs(CROP_DIR, exist_ok=True)
for fname in CROP_FILES:
    dest = os.path.join(CROP_DIR, fname)
    if not os.path.exists(dest):
        print(f"  Downloading {fname} ...")
        r = requests.get(BASE_URL + fname, timeout=30)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        print(f"  Saved → {dest}")
    else:
        print(f"  Found cached: {dest}")

print("Loading WOFOST crop parameters ...")
crop_data = YAMLCropDataProvider(fpath=CROP_DIR, force_reload=False)
crop_data.set_active_crop("sugarbeet", "Sugarbeet_601")
print("  Crop loaded: sugarbeet / Sugarbeet_601")

# ─────────────────────────────────────────────────────────────
# 3. SOIL DATA
# ─────────────────────────────────────────────────────────────
soil_data = CABOFileReader("ec3.soil")
print("Soil data loaded: ec3.soil")

# ─────────────────────────────────────────────────────────────
# 4. SITE DATA
# ─────────────────────────────────────────────────────────────
site_data = WOFOST72SiteDataProvider(WAV=15.0)

# ─────────────────────────────────────────────────────────────
# 5. AGROMANAGEMENT
# ─────────────────────────────────────────────────────────────
agromanagement = YAMLAgroManagementReader("menemen_sugarbeet_agro.yaml")
print("Agromanagement loaded: menemen_sugarbeet_agro.yaml")

# ─────────────────────────────────────────────────────────────
# 6. ASSEMBLE & RUN
# ─────────────────────────────────────────────────────────────
params = ParameterProvider(
    cropdata=crop_data,
    soildata=soil_data,
    sitedata=site_data
)

wofost = Wofost72_WLP_FD(params, wdp, agromanagement)
print("\nRunning WOFOST simulation...")
wofost.run_till_terminate()

output = wofost.get_output()
df = pd.DataFrame(output)
df["day"] = pd.to_datetime(df["day"])
df.set_index("day", inplace=True)

summary = wofost.get_summary_output()
print("\n=== SIMULATION SUMMARY ===")
print(f"  Total Above Ground Production (TAGP): {summary[0]['TAGP']:>8.0f} kg/ha")
print(f"  Storage Organ Yield       (TWSO):     {summary[0]['TWSO']:>8.0f} kg/ha")
print(f"  Duration:  {df.index[0].date()} → {df.index[-1].date()}  ({len(df)} days)")

# ─────────────────────────────────────────────────────────────
# 7. PLOTS
# ─────────────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 2, figsize=(14, 9))
fig.suptitle(
    "WOFOST 7.2 WLP – Sugar Beet Simulation\nMenemen, İzmir, Turkey (2023)",
    fontsize=13, fontweight="bold"
)

ax = axes[0, 0]
ax.plot(df.index, df["TAGP"], color="#2ecc71", linewidth=2, label="Total Above Ground (TAGP)")
ax.plot(df.index, df["TWSO"], color="#e67e22", linewidth=2, label="Storage Organs / Yield (TWSO)")
ax.set_title("Biomass Accumulation")
ax.set_ylabel("kg/ha")
ax.legend(fontsize=8)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
ax.grid(alpha=0.3)

ax = axes[0, 1]
ax.plot(df.index, df["LAI"], color="#3498db", linewidth=2)
ax.set_title("Leaf Area Index (LAI)")
ax.set_ylabel("m² m⁻²")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
ax.grid(alpha=0.3)

ax = axes[1, 0]
ax.plot(df.index, df["SM"], color="#1abc9c", linewidth=2, label="Soil Moisture (SM)")
ax.axhline(y=0.280, color="#2980b9", linestyle="--", alpha=0.7, label="Field Capacity (0.28)")
ax.axhline(y=0.120, color="#e74c3c", linestyle="--", alpha=0.7, label="Wilting Point (0.12)")
ax.set_title("Soil Moisture")
ax.set_ylabel("cm³/cm³")
ax.legend(fontsize=7)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
ax.grid(alpha=0.3)

ax = axes[1, 1]
ax.plot(df.index, df["TRA"], color="#9b59b6", linewidth=2, label="Actual Transpiration (TRA)")
if "TRAMX" in df.columns:
    ax.plot(df.index, df["TRAMX"], color="#bdc3c7", linestyle="--",
            linewidth=1.5, label="Potential (TRAMX)")
    ax.fill_between(df.index, df["TRA"], df["TRAMX"],
                    alpha=0.2, color="#e74c3c", label="Water stress")
ax.set_title("Transpiration (Water Stress)")
ax.set_ylabel("cm/day")
ax.legend(fontsize=7)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
ax.grid(alpha=0.3)

plt.tight_layout()
plt.savefig("menemen_wofost_results.png", dpi=150, bbox_inches="tight")
plt.show()
print("\nPlot saved: menemen_wofost_results.png")

# ─────────────────────────────────────────────────────────────
# 8. EXPORT
# ─────────────────────────────────────────────────────────────
df.to_csv("menemen_wofost_output.csv")
print("Output saved: menemen_wofost_output.csv")
print("\nKey variable stats:")
print(df[["TAGP", "TWSO", "LAI", "SM", "TRA"]].describe().round(3))