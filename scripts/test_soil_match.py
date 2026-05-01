"""
End-to-end test for the SoilGrids → texture → WOFOST file matching pipeline.

Runs through match_soil() — neighbor search and fallback chain are automatic.

Usage:
    python scripts/test_soil_match.py
"""

import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from providers.soil_matcher import load_soil_files, match_soil

soil_dir = os.path.join(project_root, "soils")
soil_files = load_soil_files(soil_dir)

# Mixed coordinates: some return direct data, some trigger neighbor search
LOCATIONS = [
    {"name": "Konya Plain",         "lat": 37.0,  "lon": 32.0},
    {"name": "Ankara surroundings", "lat": 39.5,  "lon": 32.5},
    {"name": "Ankara (null pixel)", "lat": 39.93, "lon": 32.85},
    {"name": "Sanliurfa",           "lat": 37.0,  "lon": 38.5},
    {"name": "Erzincan",            "lat": 39.5,  "lon": 39.5},
]

SEP  = "═" * 68
SEP2 = "─" * 68


def print_result(name, lat, lon, r):
    print(f"\n{SEP}")
    print(f"  {name}  ({lat}°N, {lon}°E)")
    print(SEP)

    if r["sg_sand_pct"] is not None:
        src = "neighbor" if r.get("neighbor_coord") else ("cache" if r["sg_from_cache"] else "API")
        if r.get("neighbor_coord"):
            nc = r["neighbor_coord"]
            src = f"neighbor ({nc[0]}, {nc[1]})"
        print(f"  Source      : [{src}]")
        print(f"  Sand        : {r['sg_sand_pct']:5.1f}%")
        print(f"  Silt        : {r['sg_silt_pct']:5.1f}%")
        print(f"  Clay        : {r['sg_clay_pct']:5.1f}%")
    else:
        print(f"  No soil data available → texture=unknown, default file used")

    print(f"  USDA Texture: {r['texture_class']}")
    print(f"  Target AWC  : {r['target_awc']:.3f} cm³/cm³")

    bm = r["best_match"]
    print(f"\n  BEST MATCH")
    print(SEP2)
    print(f"  File        : {bm['filename']}")
    print(f"  SOLNAM      : {bm.get('SOLNAM', '-')}")
    print(f"  SMW         : {bm.get('SMW', '-'):<8}  (wilting point)")
    print(f"  SMFCF       : {bm.get('SMFCF', '-'):<8}  (field capacity)")
    print(f"  AWC         : {bm.get('AWC', '-'):<8}  (plant-available water)")
    print(f"  Score       : {bm['score']:.4f}")

    alts = r.get("alternatives", [])
    if alts:
        print(f"\n  Alternatives:")
        print(f"  {'#':<4} {'File':<16} {'SOLNAM':<28} {'AWC':>6}  {'Score':>7}")
        print(f"  {'─'*62}")
        print(f"  {'1':<4} {bm['filename']:<16} {bm.get('SOLNAM',''):<28} {str(bm.get('AWC','-')):>6}  {bm['score']:>7.4f}")
        for i, alt in enumerate(alts, 2):
            print(f"  {i:<4} {alt['filename']:<16} {alt.get('SOLNAM',''):<28} {str(alt.get('AWC','-')):>6}  {alt['score']:>7.4f}")


def main():
    print(f"\nSoil Matching Test")
    print(f"Soil directory : {soil_dir}")
    print(f"Files loaded   : {len(soil_files)}")
    print(f"Test locations : {len(LOCATIONS)}")

    for loc in LOCATIONS:
        result = match_soil(loc["lat"], loc["lon"], soil_files)
        print_result(loc["name"], loc["lat"], loc["lon"], result)

    print(f"\n{SEP}")
    print("  Done.")
    print(SEP)


if __name__ == "__main__":
    main()
