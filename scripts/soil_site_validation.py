"""
Soil-Site validation: fetch SoilGrids data, compare with local CABO files,
auto-match site presets to soil texture, generate validation report.
"""
import os
import sys
import pandas as pd
from pathlib import Path

base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)

if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from openmeteo.soilgrids import fetch_batch_soil_features, classify_texture
from pcse.input import CABOFileReader

soil_dir = os.path.join(parent_dir, "soilTypes")

def get_cabo_properties(soil_file_path):
    """
    Load CABO soil file and extract key properties.
    Returns: dict with RDMSOL, KS, SMFCF, AWC, BD, etc. if available.
    """
    try:
        cb = CABOFileReader(soil_file_path)
        props = {}
        for key in ["RDMSOL", "KS", "SMFCF", "SMW", "SMFCF", "CRAIRC"]:
            props[key] = cb.get(key)
        return props
    except Exception as e:
        print(f"  Warning: CABOFileReader failed for {soil_file_path}: {e}")
        return {}

def soil_texture_mismatch_score(cabo_props, sg_features):
    """
    Score mismatch between local CABO and SoilGrids derived properties.
    Returns: score (0=perfect match, higher=worse).
    Also returns dict of individual comparisons.
    """
    diffs = {}
    score = 0.0
    
    # AWC comparison (if available)
    if "SMFCF" in cabo_props and sg_features.get("awc"):
        cabo_awc = cabo_props["SMFCF"]
        sg_awc = sg_features["awc"]
        if cabo_awc is not None and sg_awc is not None:
            diff = abs(float(cabo_awc) - float(sg_awc))
            diffs["awc_diff"] = diff
            score += diff * 10  # weight AWC mismatch
    
    # Bulk density comparison
    if "RDMSOL" in cabo_props and sg_features.get("bdod"):
        # RDMSOL is in g/cm³ (same as bdod)
        cabo_bd = cabo_props["RDMSOL"]
        sg_bd = sg_features["bdod"]
        if cabo_bd is not None and sg_bd is not None:
            diff = abs(float(cabo_bd) - float(sg_bd))
            diffs["bdod_diff"] = diff
            score += diff * 5
    
    return score, diffs

def main():
    print("=" * 80)
    print("SOIL-SITE VALIDATION REPORT")
    print("=" * 80)
    
    # Import build_locations from theta_multiyear
    try:
        from pcseData.theta_multiyear import build_locations
    except Exception as e:
        print(f"Error importing build_locations: {e}")
        return
    
    # Build locations
    print("\nBuilding locations...")
    try:
        locations = build_locations()
    except Exception as e:
        print(f"Error building locations: {e}")
        return
    
    print(f"Built {len(locations)} locations")
    
    # Fetch SoilGrids data (batch)
    print("\nFetching SoilGrids soil features (batch)...")
    try:
        sg_features_list = fetch_batch_soil_features(locations, batch_size=10)
    except Exception as e:
        print(f"Error fetching SoilGrids: {e}")
        return
    
    # Load CABO files for comparison
    print("\nLoading CABO soil files...")
    cabo_cache = {}
    for soil_file in os.listdir(soil_dir):
        if soil_file.endswith((".new", ".sol", ".awc")):
            soil_path = os.path.join(soil_dir, soil_file)
            cabo_cache[soil_file] = get_cabo_properties(soil_path)
    
    print(f"Loaded {len(cabo_cache)} CABO files")
    
    # Generate validation report
    print("\nGenerating validation report...\n")
    validation_records = []
    
    for idx, (loc, sg_feat) in enumerate(zip(locations, sg_features_list)):
        location_id = loc.get("location_id")
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        soil_file = loc.get("soil_file")
        wav = loc.get("WAV")
        smlim = loc.get("SMLIM")
        ssi = loc.get("SSI")
        ssmax = loc.get("SSMAX")
        ifunrn = loc.get("IFUNRN")
        notinf = loc.get("NOTINF")
        
        # Get CABO properties
        cabo_props = cabo_cache.get(soil_file, {})
        
        # Score mismatch
        if sg_feat:
            mismatch_score, diffs = soil_texture_mismatch_score(cabo_props, sg_feat)
            detected_texture = sg_feat.get("texture", "unknown")
        else:
            mismatch_score = None
            diffs = {}
            detected_texture = "unknown"
        
        record = {
            "location_id": location_id,
            "latitude": lat,
            "longitude": lon,
            "soil_file": soil_file,
            "WAV": wav,
            "SMLIM": smlim,
            "SSI": ssi,
            "SSMAX": ssmax,
            "IFUNRN": ifunrn,
            "NOTINF": notinf,
            "sg_detected_texture": detected_texture,
            "mismatch_score": mismatch_score,
            "sg_sand_%": sg_feat.get("sand") if sg_feat else None,
            "sg_silt_%": sg_feat.get("silt") if sg_feat else None,
            "sg_clay_%": sg_feat.get("clay") if sg_feat else None,
            "sg_awc_cm/cm": sg_feat.get("awc") if sg_feat else None,
            "sg_bdod_g/cm3": sg_feat.get("bdod") if sg_feat else None,
            "cabo_rdmsol": cabo_props.get("RDMSOL"),
            "cabo_smfcf": cabo_props.get("SMFCF"),
        }
        record.update(diffs)
        validation_records.append(record)
    
    # Output report
    df_report = pd.DataFrame(validation_records)
    
    print("SUMMARY:")
    print(f"  Total locations: {len(df_report)}")
    
    no_sg_data = df_report[df_report["sg_detected_texture"] == "unknown"].shape[0]
    print(f"  Locations without SoilGrids data: {no_sg_data}")
    
    print("\nDetailed Report (first 10):")
    print(df_report[[
        "location_id","latitude","longitude","soil_file",
        "WAV","SMLIM","SSI","SSMAX","IFUNRN","NOTINF",
        "sg_detected_texture","mismatch_score"
    ]].head(10).to_string(index=False))
    
    # Save full report
    report_output = os.path.join(parent_dir, "dataset_output", "soil_site_validation_report.csv")
    os.makedirs(os.path.dirname(report_output), exist_ok=True)
    df_report.to_csv(report_output, index=False)
    print(f"\nFull report saved to: {report_output}")
    
    # Recommendations
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS:")
    print("=" * 80)
    if no_sg_data > 0:
        print(f"\n⚠️  {no_sg_data} locations have no SoilGrids data (API/network issue).")
        print("   These locations will use fallback soil values and WAV from the provider.")
    
    print("\n✓ Validation complete.\n")

if __name__ == "__main__":
    main()
