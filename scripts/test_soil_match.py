"""
Seçilen koordinatlar için SoilGrids → texture → WOFOST dosya eşleme
pipeline'ını uçtan uca test eder.

match_soil() üzerinden çalışır — komşu arama ve fallback zinciri otomatik.

Kullanım:
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

# Karışık koordinatlar: bazısı direkt veri, bazısı komşu aramasını tetikler
LOCATIONS = [
    {"name": "Konya Ovası",         "lat": 37.0,  "lon": 32.0},
    {"name": "Ankara çevresi",      "lat": 39.5,  "lon": 32.5},
    {"name": "Ankara (null pixel)", "lat": 39.93, "lon": 32.85},
    {"name": "Şanlıurfa",           "lat": 37.0,  "lon": 38.5},
    {"name": "Erzincan",            "lat": 39.5,  "lon": 39.5},
]

SEP  = "═" * 68
SEP2 = "─" * 68


def print_result(name, lat, lon, r):
    print(f"\n{SEP}")
    print(f"  {name}  ({lat}°N, {lon}°E)")
    print(SEP)

    if r["sg_sand_pct"] is not None:
        src = "komşu koordinat" if r.get("neighbor_coord") else ("cache" if r["sg_from_cache"] else "API")
        if r.get("neighbor_coord"):
            nc = r["neighbor_coord"]
            src = f"komşu ({nc[0]}, {nc[1]})"
        print(f"  Kaynak      : [{src}]")
        print(f"  Kum (sand)  : {r['sg_sand_pct']:5.1f}%")
        print(f"  Silt        : {r['sg_silt_pct']:5.1f}%")
        print(f"  Kil (clay)  : {r['sg_clay_pct']:5.1f}%")
    else:
        print(f"  Toprak verisi alınamadı → texture=unknown, default dosya kullanıldı")

    print(f"  USDA Texture: {r['texture_class']}")
    print(f"  Hedef AWC   : {r['target_awc']:.3f} cm³/cm³")

    bm = r["best_match"]
    print(f"\n  EN İYİ EŞLEŞME")
    print(SEP2)
    print(f"  Dosya       : {bm['filename']}")
    print(f"  SOLNAM      : {bm.get('SOLNAM', '-')}")
    print(f"  SMW         : {bm.get('SMW', '-'):<8}  (wilting point)")
    print(f"  SMFCF       : {bm.get('SMFCF', '-'):<8}  (field capacity)")
    print(f"  AWC         : {bm.get('AWC', '-'):<8}  (bitkilere yarayışlı su)")
    print(f"  Skor        : {bm['score']:.4f}")

    alts = r.get("alternatives", [])
    if alts:
        print(f"\n  Alternatifler:")
        print(f"  {'#':<4} {'Dosya':<16} {'SOLNAM':<28} {'AWC':>6}  {'Skor':>7}")
        print(f"  {'─'*62}")
        print(f"  {'1':<4} {bm['filename']:<16} {bm.get('SOLNAM',''):<28} {str(bm.get('AWC','-')):>6}  {bm['score']:>7.4f}")
        for i, alt in enumerate(alts, 2):
            print(f"  {i:<4} {alt['filename']:<16} {alt.get('SOLNAM',''):<28} {str(alt.get('AWC','-')):>6}  {alt['score']:>7.4f}")


def main():
    print(f"\nToprak Eşleştirme Testi")
    print(f"Toprak dosyaları : {soil_dir}")
    print(f"Yüklenen dosya   : {len(soil_files)}")
    print(f"Test lokasyonu   : {len(LOCATIONS)}")

    for loc in LOCATIONS:
        result = match_soil(loc["lat"], loc["lon"], soil_files)
        print_result(loc["name"], loc["lat"], loc["lon"], result)

    print(f"\n{SEP}")
    print("  Tamamlandı.")
    print(SEP)


if __name__ == "__main__":
    main()
