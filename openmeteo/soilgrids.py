"""
SoilGrids REST API batch wrapper for fetching soil properties.
Uses gantian127/soilgrids REST endpoint for coordinate-based queries.
Cache results locally and return texture class (sand/silt/clay/loam) + derived properties.
"""
import json
import os
import time
import requests
import requests_cache

script_dir = os.path.dirname(os.path.abspath(__file__))
cache_session = requests_cache.CachedSession(
    os.path.join(script_dir, ".soilgrids_cache"), 
    expire_after=-1
)

# SoilGrids REST API endpoint (official, stable)
SOILGRIDS_URL = "https://rest.soilgrids.org/query"

# Soil texture classification (USDA)
def classify_texture(sand_pct, silt_pct, clay_pct):
    """
    Classify soil into USDA texture class using Sand/Silt/Clay percentages.
    Returns: 'sand', 'sandy_loam', 'loam', 'silt_loam', 'clay_loam', 'clay', or 'unknown'
    """
    if sand_pct is None or silt_pct is None or clay_pct is None:
        return "unknown"
    
    # USDA texture triangle logic (simplified)
    s, sl, c = sand_pct, silt_pct, clay_pct
    
    # Clay dominant
    if c >= 40:
        if s >= 40:
            return "clay"  # actually sandy clay or clay
        else:
            return "clay"
    # Silt dominant
    elif sl >= 80 and c < 27:
        return "silt_loam"
    # Sand dominant
    elif s >= 85 and c < 10:
        return "sand"
    # Sandy loam
    elif s >= 50 and s < 85 and c < 27:
        return "sandy_loam"
    # Loam
    elif 23 <= c < 40 and 27 <= sl < 50 and 23 <= s < 52:
        return "loam"
    # Clay loam
    elif c >= 27 and c < 40 and s < 50:
        return "clay_loam"
    else:
        return "loam"  # default


def _request_soilgrids_single(lat, lon, max_attempts=2, wait_seconds=1):
    """
    Single coordinate SoilGrids REST API query.
    Returns dict with 'sand','silt','clay','awc','bdod' (top layer mean).
    Tolerates network issues gracefully.
    """
    url = SOILGRIDS_URL
    params = {
        "lat": float(lat),
        "lon": float(lon),
        "property": ["sand", "silt", "clay", "awc", "bdod"],
        "depth": "0-5cm"  # Use top layer for speed
    }
    
    for attempt in range(1, max_attempts + 1):
        try:
            response = cache_session.get(url, params=params, timeout=15)
            if response.status_code == 429 and attempt < max_attempts:
                # Rate limited, retry after wait
                time.sleep(wait_seconds * attempt)
                continue
            if response.status_code >= 500:
                # Server error, retry
                if attempt < max_attempts:
                    time.sleep(wait_seconds * attempt)
                    continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError as e:
            # Network issue
            if attempt < max_attempts:
                time.sleep(wait_seconds * attempt)
                continue
            return None  # Tolerate and return None (fallback in caller)
        except Exception as e:
            # Other errors
            return None
    return None


def extract_soil_features(sg_response):
    """
    Extract soil properties from SoilGrids JSON response.
    Returns: {'sand':%, 'silt':%, 'clay':%, 'awc':cm/cm, 'bdod':g/cm3, 'texture':'...'}
    If response is None, returns fallback defaults.
    """
    if sg_response is None:
        # Fallback values (median global soil properties)
        return {
            "sand": 35.0,
            "silt": 45.0,
            "clay": 20.0,
            "awc": 0.08,
            "bdod": 1.4,
            "texture": "loam"
        }
    
    features = {}
    props = sg_response.get("properties", {})
    
    for var in ["sand", "silt", "clay", "awc", "bdod"]:
        if var not in props:
            features[var] = None
            continue
        
        var_data = props[var]
        if not isinstance(var_data, dict):
            features[var] = None
            continue
        
        values_dict = var_data.get("values", {})
        means = []
        for depth_key, depth_val in values_dict.items():
            if isinstance(depth_val, dict) and "mean" in depth_val:
                mean_val = depth_val["mean"]
                if mean_val is not None:
                    means.append(mean_val)
        
        if means:
            features[var] = sum(means) / len(means)
        else:
            features[var] = None
    
    # Fill missing values with fallback
    if features.get("sand") is None:
        features["sand"] = 35.0
    if features.get("silt") is None:
        features["silt"] = 45.0
    if features.get("clay") is None:
        features["clay"] = 20.0
    if features.get("awc") is None:
        features["awc"] = 0.08
    if features.get("bdod") is None:
        features["bdod"] = 1.4
    
    # Classify texture
    texture = classify_texture(
        features.get("sand"),
        features.get("silt"),
        features.get("clay")
    )
    features["texture"] = texture
    
    return features


def fetch_batch_soil_features(locations, batch_size=5):
    """
    Batch fetch SoilGrids features for multiple locations.
    locations: list of dicts with 'latitude', 'longitude'
    Returns: list of dicts with soil features aligned to locations.
    Gracefully handles network issues with fallback values.
    """
    if not locations:
        return []
    
    soil_features = []
    failed_count = 0
    
    for idx, loc in enumerate(locations):
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            soil_features.append(None)
            continue
        
        # Query with silent fail (return None, later converted to fallback)
        sg_resp = _request_soilgrids_single(lat, lon)
        features = extract_soil_features(sg_resp)
        
        if sg_resp is None:
            failed_count += 1
        
        soil_features.append(features)
        
        # Print progress every 10
        if (idx + 1) % 10 == 0:
            print(f"  Fetched {idx + 1}/{len(locations)} soil features ({failed_count} used fallback)")
    
    if failed_count > 0:
        print(f"  Note: {failed_count} locations used fallback soil values (network/API issue)")
    
    return soil_features
